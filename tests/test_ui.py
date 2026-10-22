import pytest

from apps.rbac.models import Role
from apps.tenancy.models import Membership

pytestmark = pytest.mark.django_db

OWNER_PAGES = [
    "/app/", "/app/organization/", "/app/users/", "/app/users/invite/", "/app/roles/",
    "/app/roles/new/", "/app/audit/", "/app/notifications/", "/app/notifications/bell/",
    "/app/search/?q=ow"
]

PUBLIC_PAGES = [
    "/",
    "/features/",
    "/solutions/",
    "/solutions/enterprise-operations/",
    "/modules/",
    "/modules/FON-M01/",
    "/resources/",
    "/about/",
    "/legal/privacy-policy/",
    "/legal/terms-and-conditions/",
]


@pytest.mark.parametrize("url", PUBLIC_PAGES)
def test_public_pages_render_for_anonymous_visitors(client, url):
    r = client.get(url)
    assert r.status_code == 200, f"Public page failed: {url}"
    assert "FieldOps Nexus" in r.content.decode()


def test_authenticated_user_redirected_from_root(client, owner_a):
    client.force_login(owner_a)
    r = client.get("/")
    assert r.status_code == 302
    assert r["Location"] == "/app/"


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


@pytest.mark.parametrize("url", ["/app/", "/app/users/", "/app/audit/"])
def test_anonymous_redirected_to_login(client, url):
    r = client.get(url)
    assert r.status_code == 302 and "/accounts/login/" in r["Location"]


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


def test_login_page_renders_with_branded_identity(client):
    r = client.get("/accounts/login/")
    assert r.status_code == 200
    html = r.content.decode()
    assert "FieldOps Nexus" in html
    assert "Sign in with your enterprise credentials" in html
    assert "Client Onboarding" in html or "Requester" in html


def test_client_registration_flow(client):
    from apps.audit.models import AuditLog

    # 1. GET page
    r = client.get("/register/")
    assert r.status_code == 200
    assert "Client Onboarding" in r.content.decode()

    # 2. Invalid POST (missing required fields)
    r_bad = client.post("/register/", {"full_name": "Incomplete Client"})
    assert r_bad.status_code == 200
    assert "This field is required" in r_bad.content.decode()

    # 3. Valid POST (onboarding request)
    data = {
        "full_name": "Test Client",
        "email": "requester@testclient.test",
        "phone": "+1 555 123 4567",
        "company_name": "Test Client Corp",
        "facility_address": "400 Industrial Parkway",
        "service_scope": "HVAC inspection and chiller maintenance",
        "terms_accepted": True,
    }
    r_ok = client.post("/register/", data)
    assert r_ok.status_code == 200
    html = r_ok.content.decode()
    assert "Onboarding Request Received" in html
    assert "Test Client Corp" in html

    # Verify audit record
    assert AuditLog.objects.filter(action="portal.client_onboarding_requested", actor_email="requester@testclient.test").exists()


def test_home_dashboard_renders_enhanced_kpi_cards(client, owner_a):
    client.force_login(owner_a)
    r = client.get("/app/")
    assert r.status_code == 200
    html = r.content.decode()
    assert "Active Users" in html
    assert "Pending Invitations" in html
    assert "RBAC Roles" in html
    assert "Operational Shortcuts" in html
