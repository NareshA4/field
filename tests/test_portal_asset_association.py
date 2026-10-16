import uuid
import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.portal.forms import PortalServiceRequestForm
from apps.portal.models import PortalAccount, PortalAssetGrant
from apps.portal import services as portal_services
from apps.portal import selectors as portal_selectors
from apps.rbac import services as rbac
from apps.tenancy.models import Membership

pytestmark = pytest.mark.django_db
User = get_user_model()
PASSWORD = "Correct-Horse-Battery-9"


@pytest.fixture
def make_portal_client(db):
    def _make(
        org,
        email="client@acme.test",
        company_name="Acme Corporation",
        *,
        is_enabled=True,
        active=True,
        role_key="client_requester",
    ):
        user = User.objects.filter(email=email).first() or User.objects.create_user(
            email=email,
            password=PASSWORD,
            full_name=email.split("@")[0].replace(".", " ").title(),
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

        if role_key:
            role = rbac.system_role_by_key(org, role_key)
            rbac.set_membership_roles(membership, [role], actor=None, system=True)
        else:
            rbac.set_membership_roles(membership, [], actor=None, system=True)

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
def asset_1(org_a, site_a1, make_asset):
    return make_asset(org_a, site_a1, "CHILLER-01")


@pytest.fixture
def asset_2(org_a, site_a1, make_asset):
    return make_asset(org_a, site_a1, "PUMP-02")


@pytest.fixture
def asset_foreign(org_b, site_b1, make_asset):
    return make_asset(org_b, site_b1, "BOILER-01")


# 1. Client lists only explicitly granted assets via API
def test_client_lists_only_granted_assets_api(as_user, org_a, asset_1, asset_2, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.grants@acme.test")
    # Grant only asset_1
    portal_services.grant_portal_asset(account, asset_1)

    api = as_user(user, org_a)
    res = api.get("/api/v1/portal/assets/")
    assert res.status_code == status.HTTP_200_OK

    data = res.json()
    assert data["count"] == 1
    tags = [a["asset_tag"] for a in data["results"]]
    assert asset_1.asset_tag in tags
    assert asset_2.asset_tag not in tags

    item = data["results"][0]
    assert item["id"] == str(asset_1.id)
    assert item["asset_tag"] == asset_1.asset_tag
    assert item["name"] == asset_1.name
    assert item["site_id"] == str(asset_1.site_id)
    assert item["site_code"] == asset_1.site.code


# 2. Client without grants sees empty asset list (No grant = no access)
def test_client_without_grants_sees_empty_asset_list(as_user, org_a, asset_1, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.nogrants@acme.test")

    api = as_user(user, org_a)
    res = api.get("/api/v1/portal/assets/")
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["count"] == 0
    assert len(res.json()["results"]) == 0


# 3. Two clients in the same organization cannot see each other's ungranted assets
def test_two_clients_same_org_asset_isolation(as_user, org_a, asset_1, asset_2, make_portal_client):
    user_a, mem_a, acc_a = make_portal_client(org_a, "client.a@acme.test")
    user_b, mem_b, acc_b = make_portal_client(org_a, "client.b@acme.test")

    # Grant asset_1 to client A, asset_2 to client B
    portal_services.grant_portal_asset(acc_a, asset_1)
    portal_services.grant_portal_asset(acc_b, asset_2)

    api_a = as_user(user_a, org_a)
    res_a = api_a.get("/api/v1/portal/assets/")
    tags_a = [a["asset_tag"] for a in res_a.json()["results"]]
    assert asset_1.asset_tag in tags_a
    assert asset_2.asset_tag not in tags_a

    api_b = as_user(user_b, org_a)
    res_b = api_b.get("/api/v1/portal/assets/")
    tags_b = [a["asset_tag"] for a in res_b.json()["results"]]
    assert asset_2.asset_tag in tags_b
    assert asset_1.asset_tag not in tags_b


# 4. Creating request with granted asset succeeds
def test_request_creation_with_granted_asset_succeeds(as_user, org_a, asset_1, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.create.ok@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    api = as_user(user, org_a)
    payload = {
        "title": "Chiller maintenance needed",
        "asset": str(asset_1.pk),
        "severity": "MEDIUM",
    }
    res = api.post("/api/v1/portal/requests/", payload, format="json")
    assert res.status_code == status.HTTP_201_CREATED
    assert res.json()["asset_tag"] == asset_1.asset_tag


# 5. Creating request with ungranted asset in same org is rejected without disclosing existence
def test_request_creation_with_ungranted_asset_rejected(as_user, org_a, asset_1, asset_2, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.create.ungranted@acme.test")
    # Grant ONLY asset_1
    portal_services.grant_portal_asset(account, asset_1)

    api = as_user(user, org_a)
    payload = {
        "title": "Unauthorized attempt on ungranted asset",
        "asset": str(asset_2.pk),
        "severity": "HIGH",
    }
    res = api.post("/api/v1/portal/requests/", payload, format="json")
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "asset" in res.data
    assert "not found or not accessible" in str(res.data).lower()


# 6. Creating request with cross-tenant asset is rejected
def test_request_creation_with_cross_org_asset_rejected(as_user, org_a, asset_foreign, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.create.cross@acme.test")

    api = as_user(user, org_a)
    payload = {
        "title": "Foreign asset request",
        "asset": str(asset_foreign.pk),
    }
    res = api.post("/api/v1/portal/requests/", payload, format="json")
    assert res.status_code == status.HTTP_400_BAD_REQUEST


# 7. Retired or disposed assets excluded from granted assets list
def test_retired_or_disposed_assets_excluded(as_user, org_a, asset_1, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.retired@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    api = as_user(user, org_a)
    res = api.get("/api/v1/portal/assets/")
    assert res.json()["count"] == 1

    # Retire asset
    asset_1.status = "RETIRED"
    asset_1.save(update_fields=["status"])

    res2 = api.get("/api/v1/portal/assets/")
    assert res2.json()["count"] == 0


# 8. Cross-organization grant blocked at model level
def test_cross_organization_grant_blocked(org_a, org_b, asset_foreign, make_portal_client):
    _, _, account_a = make_portal_client(org_a, "client.grant.cross@acme.test")

    with pytest.raises((ValueError, Exception)):
        portal_services.grant_portal_asset(account_a, asset_foreign)


# 9. Staff grant and revoke asset access with audit trail
def test_staff_grant_and_revoke_asset_with_audit(org_a, asset_1, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.auditgrant@acme.test")

    # Grant
    grant = portal_services.grant_portal_asset(account, asset_1, actor=user)
    assert PortalAssetGrant.objects.filter(portal_account=account, asset=asset_1).exists()

    audit_grant = AuditLog.objects.filter(organization=org_a, action="portal.asset_granted").first()
    assert audit_grant is not None
    assert audit_grant.after["asset_tag"] == asset_1.asset_tag

    # Revoke
    revoked = portal_services.revoke_portal_asset(account, asset_1, actor=user)
    assert revoked is True
    assert not PortalAssetGrant.objects.filter(portal_account=account, asset=asset_1).exists()

    audit_revoke = AuditLog.objects.filter(organization=org_a, action="portal.asset_revoked").first()
    assert audit_revoke is not None
    assert audit_revoke.before["asset_tag"] == asset_1.asset_tag


# 10. Form queryset contains only granted assets
def test_portal_request_create_form_restricts_assets(org_a, asset_1, asset_2, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.form@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    form = PortalServiceRequestForm(organization=org_a, membership=membership)
    choices = list(form.fields["asset"].queryset)
    assert asset_1 in choices
    assert asset_2 not in choices


# 11. Unauthenticated or unauthorized asset listing denied
def test_unauthenticated_and_unauthorized_asset_listing_denied(client, as_user, org_a, make_portal_client):
    # Unauthenticated
    api = APIClient()
    res_anon = api.get("/api/v1/portal/assets/")
    assert res_anon.status_code == status.HTTP_401_UNAUTHORIZED

    # Without portal.request.view
    user_noperm, mem_noperm, _ = make_portal_client(org_a, "client.noperm@acme.test", role_key=None)
    api_noperm = as_user(user_noperm, org_a)
    res_noperm = api_noperm.get("/api/v1/portal/assets/")
    assert res_noperm.status_code == status.HTTP_403_FORBIDDEN

    # Disabled portal account
    user_dis, _, _ = make_portal_client(org_a, "client.dis@acme.test", is_enabled=False)
    api_dis = as_user(user_dis, org_a)
    res_dis = api_dis.get("/api/v1/portal/assets/")
    assert res_dis.status_code == status.HTTP_403_FORBIDDEN
