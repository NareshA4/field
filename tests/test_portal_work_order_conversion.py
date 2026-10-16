from datetime import timedelta
import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status

from apps.core.exceptions import Conflict, InvalidTransition
from apps.incidents import services as incident_services
from apps.incidents.models import ServiceRequest
from apps.portal.models import PortalAccount, PortalAssetGrant
from apps.portal import services as portal_services
from apps.rbac import services as rbac
from apps.tenancy.models import Membership
from apps.workorders.models import WorkOrder

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
    return make_member(org_a, "service.manager@alpha.test", "service_manager")


@pytest.fixture
def planner_a(org_a, make_member):
    return make_member(org_a, "planner@alpha.test", "maintenance_planner")


@pytest.fixture
def service_manager_b(org_b, make_member):
    return make_member(org_b, "service.manager@beta.test", "service_manager")


@pytest.fixture
def technician_member(org_a, make_member):
    return make_member(org_a, "tech.bob@alpha.test", "technician")


def _create_approved_request(membership, asset, staff_user):
    sr = portal_services.create_portal_service_request(membership, asset=asset, title="Compressor overhaul")
    incident_services.transition(sr, action="triage", actor=staff_user)
    sr_approved = incident_services.transition(sr, action="approve", actor=staff_user)
    return sr_approved


# 1. Authorized internal staff converts approved request to work order
def test_authorized_staff_converts_approved_request_to_work_order(
    as_user, org_a, asset_1, service_manager_a, planner_a, make_portal_client
):
    user, membership, account = make_portal_client(org_a, "client.wo@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    sr = _create_approved_request(membership, asset_1, service_manager_a.user)
    assert sr.status == "APPROVED"

    # Conversion executed via existing M05-to-M06 service
    wo = incident_services.create_work_order_for_request(
        sr,
        actor=planner_a.user,
        membership=planner_a,
        planned_start=timezone.now() + timedelta(days=1),
        planned_end=timezone.now() + timedelta(days=1, hours=4),
        due_at=timezone.now() + timedelta(days=2),
        estimated_hours=4.0,
    )

    assert wo is not None
    assert wo.source_request == sr
    assert wo.order_number.startswith("WO-")

    sr.refresh_from_db()
    assert sr.status == "WORK_ORDER_CREATED"

    # Client can see client-safe tracking details in portal
    api_client = as_user(user, org_a)
    res = api_client.get(f"/api/v1/portal/requests/{sr.pk}/")
    assert res.status_code == status.HTTP_200_OK

    data = res.json()
    assert data["status"] == "WORK_ORDER_CREATED"
    assert data["status_label"] == "Scheduled"
    assert data["progress_stage"] == "IN_PROGRESS"

    assert data["work_order"] is not None
    assert data["work_order"]["order_number"] == wo.order_number
    assert data["work_order"]["planned_start"] is not None


# 2. Duplicate conversion is prevented (fails with conflict/invalid transition)
def test_duplicate_conversion_prevented(org_a, asset_1, service_manager_a, planner_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.nodup@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    sr = _create_approved_request(membership, asset_1, service_manager_a.user)

    # First conversion succeeds
    wo1 = incident_services.create_work_order_for_request(
        sr,
        actor=planner_a.user,
        membership=planner_a,
    )
    assert wo1 is not None

    # Repeated conversion is blocked
    with pytest.raises((Conflict, InvalidTransition)):
        incident_services.create_work_order_for_request(
            sr,
            actor=planner_a.user,
            membership=planner_a,
        )


# 3. Unapproved request cannot be converted
def test_unapproved_request_cannot_be_converted(org_a, asset_1, service_manager_a, planner_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.unapproved@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    sr = portal_services.create_portal_service_request(membership, asset=asset_1, title="Still new")
    assert sr.status == "NEW"

    with pytest.raises(InvalidTransition):
        incident_services.create_work_order_for_request(
            sr,
            actor=planner_a.user,
            membership=planner_a,
        )


# 4. Client sees client-safe work order tracking before dispatch (technician hidden)
def test_technician_name_hidden_before_dispatch(
    as_user, client, org_a, asset_1, service_manager_a, planner_a, technician_member, make_portal_client
):
    user, membership, account = make_portal_client(org_a, "client.pre_dispatch@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    sr = _create_approved_request(membership, asset_1, service_manager_a.user)
    wo = incident_services.create_work_order_for_request(
        sr,
        actor=planner_a.user,
        membership=planner_a,
        assigned_to=technician_member,
    )
    wo.status = WorkOrder.Status.ASSIGNED
    WorkOrder.objects.filter(pk=wo.pk).update(status=WorkOrder.Status.ASSIGNED)

    # API verification: technician_name is None before dispatch
    api_client = as_user(user, org_a)
    res = api_client.get(f"/api/v1/portal/requests/{sr.pk}/")
    assert res.status_code == status.HTTP_200_OK

    wo_data = res.json()["work_order"]
    assert wo_data["order_number"] == wo.order_number
    assert wo_data["technician_name"] is None

    # UI verification: technician display name not rendered
    client.force_login(user)
    res_ui = client.get(f"/app/portal/requests/{sr.pk}/")
    assert res_ui.status_code == 200
    assert technician_member.user.full_name not in res_ui.content.decode()


# 5. Technician display name shown to client only from DISPATCHED on
def test_technician_name_shown_when_dispatched(
    as_user, client, org_a, asset_1, service_manager_a, planner_a, technician_member, make_portal_client
):
    user, membership, account = make_portal_client(org_a, "client.dispatched@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    sr = _create_approved_request(membership, asset_1, service_manager_a.user)
    wo = incident_services.create_work_order_for_request(
        sr,
        actor=planner_a.user,
        membership=planner_a,
        assigned_to=technician_member,
    )
    wo.status = WorkOrder.Status.DISPATCHED
    wo.dispatched_at = timezone.now()
    WorkOrder.objects.filter(pk=wo.pk).update(status=WorkOrder.Status.DISPATCHED, dispatched_at=wo.dispatched_at)

    # API: technician display name is revealed
    api_client = as_user(user, org_a)
    res = api_client.get(f"/api/v1/portal/requests/{sr.pk}/")
    assert res.status_code == status.HTTP_200_OK
    wo_data = res.json()["work_order"]
    assert wo_data["technician_name"] == technician_member.user.full_name

    # UI: technician name rendered
    client.force_login(user)
    res_ui = client.get(f"/app/portal/requests/{sr.pk}/")
    assert res_ui.status_code == 200
    assert technician_member.user.full_name in res_ui.content.decode()


# 6. Internal work order fields are NEVER exposed to client
def test_internal_work_order_fields_never_exposed(
    as_user, org_a, asset_1, service_manager_a, planner_a, technician_member, make_portal_client
):
    user, membership, account = make_portal_client(org_a, "client.leak_check@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    sr = _create_approved_request(membership, asset_1, service_manager_a.user)
    wo = incident_services.create_work_order_for_request(
        sr,
        actor=planner_a.user,
        membership=planner_a,
        assigned_to=technician_member,
        planned_start=timezone.now() + timedelta(days=1),
        planned_end=timezone.now() + timedelta(days=1, hours=10),
        due_at=timezone.now() + timedelta(days=2),
        estimated_hours=8.50,
        supervisor_notes="Confidential cost estimate and technician performance rating",
    )

    api_client = as_user(user, org_a)
    res = api_client.get(f"/api/v1/portal/requests/{sr.pk}/")
    assert res.status_code == status.HTTP_200_OK

    raw_json = str(res.json())
    forbidden_terms = [
        "supervisor_notes",
        "Confidential cost estimate",
        "estimated_hours",
        "checklist",
        "provisional_checklist",
        "hold_reason",
    ]
    for term in forbidden_terms:
        assert term not in raw_json


# 7. Portal client cannot trigger work order conversion
def test_portal_client_cannot_trigger_conversion(as_user, org_a, asset_1, service_manager_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.no_convert@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    sr = _create_approved_request(membership, asset_1, service_manager_a.user)

    api_client = as_user(user, org_a)
    # Attempting to call staff work order creation
    res = api_client.post("/api/v1/work-orders/", {"source_request": str(sr.pk), "title": "Hacked WO"}, format="json")
    assert res.status_code == status.HTTP_403_FORBIDDEN


# 8. Tenant isolation on conversion
def test_cross_tenant_conversion_blocked(org_a, org_b, asset_1, service_manager_a, service_manager_b, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.cross_convert@acme.test")
    portal_services.grant_portal_asset(account, asset_1)

    sr = _create_approved_request(membership, asset_1, service_manager_a.user)

    # Service manager from Org B cannot convert request from Org A
    with pytest.raises(Exception):
        incident_services.create_work_order_for_request(
            sr,
            actor=service_manager_b.user,
            membership=service_manager_b,
        )
