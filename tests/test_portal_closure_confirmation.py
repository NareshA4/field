import uuid
import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status

from apps.audit.models import AuditLog
from apps.incidents import services as incident_services
from apps.incidents.models import ServiceRequest
from apps.portal.models import PortalAccount, PortalClosureConfirmation
from apps.portal import services as portal_services
from apps.rbac import services as rbac
from apps.tenancy.models import Membership
from apps.workorders import services as wo_services
from apps.workorders.models import ClosureApproval, WorkOrder

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


def _resolve_request(sr, actor):
    """Helper to transition a ServiceRequest from NEW -> TRIAGED -> IN_SERVICE -> RESOLVED."""
    incident_services.transition(sr, action="triage", actor=actor)
    # Direct transition to RESOLVED (or via M05 system action)
    sr.status = ServiceRequest.Status.RESOLVED
    sr.resolved_at = timezone.now()
    sr.save(update_fields=["status", "resolved_at"])


# 1. Authorized requester confirms eligible completed service
def test_authorized_requester_confirms_eligible_service(as_user, org_a, asset_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.confirm@acme.test")
    api = as_user(user, org_a)

    sr = portal_services.create_portal_service_request(
        membership,
        asset=asset_a,
        title="Pump Vibration Inspection",
    )
    _resolve_request(sr, user)

    payload = {
        "rating": 5,
        "feedback": "Vibration resolved. Excellent service.",
    }

    res = api.post(f"/api/v1/portal/requests/{sr.id}/confirm/", payload, format="json")
    assert res.status_code == status.HTTP_200_OK

    data = res.json()
    assert data["status"] == "CONFIRMED"
    assert data["reference_number"] == sr.number
    assert data["confirmed_by"] == user.email
    assert data["rating"] == 5
    assert data["feedback"] == "Vibration resolved. Excellent service."
    assert data["confirmed_at"] is not None


# 2. Confirmation is persisted in PostgreSQL
def test_confirmation_is_persisted_in_postgresql(as_user, org_a, asset_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.pgpersist@acme.test")
    api = as_user(user, org_a)

    sr = portal_services.create_portal_service_request(
        membership,
        asset=asset_a,
        title="HVAC Filter Cleaning",
    )
    _resolve_request(sr, user)

    res = api.post(f"/api/v1/portal/requests/{sr.id}/confirm/", {"rating": 4, "feedback": "Persisted in DB"}, format="json")
    assert res.status_code == status.HTTP_200_OK

    # Query directly from DB
    conf = PortalClosureConfirmation.objects.filter(request=sr).first()
    assert conf is not None
    assert conf.organization == org_a
    assert conf.rating == 4
    assert conf.feedback == "Persisted in DB"

    sr.refresh_from_db()
    assert sr.status == ServiceRequest.Status.CONFIRMED


# 3. Correct user and timestamp are recorded
def test_correct_user_and_timestamp_recorded(as_user, org_a, asset_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.timestamp@acme.test")
    api = as_user(user, org_a)

    sr = portal_services.create_portal_service_request(
        membership,
        asset=asset_a,
        title="Generator Maintenance Check",
    )
    _resolve_request(sr, user)

    before_time = timezone.now()
    res = api.post(f"/api/v1/portal/requests/{sr.id}/confirm/", {"feedback": "Confirmed on time"}, format="json")
    assert res.status_code == status.HTTP_200_OK
    after_time = timezone.now()

    conf = PortalClosureConfirmation.objects.get(request=sr)
    assert conf.confirmed_by == user
    assert conf.portal_account == account
    assert before_time <= conf.confirmed_at <= after_time

    sr.refresh_from_db()
    assert before_time <= sr.confirmed_at <= after_time


# 4. Unauthenticated users are rejected
def test_unauthenticated_user_confirmation_rejected(client, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.anon@acme.test")
    sr = portal_services.create_portal_service_request(membership, asset=asset_a, title="Air Filter Replacement")
    _resolve_request(sr, user)

    res = client.post(f"/api/v1/portal/requests/{sr.id}/confirm/", {"feedback": "Good job"}, format="json")
    assert res.status_code == status.HTTP_401_UNAUTHORIZED


# 5. Users without portal.request.confirm permission are rejected
def test_user_without_portal_request_confirm_rejected(as_user, org_a, asset_a, make_portal_client):
    # Member with view only (no confirm)
    user, membership, _ = make_portal_client(org_a, "client.noconfirm@acme.test", role_key=None)
    # Grant only portal.request.view
    role = rbac.create_role(
        org_a,
        name="Portal Viewer",
        description="Portal Viewer",
        permission_codes=["portal.request.view"],
        actor=None,
    )
    rbac.set_membership_roles(membership, [role], actor=None, system=True)

    sr = ServiceRequest.objects.create(
        organization=org_a,
        reported_by=membership,
        asset=asset_a,
        site=asset_a.site,
        title="Lighting Issue",
        status=ServiceRequest.Status.RESOLVED,
        occurred_at=timezone.now(),
        number="SR-2026-90001",
    )

    api = as_user(user, org_a)
    res = api.post(f"/api/v1/portal/requests/{sr.id}/confirm/", {"feedback": "Done"}, format="json")
    assert res.status_code == status.HTTP_403_FORBIDDEN


# 6. Disabled or suspended portal accounts are rejected
def test_disabled_or_suspended_portal_accounts_rejected(as_user, org_a, asset_a, make_portal_client):
    user_dis, mem_dis, _ = make_portal_client(org_a, "client.dis@acme.test", is_enabled=False)
    sr_dis = ServiceRequest.objects.create(
        organization=org_a,
        reported_by=mem_dis,
        asset=asset_a,
        site=asset_a.site,
        title="Check disabled",
        status=ServiceRequest.Status.RESOLVED,
        occurred_at=timezone.now(),
        number="SR-2026-90002",
    )
    api_dis = as_user(user_dis, org_a)
    res_dis = api_dis.post(f"/api/v1/portal/requests/{sr_dis.id}/confirm/", {}, format="json")
    assert res_dis.status_code == status.HTTP_403_FORBIDDEN

    user_susp, mem_susp, _ = make_portal_client(org_a, "client.susp@acme.test", active=False)
    sr_susp = ServiceRequest.objects.create(
        organization=org_a,
        reported_by=mem_susp,
        asset=asset_a,
        site=asset_a.site,
        title="Check suspended",
        status=ServiceRequest.Status.RESOLVED,
        occurred_at=timezone.now(),
        number="SR-2026-90003",
    )
    api_susp = as_user(user_susp, org_a)
    res_susp = api_susp.post(f"/api/v1/portal/requests/{sr_susp.id}/confirm/", {}, format="json")
    assert res_susp.status_code == status.HTTP_403_FORBIDDEN


# 7. Cross-organization confirmation is denied
def test_cross_organization_confirmation_denied(as_user, org_a, org_b, asset_b, make_portal_client):
    user_a, mem_a, _ = make_portal_client(org_a, "client.orga@acme.test")
    user_b, mem_b, _ = make_portal_client(org_b, "client.orgb@acme.test")

    sr_b = portal_services.create_portal_service_request(mem_b, asset=asset_b, title="Org B Request")
    _resolve_request(sr_b, user_b)

    api_a = as_user(user_a, org_a)
    res = api_a.post(f"/api/v1/portal/requests/{sr_b.id}/confirm/", {}, format="json")
    assert res.status_code == status.HTTP_404_NOT_FOUND


# 8. Another client's request cannot be confirmed
def test_another_client_request_cannot_be_confirmed(as_user, org_a, asset_a, make_portal_client):
    user_1, mem_1, _ = make_portal_client(org_a, "client1.confirm@acme.test")
    user_2, mem_2, _ = make_portal_client(org_a, "client2.confirm@acme.test")

    sr_1 = portal_services.create_portal_service_request(mem_1, asset=asset_a, title="Client 1 Issue")
    _resolve_request(sr_1, user_1)

    api_2 = as_user(user_2, org_a)
    res = api_2.post(f"/api/v1/portal/requests/{sr_1.id}/confirm/", {"feedback": "Confirmed"}, format="json")
    assert res.status_code == status.HTTP_404_NOT_FOUND


# 9. Incomplete service cannot be confirmed
def test_incomplete_service_cannot_be_confirmed(as_user, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.incomplete@acme.test")
    api = as_user(user, org_a)

    sr = portal_services.create_portal_service_request(membership, asset=asset_a, title="Still In Progress")
    assert sr.status == ServiceRequest.Status.NEW

    res = api.post(f"/api/v1/portal/requests/{sr.id}/confirm/", {}, format="json")
    assert res.status_code == status.HTTP_409_CONFLICT
    assert "in progress" in res.json().get("detail", "").lower()


# 10. Required supervisor approval cannot be bypassed
def test_required_supervisor_approval_cannot_be_bypassed(as_user, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.superreview@acme.test")
    api = as_user(user, org_a)

    sr = portal_services.create_portal_service_request(membership, asset=asset_a, title="Work Order Linked Request")
    _resolve_request(sr, user)

    # Link a WorkOrder that is awaiting supervisor review (not yet closed/approved)
    wo = WorkOrder.objects.create(
        organization=org_a,
        created_by=user,
        site=asset_a.site,
        asset=asset_a,
        source_request=sr,
        source_type=WorkOrder.SourceType.REQUEST,
        source_id=sr.pk,
        source_reference=sr.number,
        order_number="WO-2026-88001",
        title="WO for Request",
        status=WorkOrder.Status.SUPERVISOR_REVIEW,
        priority=WorkOrder.Priority.MEDIUM,
    )

    # Confirmation should be rejected because supervisor has not approved the work order
    res = api.post(f"/api/v1/portal/requests/{sr.id}/confirm/", {}, format="json")
    assert res.status_code == status.HTTP_409_CONFLICT
    assert "supervisor" in res.json().get("detail", "").lower()

    # Now simulate supervisor review approval on the work order
    ClosureApproval.objects.create(
        organization=org_a,
        work_order=wo,
        approver=user,
        decision=ClosureApproval.Decision.APPROVED,
        comment="Supervisor approved work.",
    )
    wo._workflow_transition_write = True
    wo.status = WorkOrder.Status.CLOSED
    wo.save(update_fields=["status"])

    # Now confirmation succeeds!
    res_ok = api.post(f"/api/v1/portal/requests/{sr.id}/confirm/", {"feedback": "Supervisor approved, I confirm too."}, format="json")
    assert res_ok.status_code == status.HTTP_200_OK
    assert res_ok.json()["status"] == "CONFIRMED"


# 11. Duplicate confirmation is handled safely
def test_duplicate_confirmation_rejected(as_user, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.dup@acme.test")
    api = as_user(user, org_a)

    sr = portal_services.create_portal_service_request(membership, asset=asset_a, title="Duplicate Check")
    _resolve_request(sr, user)

    # First confirmation succeeds
    res1 = api.post(f"/api/v1/portal/requests/{sr.id}/confirm/", {"feedback": "First confirmation"}, format="json")
    assert res1.status_code == status.HTTP_200_OK

    # Second confirmation returns 409 Conflict
    res2 = api.post(f"/api/v1/portal/requests/{sr.id}/confirm/", {"feedback": "Second confirmation attempt"}, format="json")
    assert res2.status_code == status.HTTP_409_CONFLICT
    assert "already" in res2.json().get("detail", "").lower()


# 12. Invalid request IDs return 404 Not Found
def test_invalid_request_id_returns_404(as_user, org_a, make_portal_client):
    user, _, _ = make_portal_client(org_a, "client.id404@acme.test")
    api = as_user(user, org_a)

    res = api.post(f"/api/v1/portal/requests/{uuid.uuid4()}/confirm/", {}, format="json")
    assert res.status_code == status.HTTP_404_NOT_FOUND

    res_num = api.post("/api/v1/portal/requests/SR-9999-00000/confirm/", {}, format="json")
    assert res_num.status_code == status.HTTP_404_NOT_FOUND


# 13. Audit event is created
def test_audit_event_created_on_closure_confirmation(as_user, org_a, asset_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.audit@acme.test")
    api = as_user(user, org_a)

    sr = portal_services.create_portal_service_request(membership, asset=asset_a, title="Audit Verified Issue")
    _resolve_request(sr, user)

    res = api.post(f"/api/v1/portal/requests/{sr.id}/confirm/", {"rating": 4, "feedback": "Audited confirmation"}, format="json")
    assert res.status_code == status.HTTP_200_OK

    audit_entry = AuditLog.objects.filter(
        organization=org_a,
        action="portal.closure_confirmed",
    ).first()
    assert audit_entry is not None
    assert audit_entry.actor == user
    assert audit_entry.after["rating"] == 4
    assert audit_entry.metadata["portal_account_id"] == str(account.pk)
    assert audit_entry.metadata["service_request_number"] == sr.number


# 14. UI confirmation flow and details page integration
def test_ui_closure_confirmation_flow(client, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.ui.confirm@acme.test")
    client.force_login(user)

    sr = portal_services.create_portal_service_request(membership, asset=asset_a, title="UI Confirmation Test")
    _resolve_request(sr, user)

    # Details page shows confirmation card when eligible
    res_get = client.get(f"/app/portal/requests/{sr.id}/")
    assert res_get.status_code == 200
    assert "Confirm Service Completion" in res_get.content.decode()

    # Post confirmation via UI
    res_post = client.post(
        f"/app/portal/requests/{sr.id}/confirm/",
        {"rating": "5", "feedback": "UI Confirmation Successful"},
        follow=True,
    )
    assert res_post.status_code == 200
    content = res_post.content.decode()
    assert "completion confirmed successfully" in content.lower()
    assert "Service Completion Confirmed" in content

    # Refresh details page: Confirm button is now hidden, confirmed banner is shown
    res_get2 = client.get(f"/app/portal/requests/{sr.id}/")
    content2 = res_get2.content.decode()
    assert "Action Required: Confirm Service Completion" not in content2
    assert "Service Completion Confirmed!" in content2
    assert user.email in content2


# 15. Invalid closure rating is rejected with 400
def test_invalid_closure_rating_rejected(as_user, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.invalidrate@acme.test")
    sr = portal_services.create_portal_service_request(membership, asset=asset_a, title="Invalid Rating Test")
    _resolve_request(sr, user)

    client = as_user(user, org_a)
    res = client.post(
        f"/api/v1/portal/requests/{sr.id}/confirm/",
        {"rating": 10, "feedback": "Rating is out of bounds"},
    )
    assert res.status_code == 400


# 16. Authorized client reopens resolved request via API
def test_client_reopens_resolved_request_via_api(as_user, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.reopen.api@acme.test")
    sr = portal_services.create_portal_service_request(membership, asset=asset_a, title="Reopen API Test")
    _resolve_request(sr, user)

    client = as_user(user, org_a)
    res = client.post(
        f"/api/v1/portal/requests/{sr.id}/reopen/",
        {"reason": "Machine started leaking again after technician left"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "APPROVED"

    sr.refresh_from_db()
    assert sr.status == "APPROVED"
    assert sr.resolved_at is None
    assert sr.decision_reason == "Machine started leaking again after technician left"

    # Audit log check
    audit_entry = AuditLog.objects.filter(
        organization=org_a,
        action="portal.request_reopened",
    ).first()
    assert audit_entry is not None
    assert audit_entry.actor == user


# 17. Client reopen requires mandatory reason
def test_client_reopen_requires_mandatory_reason(as_user, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.noreason@acme.test")
    sr = portal_services.create_portal_service_request(membership, asset=asset_a, title="Mandatory Reason Test")
    _resolve_request(sr, user)

    client = as_user(user, org_a)
    # Empty reason
    res = client.post(f"/api/v1/portal/requests/{sr.id}/reopen/", {"reason": ""})
    assert res.status_code == 400

    # Short reason (< 3 chars)
    res2 = client.post(f"/api/v1/portal/requests/{sr.id}/reopen/", {"reason": "no"})
    assert res2.status_code == 400


# 18. Unresolved requests cannot be reopened
def test_unresolved_request_cannot_be_reopened(as_user, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.unresolved@acme.test")
    sr = portal_services.create_portal_service_request(membership, asset=asset_a, title="Not Resolved Reopen Test")

    client = as_user(user, org_a)
    # Status is NEW
    res = client.post(
        f"/api/v1/portal/requests/{sr.id}/reopen/",
        {"reason": "Premature reopen attempt"},
    )
    assert res.status_code == 409


# 19. Client cannot reopen another client's request
def test_client_cannot_reopen_other_client_request(as_user, org_a, asset_a, make_portal_client):
    user1, mem1, _ = make_portal_client(org_a, "client.owner@acme.test")
    user2, mem2, _ = make_portal_client(org_a, "client.intruder@acme.test")

    sr = portal_services.create_portal_service_request(mem1, asset=asset_a, title="Private Request")
    _resolve_request(sr, user1)

    client2 = as_user(user2, org_a)
    res = client2.post(
        f"/api/v1/portal/requests/{sr.id}/reopen/",
        {"reason": "Unauthorized reopen attempt"},
    )
    assert res.status_code == 404


# 20. Client reopen UI workflow
def test_client_reopen_ui_workflow(client, org_a, asset_a, make_portal_client):
    user, membership, _ = make_portal_client(org_a, "client.reopen.ui@acme.test")
    client.force_login(user)

    sr = portal_services.create_portal_service_request(membership, asset=asset_a, title="UI Reopen Flow Test")
    _resolve_request(sr, user)

    # Details page shows reopen action
    res_get = client.get(f"/app/portal/requests/{sr.id}/")
    assert res_get.status_code == 200
    assert "Reopen Request" in res_get.content.decode()

    # Post reopen via UI
    res_post = client.post(
        f"/app/portal/requests/{sr.id}/reopen/",
        {"reason": "Issue persists on site, rework requested"},
        follow=True,
    )
    assert res_post.status_code == 200
    content = res_post.content.decode()
    assert "has been reopened for rework" in content

    sr.refresh_from_db()
    assert sr.status == "APPROVED"
