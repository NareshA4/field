"""Every state-changing UI control must hit a real backend action that persists, audits and enforces rules."""
import pytest
from django.core import mail

from apps.audit.models import AuditLog
from apps.notifications import services as notif
from apps.notifications.models import Notification
from apps.rbac.models import MembershipRole, Role
from apps.tenancy.models import Membership, Organization

pytestmark = pytest.mark.django_db


def _role(org, key):
    return Role.objects.unscoped().get(organization=org, system_key=key)


def test_role_create_edit_delete_via_ui(client, owner_a, org_a):
    client.force_login(owner_a)
    r = client.post("/app/roles/new/", {"name": "Field Lead", "description": "d",
                                        "permissions": ["user.view", "audit.view"]})
    assert r.status_code == 302
    role = Role.objects.unscoped().get(organization=org_a, name="Field Lead")
    assert set(role.role_permissions.values_list("permission__code", flat=True)) == {"user.view", "audit.view"}
    assert client.get(f"/app/roles/{role.pk}/").status_code == 200

    client.post(f"/app/roles/{role.pk}/", {"name": "Field Lead", "description": "x", "permissions": ["user.view"]})
    assert set(role.role_permissions.values_list("permission__code", flat=True)) == {"user.view"}
    assert AuditLog.objects.filter(organization=org_a, action="role.updated").exists()

    client.post(f"/app/roles/{role.pk}/", {"action": "delete"})
    assert not Role.objects.unscoped().filter(pk=role.pk).exists()
    assert AuditLog.objects.filter(organization=org_a, action="role.deleted").exists()


def test_role_duplicate_name_shows_error(client, owner_a, org_a):
    client.force_login(owner_a)
    r = client.post("/app/roles/new/", {"name": "technician", "description": "", "permissions": []})
    assert r.status_code == 200 and "already exists" in r.content.decode()


def test_role_ui_requires_manage_permission(client, org_a, make_member):
    viewer = make_member(org_a, "viewer@alpha.test", "auditor").user  # has role.view, not role.manage
    client.force_login(viewer)
    assert client.get("/app/roles/").status_code == 200
    assert client.get("/app/roles/new/").status_code == 403
    role = _role(org_a, "technician")
    assert client.post(f"/app/roles/{role.pk}/", {"action": "delete"}).status_code == 403


def test_member_update_roles_via_ui(client, owner_a, org_a, tech_a):
    m = Membership.objects.unscoped().get(organization=org_a, user=tech_a)
    client.force_login(owner_a)
    r = client.post(f"/app/users/{m.pk}/", {"action": "update", "full_name": "Tess Tech", "job_title": "Lead"})
    assert r.status_code == 302
    r = client.post(f"/app/users/{m.pk}/", {"action": "assign_role", "role": _role(org_a, "supervisor").pk})
    assert r.status_code == 302
    tech_assignment = MembershipRole.objects.get(membership=m, role__system_key="technician")
    r = client.post(f"/app/users/{m.pk}/", {"action": "remove_role", "assignment_id": tech_assignment.pk})
    assert r.status_code == 302
    m.refresh_from_db()
    tech_a.refresh_from_db()
    assert tech_a.full_name == "Tess Tech" and m.job_title == "Lead"
    assert list(MembershipRole.objects.filter(membership=m).values_list("role__system_key", flat=True)) == ["supervisor"]
    assert AuditLog.objects.filter(organization=org_a, action="membership.roles_changed").exists()


def test_member_deactivate_blocks_access_then_reactivate(client, owner_a, org_a, tech_a):
    m = Membership.objects.unscoped().get(organization=org_a, user=tech_a)
    client.force_login(owner_a)
    client.post(f"/app/users/{m.pk}/", {"action": "deactivate"})
    m.refresh_from_db()
    assert m.status == "SUSPENDED"
    from django.test import Client
    t = Client()
    t.force_login(tech_a)
    assert t.get("/app/").status_code in (302, 403)  # no active membership -> no tenant access
    assert t.get("/app/users/").status_code in (302, 403)
    client.post(f"/app/users/{m.pk}/", {"action": "reactivate"})
    m.refresh_from_db()
    assert m.status == "ACTIVE"
    assert {"user.deactivated", "user.reactivated"} <= set(
        AuditLog.objects.filter(organization=org_a).values_list("action", flat=True))


def test_owner_cannot_deactivate_self_via_ui(client, owner_a, org_a):
    m = Membership.objects.unscoped().get(organization=org_a, user=owner_a)
    client.force_login(owner_a)
    client.post(f"/app/users/{m.pk}/", {"action": "deactivate"})
    m.refresh_from_db()
    assert m.status == "ACTIVE"


def test_resend_invitation_sends_email(client, owner_a, org_a, django_capture_on_commit_callbacks):
    client.force_login(owner_a)
    with django_capture_on_commit_callbacks(execute=True):
        client.post("/app/users/invite/", {"email": "pending@alpha.test", "full_name": "Pen Ding",
                                           "roles": [_role(org_a, "technician").pk]})
    m = Membership.objects.unscoped().get(organization=org_a, user__email="pending@alpha.test")
    assert len(mail.outbox) == 1
    with django_capture_on_commit_callbacks(execute=True):
        client.post(f"/app/users/{m.pk}/", {"action": "resend"})
    assert len(mail.outbox) == 2 and "/accounts/activate/" in mail.outbox[1].body


def test_organization_profile_edit_via_ui(client, owner_a, org_a):
    client.force_login(owner_a)
    r = client.post("/app/organization/", {"name": "Alpha Renamed", "legal_name": "Alpha Pvt Ltd",
                                           "timezone": "Asia/Kolkata", "country": "in", "contact_email": "",
                                           "contact_phone": "", "address": "Plot 1"})
    assert r.status_code == 302
    org = Organization.objects.get(pk=org_a.pk)
    assert org.name == "Alpha Renamed" and org.timezone == "Asia/Kolkata"
    bad = client.post("/app/organization/", {"name": "Alpha Renamed", "timezone": "Mars/Base"})
    assert bad.status_code == 200 and "Unknown timezone" in bad.content.decode()


def test_organization_profile_read_only_for_viewers(client, org_a, make_member):
    viewer = make_member(org_a, "v@alpha.test", "auditor").user
    client.force_login(viewer)
    assert client.get("/app/organization/").status_code == 200
    assert client.post("/app/organization/", {"name": "Hacked", "timezone": "UTC"}).status_code == 403


def test_platform_suspend_and_activate_via_ui(client, platform_admin, org_a):
    client.force_login(platform_admin)
    client.post(f"/platform/organizations/{org_a.pk}/", {"action": "suspend", "reason": ""})
    assert Organization.objects.get(pk=org_a.pk).status == "ACTIVE"  # reason required
    client.post(f"/platform/organizations/{org_a.pk}/", {"action": "suspend", "reason": "billing"})
    assert Organization.objects.get(pk=org_a.pk).status == "SUSPENDED"
    client.post(f"/platform/organizations/{org_a.pk}/", {"action": "activate"})
    assert Organization.objects.get(pk=org_a.pk).status == "ACTIVE"
    assert {"organization.suspended", "organization.activated"} <= set(
        AuditLog.objects.filter(organization=org_a).values_list("action", flat=True))


def test_platform_actions_forbidden_for_org_owner(client, owner_a, org_a):
    client.force_login(owner_a)
    assert client.post(f"/platform/organizations/{org_a.pk}/", {"action": "suspend", "reason": "x"}).status_code == 403
    assert Organization.objects.get(pk=org_a.pk).status == "ACTIVE"


def test_notifications_ui_mark_read_and_all(client, owner_a, org_a):
    ns = notif.notify(org_a, [owner_a], title="One", link="/app/users/") + notif.notify(org_a, [owner_a], title="Two")
    client.force_login(owner_a)
    assert "One" in client.get("/app/notifications/bell/").content.decode()
    r = client.post(f"/app/notifications/{ns[0].pk}/read/")
    assert r.status_code == 302 and r["Location"] == "/app/users/"
    assert Notification.objects.unscoped().get(pk=ns[0].pk).read_at is not None
    client.post("/app/notifications/read-all/")
    assert not Notification.objects.unscoped().filter(recipient=owner_a, read_at__isnull=True).exists()


def test_notification_open_redirect_blocked(client, owner_a, org_a):
    n = notif.notify(org_a, [owner_a], title="Evil", link="//evil.example/x")[0]
    client.force_login(owner_a)
    r = client.post(f"/app/notifications/{n.pk}/read/")
    assert "evil.example" not in r["Location"]


def test_notification_email_task(org_a, owner_a, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        notif.notify(org_a, [owner_a], title="Escalation", body="Check it", link="/app/", email=True)
    assert mail.outbox and mail.outbox[-1].subject == "[Alpha Industries] Escalation"


def test_global_search_is_scoped_and_permissioned(client, owner_a, tech_a, org_a, org_b, make_member):
    make_member(org_b, "zoe@beta.test", "technician")
    client.force_login(owner_a)
    html = client.get("/app/search/?q=zoe").content.decode()
    assert "zoe@beta.test" not in html  # other tenant
    assert "tech@alpha.test" in client.get("/app/search/?q=tech").content.decode()
    client.force_login(tech_a)
    assert "tech@alpha.test" not in client.get("/app/search/?q=tech").content.decode()  # no user.view


def test_choose_org_and_switch(client, org_a, org_b, make_member):
    m = make_member(org_a, "multi@x.test", "admin")
    make_member(org_b, "multi@x.test", "admin")
    client.force_login(m.user)
    assert client.get("/app/choose-organization/").status_code == 200
    client.post("/app/switch-organization/", {"organization": str(org_b.pk)})
    assert "Beta Utilities" in client.get("/app/").content.decode()
    client.post("/app/switch-organization/", {"organization": str(org_a.pk)})
    assert "Alpha Industries" in client.get("/app/").content.decode()


def test_audit_ui_filters_and_detail(client, owner_a, org_a):
    client.force_login(owner_a)
    html = client.get("/app/audit/?action=organization.").content.decode()
    assert "organization.created" in html
    entry = AuditLog.objects.filter(organization=org_a, action="organization.created").first()
    assert client.get(f"/app/audit/{entry.pk}/").status_code == 200
    assert "organization.created" not in client.get("/app/audit/?q=nomatch-xyz").content.decode().split("<tbody>")[1]
