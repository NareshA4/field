import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework import status

from apps.audit.models import AuditLog
from apps.portal.models import PortalAccount
from apps.portal import services as portal_services
from apps.rbac import services as rbac
from apps.tenancy.models import Membership, Organization

pytestmark = pytest.mark.django_db
User = get_user_model()
PASSWORD = "Correct-Horse-Battery-9"


@pytest.fixture
def make_portal_client(db):
    def _make(org, email="client@acme.test", company_name="Acme Corporation", *, is_enabled=True, active=True):
        user = User.objects.filter(email=email).first() or User.objects.create_user(
            email=email, password=PASSWORD, full_name=email.split("@")[0].replace(".", " ").title()
        )
        membership, _ = Membership.objects.unscoped().get_or_create(
            organization=org,
            user=user,
            defaults={
                "status": Membership.Status.ACTIVE if active else Membership.Status.SUSPENDED,
                "job_title": "Requester",
            },
        )
        if not active:
            membership.status = Membership.Status.SUSPENDED
            membership.save(update_fields=["status"])

        role = rbac.system_role_by_key(org, "client_requester")
        rbac.set_membership_roles(membership, [role], actor=None, system=True)

        account, _ = PortalAccount.objects.unscoped().get_or_create(
            organization=org,
            membership=membership,
            defaults={"company_name": company_name, "is_enabled": is_enabled},
        )
        account.is_enabled = is_enabled
        account.save(update_fields=["is_enabled"])
        return user, membership, account

    return _make


@pytest.fixture
def service_manager_a(org_a, make_member):
    return make_member(org_a, "service.mgr@alpha.test", "service_manager").user


def test_authorized_client_web_access(client, org_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.auth@acme.test", "Acme Corporation")
    client.force_login(user)

    r = client.get("/app/portal/")
    assert r.status_code == 200
    html = r.content.decode()
    assert "Acme Corporation" in html
    assert "Authorized Client" in html
    assert "portal.request.create" in html


def test_client_home_redirects_to_portal_dashboard(client, org_a, make_portal_client):
    user, _, _ = make_portal_client(org_a, "client.redirect@acme.test")
    client.force_login(user)

    r = client.get("/app/")
    assert r.status_code == 302
    assert r["Location"] == "/app/portal/"


def test_authorized_client_api_me(as_user, org_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.api@acme.test", "Apex Client Ltd")
    api = as_user(user, org_a)

    r = api.get("/api/v1/portal/me/")
    assert r.status_code == 200
    data = r.json()
    assert data["company_name"] == "Apex Client Ltd"
    assert data["is_enabled"] is True
    assert data["user"]["email"] == "client.api@acme.test"
    assert data["organization"]["slug"] == org_a.slug


def test_unauthenticated_user_denied():
    from django.test import Client
    c = Client()

    r = c.get("/app/portal/")
    assert r.status_code == 302
    assert "/accounts/login/" in r["Location"]

    from rest_framework.test import APIClient
    api = APIClient()
    r_api = api.get("/api/v1/portal/me/")
    assert r_api.status_code == 401


def test_internal_staff_denied_portal_access(client, as_user, org_a, tech_a):
    # Technician does not have client portal role or account
    client.force_login(tech_a)
    r = client.get("/app/portal/")
    assert r.status_code == 403

    api = as_user(tech_a, org_a)
    r_api = api.get("/api/v1/portal/me/")
    assert r_api.status_code == 403


def test_disabled_portal_account_denied(client, as_user, org_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.disabled@acme.test", is_enabled=False)
    client.force_login(user)

    r = client.get("/app/portal/")
    assert r.status_code == 403

    api = as_user(user, org_a)
    r_api = api.get("/api/v1/portal/me/")
    assert r_api.status_code == 403


def test_suspended_membership_denied(client, as_user, org_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.suspended@acme.test", active=False)
    client.force_login(user)

    r = client.get("/app/portal/")
    assert r.status_code in (302, 403) and r.status_code != 200

    api = as_user(user, org_a)
    r_api = api.get("/api/v1/portal/me/")
    assert r_api.status_code == 403


def test_missing_portal_account_denied(client, as_user, org_a, make_member):
    # Member with client_requester role but no PortalAccount row
    mem = make_member(org_a, "client.noaccount@acme.test", "client_requester")
    client.force_login(mem.user)

    r = client.get("/app/portal/")
    assert r.status_code == 403

    api = as_user(mem.user, org_a)
    r_api = api.get("/api/v1/portal/me/")
    assert r_api.status_code == 403


def test_cross_organization_isolation(client, as_user, org_a, org_b, make_portal_client):
    # Client in Org B
    user_b, mem_b, account_b = make_portal_client(org_b, "client.orgb@acme.test", "Beta Client")

    # API request to Org A header with user B
    api = as_user(user_b, org_a)
    r = api.get("/api/v1/portal/me/")
    assert r.status_code == 403

    # Cross-tenant model write blocked
    with pytest.raises(ValueError, match="Cross-tenant write blocked"):
        PortalAccount.objects.create(
            organization=org_a,
            membership=mem_b,
            company_name="Malicious Cross Org",
            is_enabled=True,
        )


def test_staff_portal_accounts_management(client, org_a, service_manager_a, tech_a, make_portal_client):
    user, mem, account = make_portal_client(org_a, "client.toggle@acme.test", "Acme Toggler")

    # Technician cannot view staff accounts page
    client.force_login(tech_a)
    assert client.get("/app/portal/accounts/").status_code == 403

    # Client cannot view staff accounts page
    client.force_login(user)
    assert client.get("/app/portal/accounts/").status_code == 403

    # Service Manager can view staff accounts page
    client.force_login(service_manager_a)
    r = client.get("/app/portal/accounts/")
    assert r.status_code == 200
    assert "Acme Toggler" in r.content.decode()

    # Service Manager can toggle account to disabled
    assert account.is_enabled is True
    r_post = client.post(f"/app/portal/accounts/{account.pk}/toggle/")
    assert r_post.status_code == 302
    account.refresh_from_db()
    assert account.is_enabled is False

    # Audit log was created
    audit_entry = AuditLog.objects.filter(
        organization=org_a,
        action="portal.account_disabled",
        target_id=str(account.pk),
    ).first()
    assert audit_entry is not None

    # Service Manager can toggle account back to enabled
    r_post2 = client.post(f"/app/portal/accounts/{account.pk}/toggle/")
    assert r_post2.status_code == 302
    account.refresh_from_db()
    assert account.is_enabled is True

    audit_entry2 = AuditLog.objects.filter(
        organization=org_a,
        action="portal.account_enabled",
        target_id=str(account.pk),
    ).first()
    assert audit_entry2 is not None


def test_portal_accounts_api_viewset(as_user, org_a, service_manager_a, tech_a, make_portal_client):
    _, mem, account = make_portal_client(org_a, "client.apivs@acme.test", "API Test Co")

    # Technician forbidden
    api_tech = as_user(tech_a, org_a)
    assert api_tech.get("/api/v1/portal-accounts/").status_code == 403

    # Service Manager authorized
    api_sm = as_user(service_manager_a, org_a)
    r = api_sm.get("/api/v1/portal-accounts/")
    assert r.status_code == 200
    results = r.json().get("results", r.json())
    assert any(item["company_name"] == "API Test Co" for item in results)

    # Toggle action
    r_toggle = api_sm.post(f"/api/v1/portal-accounts/{account.pk}/toggle/")
    assert r_toggle.status_code == 200
    assert r_toggle.json()["is_enabled"] is False


def test_seed_user_roles_command_idempotency(db):
    from apps.rbac.models import MembershipRole

    call_command("seed_user_roles")

    org = Organization.objects.get(slug="apex-industrial-technologies")
    client_user = User.objects.get(email="client.requester@acmecorp.example.com")
    membership = Membership.objects.unscoped().get(organization=org, user=client_user)

    assert membership.status == "ACTIVE"
    roles = [mr.role for mr in MembershipRole.objects.filter(membership=membership).select_related("role")]
    assert any(r.system_key == "client_requester" for r in roles)

    account = PortalAccount.objects.unscoped().get(organization=org, membership=membership)
    assert account.company_name == "Acme Corporation"
    assert account.is_enabled is True

    # Re-running seeder must be idempotent and not create duplicate records
    call_command("seed_user_roles")
    assert PortalAccount.objects.unscoped().filter(organization=org, membership=membership).count() == 1
