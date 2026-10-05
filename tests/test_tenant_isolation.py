import pathlib
import re

import pytest
from django.contrib.auth import get_user_model

from apps.audit.models import AuditLog
from apps.core import tenant
from apps.notifications import services as notif
from apps.notifications.models import Notification
from apps.rbac.models import Role
from apps.tenancy.models import Membership

pytestmark = [pytest.mark.tenant, pytest.mark.django_db]
User = get_user_model()


def test_manager_is_fail_closed_without_tenant(org_a):
    token = tenant.set_current(tenant.NO_TENANT)
    try:
        assert Role.objects.count() == 0
    finally:
        tenant.reset_current(token)


def test_manager_filters_to_active_org(org_a, org_b):
    with tenant.tenant_context(org_a):
        assert Role.objects.count() > 0
        assert set(Role.objects.values_list("organization_id", flat=True)) == {org_a.pk}
    with tenant.tenant_context(org_b):
        assert set(Role.objects.values_list("organization_id", flat=True)) == {org_b.pk}


def test_cross_tenant_write_blocked(org_a, org_b):
    with tenant.tenant_context(org_a):
        with pytest.raises(ValueError, match="Cross-tenant"):
            Role(organization=org_b, name="Sneaky").save()


def test_get_by_pk_from_other_tenant_is_not_found(org_a, org_b):
    foreign = Role.objects.unscoped().filter(organization=org_b).first()
    with tenant.tenant_context(org_a):
        assert not Role.objects.filter(pk=foreign.pk).exists()


def test_api_member_idor(as_user, owner_a, org_a, owner_b, org_b):
    foreign = Membership.objects.unscoped().get(organization=org_b, user=owner_b)
    r = as_user(owner_a, org_a).get(f"/api/v1/members/{foreign.pk}/")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_api_role_idor(as_user, owner_a, org_a, org_b):
    foreign = Role.objects.unscoped().filter(organization=org_b).first()
    assert as_user(owner_a, org_a).get(f"/api/v1/roles/{foreign.pk}/").status_code == 404
    assert as_user(owner_a, org_a).delete(f"/api/v1/roles/{foreign.pk}/").status_code == 404


def test_api_audit_idor_and_listing_scoped(as_user, owner_a, org_a, owner_b, org_b):
    foreign_entry = AuditLog.objects.filter(organization=org_b).first()
    c = as_user(owner_a, org_a)
    assert c.get(f"/api/v1/audit-logs/{foreign_entry.pk}/").status_code == 404
    ids = {e["id"] for e in c.get("/api/v1/audit-logs/").json()["results"]}
    assert str(foreign_entry.pk) not in ids


def test_header_cannot_grant_membership(as_user, owner_a, org_b):
    """X-Organization only SELECTS among the caller's own memberships; it never grants access."""
    r = as_user(owner_a, org_b).get("/api/v1/members/")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "not_a_member"


def test_header_with_org_uuid_also_cannot_grant(as_user, owner_a, org_b):
    c = as_user(owner_a)
    c.credentials(HTTP_X_ORGANIZATION=str(org_b.pk))
    assert c.get("/api/v1/organization/").status_code == 403


def test_suspended_membership_denied(as_user, owner_a, org_a, make_member):
    m = make_member(org_a, "gone@alpha.test", "admin", active=False)
    assert as_user(m.user, org_a).get("/api/v1/members/").status_code == 403


def test_suspended_org_denied(as_user, platform_admin, owner_a, org_a):
    from apps.tenancy import services
    services.set_organization_status(org_a, active=False, actor=platform_admin, reason="non-payment")
    r = as_user(owner_a, org_a).get("/api/v1/members/")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "organization_suspended"


def test_user_in_two_orgs_sees_only_selected(as_user, org_a, org_b, make_member):
    m = make_member(org_a, "multi@x.test", "admin")
    make_member(org_b, "multi@x.test", "admin")
    emails_a = {x["email"] for x in as_user(m.user, org_a).get("/api/v1/members/").json()["results"]}
    emails_b = {x["email"] for x in as_user(m.user, org_b).get("/api/v1/members/").json()["results"]}
    assert "owner@alpha.test" in emails_a and "owner@beta.test" not in emails_a
    assert "owner@beta.test" in emails_b and "owner@alpha.test" not in emails_b


def test_assigning_foreign_role_rejected(as_user, owner_a, org_a, org_b):
    foreign_role = Role.objects.unscoped().get(organization=org_b, system_key="technician")
    r = as_user(owner_a, org_a).post(
        "/api/v1/members/", {"email": "x@alpha.test", "full_name": "X", "role_ids": [str(foreign_role.pk)]},
        format="json")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_roles"


def test_notifications_scoped_to_org_and_recipient(as_user, org_a, org_b, owner_a, tech_a, owner_b):
    notif.notify(org_a, [owner_a, owner_b], title="Hello")  # owner_b is not a member of org A
    assert Notification.objects.unscoped().filter(organization=org_a).count() == 1
    mine = as_user(owner_a, org_a).get("/api/v1/notifications/").json()["results"]
    assert len(mine) == 1
    other = as_user(tech_a, org_a).get("/api/v1/notifications/").json()["results"]
    assert other == []
    nid = mine[0]["id"]
    assert as_user(tech_a, org_a).post(f"/api/v1/notifications/{nid}/read/").status_code == 404


def test_ui_member_detail_idor(client, owner_a, org_a, owner_b, org_b):
    foreign = Membership.objects.unscoped().get(organization=org_b, user=owner_b)
    client.force_login(owner_a)
    assert client.get(f"/app/users/{foreign.pk}/").status_code == 404
    assert client.post(f"/app/users/{foreign.pk}/", {"action": "deactivate"}).status_code == 404


def test_ui_role_idor(client, owner_a, org_a, org_b):
    foreign = Role.objects.unscoped().filter(organization=org_b).first()
    client.force_login(owner_a)
    assert client.get(f"/app/roles/{foreign.pk}/").status_code == 404
    assert client.post(f"/app/roles/{foreign.pk}/", {"action": "delete"}).status_code == 404


def test_session_org_cannot_be_forged(client, owner_a, org_a, org_b):
    client.force_login(owner_a)
    s = client.session
    s["active_org_id"] = str(org_b.pk)
    s.save()
    r = client.get("/app/")
    # not a member of B: falls back to the sole membership (A), never B
    assert r.status_code == 200
    assert org_b.name not in r.content.decode()
    assert client.post("/app/switch-organization/", {"organization": str(org_b.pk)}).status_code == 302
    assert client.session["active_org_id"] == str(org_a.pk)


def test_unscoped_is_only_used_in_trusted_modules():
    """Static guard: `.unscoped(` is a greppable escape hatch limited to the identity<->tenant bridge."""
    allowed = {
        "apps/core/tenant.py", "apps/tenancy/selectors.py", "apps/tenancy/services.py",
        "apps/rbac/services.py", "apps/accounts/services.py", "apps/notifications/services.py",
        "apps/platform_admin/views.py",
    }
    root = pathlib.Path(__file__).resolve().parent.parent / "src"
    offenders = []
    for p in root.rglob("*.py"):
        rel = p.relative_to(root).as_posix()
        if "/migrations/" in rel or rel in allowed:
            continue
        if re.search(r"\.unscoped\(", p.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert offenders == []
