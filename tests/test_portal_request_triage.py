import pytest
from django.contrib.auth import get_user_model
from rest_framework import status

from apps.audit.models import AuditLog
from apps.core.exceptions import InvalidTransition
from apps.incidents import services as incident_services
from apps.incidents.models import ServiceRequest
from apps.portal.models import PortalAccount, PortalAssetGrant
from apps.portal import services as portal_services
from apps.rbac import services as rbac
from apps.tenancy.models import Membership

pytestmark = pytest.mark.django_db
User = get_user_model()
PASSWORD = "Correct-Horse-Battery-9"


@pytest.fixture
def make_portal_client(db):
    def _make(org, email="client@acme.test", company_name="Acme Corporation"):
        user = User.objects.filter(email=email).first() or User.objects.create_user(
            email=email, password=PASSWORD, full_name=email.split("@")[0].replace(".", " ").title()
        )
        membership, _ = Membership.objects.unscoped().get_or_create(
            organization=org,
            user=user,
            defaults={"status": Membership.Status.ACTIVE, "job_title": "Requester"},
        )
        role = rbac.system_role_by_key(org, "client_requester")
        rbac.set_membership_roles(membership, [role], actor=None, system=True)

        account, _ = PortalAccount.objects.unscoped().get_or_create(
            organization=org,
            membership=membership,
            defaults={"company_name": company_name, "is_enabled": True},
        )
        return user, membership, account

    return _make


@pytest.fixture
def asset_1(org_a, site_a1, make_asset):
    return make_asset(org_a, site_a1, "CHILLER-01")


@pytest.fixture
def service_manager_a(org_a, make_member):
    return make_member(org_a, "service.manager@alpha.test", "service_manager").user


@pytest.fixture
def service_manager_b(org_b, make_member):
    return make_member(org_b, "service.manager@beta.test", "service_manager").user


# 1. Client-created request enters correct intake status (NEW)
def test_client_request_enters_new_status_intake(as_user, org_a, asset_1, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.intake@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    api = as_user(user, org_a)
    payload = {"title": "Generator fuel line pressure issue", "asset": str(asset_1.pk), "severity": "HIGH"}
    res = api.post("/api/v1/portal/requests/", payload, format="json")
    assert res.status_code == status.HTTP_201_CREATED

    sr = ServiceRequest.objects.get(pk=res.json()["id"])
    assert sr.status == "NEW"
    assert res.json()["status"] == "NEW"
    assert res.json()["status_label"] == "Submitted"
    assert res.json()["progress_stage"] == "PENDING"


# 2. Authorized internal staff triages client-created request
def test_authorized_staff_triages_client_request(as_user, org_a, asset_1, service_manager_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.triage@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    sr = portal_services.create_portal_service_request(membership, asset=asset_1, title="Filter blockage alarm")
    assert sr.status == "NEW"

    # Authorized internal staff triages the request
    sr_triaged = incident_services.transition(
        sr,
        action="triage",
        reason="Assigned to mechanical triage queue",
        actor=service_manager_a,
    )
    assert sr_triaged.status == "TRIAGED"
    assert sr_triaged.triaged_at is not None

    # Status change is audited
    audit_entry = AuditLog.objects.filter(
        organization=org_a,
        action="incident.status_changed",
        target_id=str(sr.pk),
    ).first()
    assert audit_entry is not None
    assert audit_entry.actor == service_manager_a

    # Client sees updated client-safe status in portal
    api_client = as_user(user, org_a)
    res = api_client.get(f"/api/v1/portal/requests/{sr.pk}/")
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["status"] == "TRIAGED"
    assert res.json()["status_label"] == "Under Review"


# 3. Internal triage notes are NOT exposed to portal client
def test_internal_triage_notes_not_exposed_to_portal_client(as_user, client, org_a, asset_1, service_manager_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.privacy@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    sr = portal_services.create_portal_service_request(membership, asset=asset_1, title="Confidential equipment review")
    incident_services.transition(
        sr,
        action="triage",
        reason="INTERNAL ONLY: Customer warranty questionable, check contract AMC-90",
        actor=service_manager_a,
    )

    # API verification: reason must be empty in client projection
    api_client = as_user(user, org_a)
    res = api_client.get(f"/api/v1/portal/requests/{sr.pk}/")
    assert res.status_code == status.HTTP_200_OK
    history = res.json()["history"]

    triage_event = next(h for h in history if h["to_status"] == "TRIAGED")
    assert triage_event["reason"] == ""
    assert "INTERNAL ONLY" not in str(res.json())

    # UI verification: internal triage reason not rendered in HTML
    client.force_login(user)
    res_ui = client.get(f"/app/portal/requests/{sr.pk}/")
    assert res_ui.status_code == 200
    html = res_ui.content.decode()
    assert "INTERNAL ONLY" not in html


# 4. Staff rejects request with rejection reason visible to client
def test_staff_rejects_triaged_request_with_rejection_reason_visible(as_user, client, org_a, asset_1, service_manager_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.rejected@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    sr = portal_services.create_portal_service_request(membership, asset=asset_1, title="Request to be declined")
    incident_services.transition(sr, action="triage", actor=service_manager_a)
    incident_services.transition(
        sr,
        action="reject",
        reason="Equipment model is not covered under the active SLA agreement.",
        actor=service_manager_a,
    )

    # API: rejection reason is safe and exposed
    api_client = as_user(user, org_a)
    res = api_client.get(f"/api/v1/portal/requests/{sr.pk}/")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["status"] == "REJECTED"
    assert data["status_label"] == "Declined"
    assert data["progress_stage"] == "REJECTED"

    reject_hist = next(h for h in data["history"] if h["to_status"] == "REJECTED")
    assert "not covered under the active SLA agreement" in reject_hist["reason"]

    # UI: rejection banner rendered with reason
    client.force_login(user)
    res_ui = client.get(f"/app/portal/requests/{sr.pk}/")
    assert res_ui.status_code == 200
    html = res_ui.content.decode()
    assert "Request Declined" in html
    assert "not covered under the active SLA agreement" in html


# 5. Staff approves triaged request
def test_staff_approves_triaged_request(as_user, org_a, asset_1, service_manager_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.approved@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    sr = portal_services.create_portal_service_request(membership, asset=asset_1, title="Request to be approved")
    incident_services.transition(sr, action="triage", actor=service_manager_a)
    sr_approved = incident_services.transition(sr, action="approve", actor=service_manager_a)

    assert sr_approved.status == "APPROVED"
    assert sr_approved.decided_at is not None

    api_client = as_user(user, org_a)
    res = api_client.get(f"/api/v1/portal/requests/{sr.pk}/")
    assert res.json()["status"] == "APPROVED"
    assert res.json()["status_label"] == "Approved"


# 6. Portal client is forbidden from triaging or approving own request
def test_portal_client_forbidden_from_triage_actions(as_user, org_a, asset_1, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.hacker@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    sr = portal_services.create_portal_service_request(membership, asset=asset_1, title="Self triage attempt")

    api_client = as_user(user, org_a)
    # Attempting to call M05 internal transition API
    res = api_client.post(f"/api/v1/requests/{sr.pk}/transition/", {"action": "triage"}, format="json")
    assert res.status_code == status.HTTP_403_FORBIDDEN


# 7. Invalid transition sequence rejected
def test_invalid_transition_sequence_rejected(org_a, asset_1, service_manager_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.invalid@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    sr = portal_services.create_portal_service_request(membership, asset=asset_1, title="Invalid transition test")
    assert sr.status == "NEW"

    # Approving directly from NEW without TRIAGED must fail
    with pytest.raises(InvalidTransition):
        incident_services.transition(sr, action="approve", actor=service_manager_a)


# 8. Tenant isolation on triage: staff from Org B cannot triage Org A request
def test_tenant_isolation_on_triage(as_user, org_a, org_b, asset_1, service_manager_b, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.isolation@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    sr = portal_services.create_portal_service_request(membership, asset=asset_1, title="Cross org triage check")

    api_sm_b = as_user(service_manager_b, org_b)
    res = api_sm_b.post(f"/api/v1/requests/{sr.pk}/transition/", {"action": "triage"}, format="json")
    assert res.status_code in (status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN)
