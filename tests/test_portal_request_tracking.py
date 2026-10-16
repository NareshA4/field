import uuid
import pytest
from django.contrib.auth import get_user_model
from rest_framework import status

from apps.incidents import services as incident_services
from apps.incidents.models import ServiceRequest
from apps.portal.models import PortalAccount
from apps.portal import services as portal_services
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

        # Auto-grant existing assets for org so existing functional tests continue to pass seamlessly
        from apps.assets.models import Asset
        from apps.portal.models import PortalAssetGrant
        for ast in Asset.objects.filter(organization=org).exclude(status__in=["RETIRED", "DISPOSED"]):
            PortalAssetGrant.objects.get_or_create(
                organization=org,
                portal_account=account,
                asset=ast,
            )

        return user, membership, account


    return _make


@pytest.fixture
def asset_a(org_a, site_a1, make_asset):
    return make_asset(org_a, site_a1, "CHILLER-01")


@pytest.fixture
def asset_b(org_b, site_b1, make_asset):
    return make_asset(org_b, site_b1, "BOILER-01")


# 1. Authorized client can view their own requests via API
def test_authorized_client_views_own_requests_list(as_user, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.viewer@acme.test")
    api = as_user(user, org_a)

    sr1 = portal_services.create_portal_service_request(
        membership,
        asset=asset_a,
        title="First Request for HVAC Inspection",
        description="Routine filter change needed",
        severity="LOW",
    )
    sr2 = portal_services.create_portal_service_request(
        membership,
        asset=asset_a,
        title="Second Request for Bearing Noise",
        description="Loud grinding sound",
        severity="HIGH",
    )

    res = api.get("/api/v1/portal/requests/")
    assert res.status_code == status.HTTP_200_OK

    data = res.json()
    assert "results" in data
    assert data["count"] == 2

    numbers = [r["number"] for r in data["results"]]
    assert sr1.number in numbers
    assert sr2.number in numbers

    # Verify standard fields
    item = data["results"][0]
    assert "reference_number" in item
    assert "status_label" in item
    assert "progress_stage" in item
    assert "asset_tag" in item
    assert "site_code" in item
    assert "created_at" in item
    assert "updated_at" in item


# 2. Client can retrieve an authorized request's details and progression
def test_client_retrieves_authorized_request_details(as_user, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.detail@acme.test")
    api = as_user(user, org_a)

    sr = portal_services.create_portal_service_request(
        membership,
        asset=asset_a,
        title="Water Pump Pressure Loss",
        description="Pressure dropped from 4 bar to 1 bar",
        severity="MEDIUM",
    )

    # By UUID
    res = api.get(f"/api/v1/portal/requests/{sr.id}/")
    assert res.status_code == status.HTTP_200_OK

    data = res.json()
    assert data["id"] == str(sr.id)
    assert data["number"] == sr.number
    assert data["title"] == sr.title
    assert data["status"] == "NEW"
    assert data["status_label"] == "Submitted"
    assert data["progress_stage"] == "PENDING"
    assert data["asset_tag"] == asset_a.asset_tag
    assert "history" in data
    assert len(data["history"]) >= 1
    assert data["history"][0]["to_status"] == "NEW"

    # By Reference Number
    res_by_num = api.get(f"/api/v1/portal/requests/{sr.number}/")
    assert res_by_num.status_code == status.HTTP_200_OK
    assert res_by_num.json()["id"] == str(sr.id)


# 3. Request status matches persisted M05 lifecycle data
def test_request_status_tracking_matches_persisted_m05_data(as_user, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.lifecycle@acme.test")
    api = as_user(user, org_a)

    sr = portal_services.create_portal_service_request(
        membership,
        asset=asset_a,
        title="Generator Temperature Sensor Fault",
        severity="HIGH",
    )

    # Progress through M05 lifecycle: NEW -> TRIAGED -> APPROVED
    incident_services.transition(sr, action="triage", actor=user)
    sr.refresh_from_db()
    assert sr.status == "TRIAGED"

    res = api.get(f"/api/v1/portal/requests/{sr.id}/")
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["status"] == "TRIAGED"
    assert res.json()["status_label"] == "Under Review"
    assert res.json()["progress_stage"] == "PENDING"

    incident_services.transition(sr, action="approve", actor=user)
    sr.refresh_from_db()
    assert sr.status == "APPROVED"

    res = api.get(f"/api/v1/portal/requests/{sr.id}/")
    assert res.json()["status"] == "APPROVED"
    assert res.json()["status_label"] == "Approved"


# 4. Unauthenticated users are rejected
def test_unauthenticated_requests_rejected(client, org_a, asset_a, make_portal_client):
    _, membership, _ = make_portal_client(org_a, "client.anon@acme.test")
    sr = portal_services.create_portal_service_request(
        membership,
        asset=asset_a,
        title="Anonymous check",
    )

    res_list = client.get("/api/v1/portal/requests/")
    assert res_list.status_code == status.HTTP_401_UNAUTHORIZED

    res_detail = client.get(f"/api/v1/portal/requests/{sr.id}/")
    assert res_detail.status_code == status.HTTP_401_UNAUTHORIZED


# 5. Users without portal.request.view are rejected
def test_user_without_portal_request_view_permission_rejected(as_user, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.noperm@acme.test", role_key=None)
    api = as_user(user, org_a)

    res_list = api.get("/api/v1/portal/requests/")
    assert res_list.status_code == status.HTTP_403_FORBIDDEN

    res_detail = api.get(f"/api/v1/portal/requests/{uuid.uuid4()}/")
    assert res_detail.status_code == status.HTTP_403_FORBIDDEN


# 6. Disabled or suspended portal accounts are rejected
def test_disabled_or_suspended_portal_accounts_rejected(as_user, org_a, asset_a, make_portal_client):
    # Disabled account
    user_dis, _, _ = make_portal_client(org_a, "client.disabled@acme.test", is_enabled=False)
    api_dis = as_user(user_dis, org_a)

    res = api_dis.get("/api/v1/portal/requests/")
    assert res.status_code == status.HTTP_403_FORBIDDEN

    # Suspended membership
    user_susp, _, _ = make_portal_client(org_a, "client.suspended@acme.test", active=False)
    api_susp = as_user(user_susp, org_a)

    res2 = api_susp.get("/api/v1/portal/requests/")
    assert res2.status_code == status.HTTP_403_FORBIDDEN


# 7. Cross-organization requests are inaccessible
def test_cross_organization_requests_inaccessible(as_user, org_a, org_b, asset_a, asset_b, make_portal_client):
    user_a, mem_a, _ = make_portal_client(org_a, "client.orga@acme.test")
    _, mem_b, _ = make_portal_client(org_b, "client.orgb@acme.test")

    sr_b = portal_services.create_portal_service_request(
        mem_b,
        asset=asset_b,
        title="Org B Secret Request",
    )

    api_a = as_user(user_a, org_a)

    # Listing org A never leaks org B
    res_list = api_a.get("/api/v1/portal/requests/")
    numbers = [r["number"] for r in res_list.json()["results"]]
    assert sr_b.number not in numbers

    # Direct detail lookup returns 404
    res_detail = api_a.get(f"/api/v1/portal/requests/{sr_b.id}/")
    assert res_detail.status_code == status.HTTP_404_NOT_FOUND


# 8. Another client's private requests are inaccessible (anti-IDOR)
def test_another_client_private_requests_inaccessible(as_user, org_a, asset_a, make_portal_client):
    user_1, mem_1, _ = make_portal_client(org_a, "client1@acme.test")
    user_2, mem_2, _ = make_portal_client(org_a, "client2@acme.test")

    sr_1 = portal_services.create_portal_service_request(
        mem_1,
        asset=asset_a,
        title="Client 1 Confidential Equipment Issue",
    )
    sr_2 = portal_services.create_portal_service_request(
        mem_2,
        asset=asset_a,
        title="Client 2 Routine Request",
    )

    api_2 = as_user(user_2, org_a)

    # Client 2 list should ONLY contain sr_2, NOT sr_1
    res_list = api_2.get("/api/v1/portal/requests/")
    numbers = [r["number"] for r in res_list.json()["results"]]
    assert sr_2.number in numbers
    assert sr_1.number not in numbers

    # Client 2 cannot access sr_1 by ID
    res_detail = api_2.get(f"/api/v1/portal/requests/{sr_1.id}/")
    assert res_detail.status_code == status.HTTP_404_NOT_FOUND

    # Client 2 cannot access sr_1 by reference number
    res_by_num = api_2.get(f"/api/v1/portal/requests/{sr_1.number}/")
    assert res_by_num.status_code == status.HTTP_404_NOT_FOUND


# 9. Invalid request IDs return 404 Not Found
def test_invalid_request_ids_return_404(as_user, org_a, make_portal_client):
    user, _, _ = make_portal_client(org_a, "client.test404@acme.test")
    api = as_user(user, org_a)

    res = api.get(f"/api/v1/portal/requests/{uuid.uuid4()}/")
    assert res.status_code == status.HTTP_404_NOT_FOUND

    res_num = api.get("/api/v1/portal/requests/SR-9999-99999/")
    assert res_num.status_code == status.HTTP_404_NOT_FOUND


# 10. Pagination and status filtering work correctly
def test_pagination_and_status_filtering(as_user, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.filter@acme.test")
    api = as_user(user, org_a)

    sr1 = portal_services.create_portal_service_request(membership, asset=asset_a, title="Request 1", description="First diagnostic check")
    sr2 = portal_services.create_portal_service_request(
        membership, asset=asset_a, title="Request 2", description="Second diagnostic check",
        override_duplicate=True, override_reason="Distinct diagnostic task 2",
    )
    sr3 = portal_services.create_portal_service_request(
        membership, asset=asset_a, title="Request 3", description="Third diagnostic check",
        override_duplicate=True, override_reason="Distinct diagnostic task 3",
    )

    # Transition sr2 to TRIAGED
    incident_services.transition(sr2, action="triage", actor=user)

    # Filter status=TRIAGED
    res_triaged = api.get("/api/v1/portal/requests/?status=TRIAGED")
    assert res_triaged.status_code == status.HTTP_200_OK
    assert res_triaged.json()["count"] == 1
    assert res_triaged.json()["results"][0]["id"] == str(sr2.id)

    # Filter status=NEW
    res_new = api.get("/api/v1/portal/requests/?status=NEW")
    assert res_new.status_code == status.HTTP_200_OK
    assert res_new.json()["count"] == 2

    # Pagination page_size=2
    res_page = api.get("/api/v1/portal/requests/?page_size=2")
    assert res_page.status_code == status.HTTP_200_OK
    assert len(res_page.json()["results"]) == 2
    assert res_page.json()["next"] is not None


# 11. Sensitive internal fields are excluded from client serializers
def test_sensitive_internal_fields_excluded(as_user, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.security@acme.test")
    api = as_user(user, org_a)

    sr = portal_services.create_portal_service_request(
        membership,
        asset=asset_a,
        title="Check Security Payload",
    )
    incident_services.transition(sr, action="triage", actor=user, reason="Internal triage note")

    res = api.get(f"/api/v1/portal/requests/{sr.id}/")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()

    # Request-level sensitive fields excluded
    forbidden_keys = [
        "internal_notes",
        "technician",
        "assigned_to",
        "work_order",
        "work_orders",
        "audit_metadata",
    ]
    for key in forbidden_keys:
        assert key not in data

    # History-level sensitive fields excluded
    for item in data.get("history", []):
        assert "changed_by" not in item
        assert "changed_by_email" not in item
        assert "changed_by_id" not in item
        assert "source" not in item


# 12. Client Portal UI views render list and detail pages
def test_portal_tracking_ui_views(client, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.ui@acme.test")
    client.force_login(user)

    sr = portal_services.create_portal_service_request(
        membership,
        asset=asset_a,
        title="UI Tracking View Test",
        description="Testing UI display",
    )

    # List page
    res_list = client.get("/app/portal/requests/")
    assert res_list.status_code == 200
    assert "My Service Requests" in res_list.content.decode()
    assert sr.number in res_list.content.decode()
    assert "UI Tracking View Test" in res_list.content.decode()

    # Detail page
    res_detail = client.get(f"/app/portal/requests/{sr.id}/")
    assert res_detail.status_code == 200
    content = res_detail.content.decode()
    assert sr.number in content
    assert "Request Lifecycle Progress" in content
    assert "Submitted" in content
    assert asset_a.asset_tag in content

    # Other client cannot view detail in UI
    user_other, _, _ = make_portal_client(org_a, "client.ui.other@acme.test")
    client.force_login(user_other)
    res_other = client.get(f"/app/portal/requests/{sr.id}/")
    assert res_other.status_code == 404
