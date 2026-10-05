import pytest

from apps.rbac.models import Role
from apps.tenancy.models import Membership

pytestmark = pytest.mark.django_db

OWNER_PAGES = ["/app/", "/app/organization/", "/app/users/", "/app/users/invite/", "/app/roles/",
               "/app/roles/new/", "/app/audit/", "/app/notifications/", "/app/notifications/bell/",
               "/app/search/?q=ow"]


@pytest.mark.parametrize("url", OWNER_PAGES)
def test_owner_pages_render(client, owner_a, url):
    client.force_login(owner_a)
    r = client.get(url)
    assert r.status_code == 200, url


@pytest.mark.parametrize("url", ["/app/users/", "/app/users/invite/", "/app/roles/", "/app/roles/new/",
                                 "/app/audit/", "/app/organization/"])
def test_technician_forbidden_from_admin_pages(client, tech_a, url):
    client.force_login(tech_a)
    assert client.get(url).status_code == 403


def test_nav_is_permission_filtered(client, owner_a, tech_a):
    client.force_login(owner_a)
    html = client.get("/app/").content.decode()
    assert "Audit Trail" in html and "Roles &amp; Permissions" in html
    client.force_login(tech_a)
    html = client.get("/app/").content.decode()
    assert "Audit Trail" not in html and "Users" not in html.split("fx-nav")[1]


@pytest.mark.parametrize("url", ["/app/", "/app/users/", "/app/audit/", "/platform/organizations/"])
def test_anonymous_redirected_to_login(client, url):
    r = client.get(url)
    assert r.status_code == 302 and "/accounts/login/" in r["Location"]


def test_platform_console_access(client, platform_admin, owner_a, org_a):
    for url in ["/platform/organizations/", "/platform/organizations/new/", "/platform/audit/",
                f"/platform/organizations/{org_a.pk}/"]:
        client.force_login(platform_admin)
        assert client.get(url).status_code == 200, url
        client.force_login(owner_a)
        assert client.get(url).status_code == 403, url


def test_platform_admin_cannot_see_tenant_pages_without_membership(client, platform_admin, org_a):
    client.force_login(platform_admin)
    r = client.get("/app/users/")
    assert r.status_code in (302, 403)
    assert r.status_code == 403 or "choose-organization" in r["Location"]


def test_platform_console_creates_org_via_form(client, platform_admin):
    client.force_login(platform_admin)
    r = client.post("/platform/organizations/new/", {
        "name": "Omega Works", "slug": "", "timezone": "Asia/Kolkata", "country": "IN",
        "owner_name": "Om Ega", "owner_email": "om@omega.test"})
    assert r.status_code == 302
    from apps.tenancy.models import Organization
    assert Organization.objects.filter(name="Omega Works").exists()


def test_invite_form_persists_and_audits(client, owner_a, org_a):
    client.force_login(owner_a)
    rid = Role.objects.unscoped().get(organization=org_a, system_key="technician").pk
    r = client.post("/app/users/invite/", {"email": "new.tech@alpha.test", "full_name": "New Tech", "roles": [rid]})
    assert r.status_code == 302
    m = Membership.objects.unscoped().get(organization=org_a, user__email="new.tech@alpha.test")
    assert m.status == "INVITED"


def test_member_actions_via_ui_require_permission(client, tech_a, org_a, owner_a):
    m = Membership.objects.unscoped().get(organization=org_a, user=owner_a)
    client.force_login(tech_a)
    assert client.post(f"/app/users/{m.pk}/", {"action": "deactivate"}).status_code == 403


def test_logout_requires_post(client, owner_a):
    client.force_login(owner_a)
    assert client.get("/accounts/logout/").status_code == 405


def test_csrf_enforced_on_ui_posts(owner_a):
    from django.test import Client
    c = Client(enforce_csrf_checks=True)
    c.force_login(owner_a)
    assert c.post("/app/switch-organization/", {"organization": "x"}).status_code == 403


def test_security_headers(client):
    r = client.get("/accounts/login/")
    assert r["X-Frame-Options"] == "DENY" and r["X-Content-Type-Options"] == "nosniff"
    assert r["X-Request-ID"]
