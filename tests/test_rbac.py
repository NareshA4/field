import pytest

from apps.core.exceptions import Conflict, PermissionDenied
from apps.rbac import catalog, services
from apps.rbac.models import MembershipRole, Role
from apps.tenancy import services as tenancy
from apps.tenancy.models import Membership

pytestmark = [pytest.mark.rbac, pytest.mark.django_db]


def test_unauthenticated_gets_401(api):
    r = api.get("/api/v1/members/")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "not_authenticated"


def test_owner_allowed_technician_denied_matrix(as_user, owner_a, tech_a, org_a):
    owner, tech = as_user(owner_a, org_a), as_user(tech_a, org_a)
    for method, url in [("get", "/api/v1/members/"), ("get", "/api/v1/roles/"), ("get", "/api/v1/audit-logs/"),
                        ("get", "/api/v1/organization/"), ("get", "/api/v1/permissions/")]:
        assert getattr(owner, method)(url).status_code == 200, url
        assert getattr(tech, method)(url).status_code == 403, url
    body = {"email": "n@alpha.test", "full_name": "N", "role_ids": []}
    assert tech.post("/api/v1/members/", body, format="json").status_code == 403
    assert tech.patch("/api/v1/organization/", {"name": "Hacked"}, format="json").status_code == 403
    assert tech.post("/api/v1/roles/", {"name": "x"}, format="json").status_code == 403


def test_technician_can_use_own_notifications(as_user, tech_a, org_a):
    assert as_user(tech_a, org_a).get("/api/v1/notifications/").status_code == 200


def test_unmapped_action_is_denied(as_user, owner_a, org_a):
    # roles viewset maps no 'partial_update'; PATCH must be refused even for the Owner
    role = Role.objects.unscoped().filter(organization=org_a, system_key="technician").first()
    assert as_user(owner_a, org_a).patch(f"/api/v1/roles/{role.pk}/", {}, format="json").status_code in (403, 405)


def test_platform_admin_has_no_tenant_permissions(as_user, platform_admin, org_a):
    c = as_user(platform_admin)
    assert c.get("/api/v1/members/").status_code == 403  # no membership, no default
    c.credentials(HTTP_X_ORGANIZATION=org_a.slug)
    r = c.get("/api/v1/members/")
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_a_member"


def test_org_owner_is_not_platform_admin(as_user, owner_a, org_a):
    assert owner_a.is_platform_admin is False
    r = as_user(owner_a, org_a).get("/api/v1/platform/organizations/")
    assert r.status_code == 403
    assert as_user(owner_a, org_a).post("/api/v1/platform/organizations/", {}, format="json").status_code == 403


def test_platform_admin_can_manage_organizations(as_user, platform_admin):
    c = as_user(platform_admin)
    r = c.post("/api/v1/platform/organizations/", {"name": "Gamma Co", "owner_email": "o@gamma.test",
                                                    "owner_name": "Gamma Owner"}, format="json")
    assert r.status_code == 201, r.content
    assert c.get("/api/v1/platform/organizations/").status_code == 200


def test_owner_role_is_locked_and_system_role_undeletable(as_user, owner_a, org_a):
    c = as_user(owner_a, org_a)
    owner_role = Role.objects.unscoped().get(organization=org_a, is_owner=True)
    r = c.put(f"/api/v1/roles/{owner_role.pk}/", {"name": "Owner", "permissions": []}, format="json")
    assert r.status_code == 403 and r.json()["error"]["code"] == "role_locked"
    tech = Role.objects.unscoped().get(organization=org_a, system_key="technician")
    assert c.delete(f"/api/v1/roles/{tech.pk}/").status_code == 403


def test_org_can_define_custom_roles_and_edit_defaults(as_user, owner_a, tech_a, org_a):
    c = as_user(owner_a, org_a)
    r = c.post("/api/v1/roles/", {"name": "Auditor Lite", "permissions": ["audit.view", "user.view"]}, format="json")
    assert r.status_code == 201
    assert sorted(r.json()["permissions"]) == ["audit.view", "user.view"]
    tech_role = Role.objects.unscoped().get(organization=org_a, system_key="technician")
    r = c.put(f"/api/v1/roles/{tech_role.pk}/", {"name": "Technician", "permissions": ["user.view"]}, format="json")
    assert r.status_code == 200
    assert as_user(tech_a, org_a).get("/api/v1/members/").status_code == 200  # newly granted by the org


def test_unknown_permission_rejected(as_user, owner_a, org_a):
    r = as_user(owner_a, org_a).post("/api/v1/roles/", {"name": "Bad", "permissions": ["nope.nope"]}, format="json")
    assert r.status_code == 400


def test_role_in_use_cannot_be_deleted(as_user, owner_a, org_a, make_member):
    c = as_user(owner_a, org_a)
    role = c.post("/api/v1/roles/", {"name": "Temp", "permissions": ["user.view"]}, format="json").json()
    m = make_member(org_a, "t@alpha.test", "technician")
    services.set_membership_roles(m, [Role.objects.unscoped().get(pk=role["id"])], actor=owner_a)
    r = c.delete(f"/api/v1/roles/{role['id']}/")
    assert r.status_code == 409 and r.json()["error"]["code"] == "role_in_use"


def test_last_owner_cannot_be_deactivated_or_demoted(owner_a, org_a, make_member):
    other = make_member(org_a, "admin@alpha.test", "admin")
    owner_m = Membership.objects.unscoped().get(organization=org_a, user=owner_a)
    # another admin (not owner) trying to deactivate the only owner is blocked by the invariant itself
    with pytest.raises(Conflict) as e:
        tenancy.set_member_active(owner_m, active=False, actor=other.user)
    assert e.value.code == "last_owner"
    admin_role = services.system_role_by_key(org_a, "admin")
    with pytest.raises(Conflict):
        services.set_membership_roles(owner_m, [admin_role], actor=owner_a)


def test_only_owner_can_grant_owner_role(owner_a, org_a, make_member):
    admin = make_member(org_a, "admin@alpha.test", "admin")
    target = make_member(org_a, "x@alpha.test", "technician")
    owner_role = services.system_role_by_key(org_a, "owner")
    with pytest.raises(PermissionDenied) as e:
        services.set_membership_roles(target, [owner_role], actor=admin.user)
    assert e.value.code == "owner_only"
    services.set_membership_roles(target, [owner_role], actor=owner_a)
    assert MembershipRole.objects.filter(membership=target, role=owner_role).exists()


def test_cannot_deactivate_self(owner_a, org_a, make_member):
    make_member(org_a, "owner2@alpha.test", "owner")
    m = Membership.objects.unscoped().get(organization=org_a, user=owner_a)
    with pytest.raises(Conflict) as e:
        tenancy.set_member_active(m, active=False, actor=owner_a)
    assert e.value.code == "self_change"


def test_role_sync_is_additive_and_picks_up_new_permissions(org_a):
    catalog.register("test", "widget.view", "View widgets (test)")
    try:
        services.sync_permissions()
        services.seed_system_roles(org_a)
        auditor = Role.objects.unscoped().get(organization=org_a, system_key="auditor")
        assert "widget.view" in set(auditor.role_permissions.values_list("permission__code", flat=True))
        # an org that removed it from a default role keeps it removed only until next sync (additive by design)
    finally:
        catalog._REGISTRY.pop("widget.view", None)


def test_seeded_roles_exist_for_every_template(org_a):
    from apps.rbac.role_templates import TEMPLATES
    keys = set(Role.objects.unscoped().filter(organization=org_a).values_list("system_key", flat=True))
    assert {t.key for t in TEMPLATES} <= keys


def test_inactive_membership_has_no_permissions(org_a, make_member):
    m = make_member(org_a, "s@alpha.test", "admin", active=False)
    assert services.membership_permissions(m) == frozenset()
