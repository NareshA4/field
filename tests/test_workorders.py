from datetime import timedelta

import pytest
from django.urls import reverse
from django.test import Client
from django.utils import timezone

from apps.core.exceptions import Conflict, InvalidTransition, NotFound
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.workorders import services
from apps.workorders.models import ClosureApproval, WorkOrder, WorkOrderEvent
from apps.tenancy.models import Membership

pytestmark = pytest.mark.django_db


def make_order(org, site, membership, *, title="Pump inspection", **kwargs):
    return services.create_work_order(org, site_id=site.pk, membership=membership, actor=membership.user,
                                      title=title, work_type=WorkOrder.WorkType.PREVENTIVE, **kwargs)


def make_review_order(org, site, planner, tech):
    wo = make_order(org, site, planner, title="API supervisor decision",
                    checklist_items=[{"label": "Inspect unit", "required": True, "completed": False}])
    start = timezone.now() + timedelta(days=3)
    wo = services.plan_work_order(wo, membership=planner, actor=planner.user, planned_start=start,
                                  planned_end=start + timedelta(hours=2))
    wo = services.assign_work_order(wo, membership=planner, actor=planner.user, assignee_id=tech.pk)
    wo = services.dispatch_work_order(wo, membership=planner, actor=planner.user)
    wo = services.start_work_order(wo, membership=tech, actor=tech.user)
    services.record_labor(wo, membership=tech, actor=tech.user, work_date=timezone.localdate(), hours="1.0",
                          description="Completed the assigned inspection")
    wo = services.complete_work_order(wo, membership=tech, actor=tech.user,
        resolution_notes="Inspection completed and the asset is operating normally.",
        checklist_items=[{"label": "Inspect unit", "required": True, "completed": True}])
    return services.submit_for_review(wo, membership=tech, actor=tech.user)


def test_full_work_order_lifecycle_is_audited(org_a, site_a1, make_member, owner_a):
    planner = make_member(org_a, "planner-wo@alpha.test", "maintenance_planner")
    tech = make_member(org_a, "technician-wo@alpha.test", "technician")
    supervisor = make_member(org_a, "supervisor-wo@alpha.test", "supervisor")
    wo = make_order(org_a, site_a1, planner, checklist_items=[
        {"label": "Inspect unit", "required": True, "completed": False}])
    start = timezone.now() + timedelta(hours=2)
    end = start + timedelta(hours=3)
    services.plan_work_order(wo, membership=planner, actor=planner.user, planned_start=start,
                             planned_end=end, estimated_hours=2)
    wo = services.assign_work_order(wo, membership=planner, actor=planner.user, assignee_id=tech.pk)
    wo = services.dispatch_work_order(wo, membership=planner, actor=planner.user)
    wo = services.start_work_order(wo, membership=tech, actor=tech.user)
    wo = services.hold_work_order(wo, membership=tech, actor=tech.user, reason="Waiting for safe access")
    wo = services.resume_work_order(wo, membership=tech, actor=tech.user)
    services.record_labor(wo, membership=tech, actor=tech.user, work_date=timezone.localdate(), hours="1.50",
                          description="Inspected and tested the pump")
    with pytest.raises(Conflict, match="checklist"):
        services.complete_work_order(wo, membership=tech, actor=tech.user,
                                     resolution_notes="Inspection completed and unit is safe.")
    wo = services.complete_work_order(wo, membership=tech, actor=tech.user,
        resolution_notes="Inspection completed and unit is safe.",
        checklist_items=[{"label": "Inspect unit", "required": True, "completed": True}])
    assert wo.status == WorkOrder.Status.COMPLETED
    wo = services.submit_for_review(wo, membership=tech, actor=tech.user)
    assert wo.status == WorkOrder.Status.SUPERVISOR_REVIEW
    wo = services.review_work_order(wo, membership=supervisor, actor=supervisor.user, approved=True,
                                    notes="Reviewed and approved.")
    assert wo.status == WorkOrder.Status.CLOSED
    transitions = list(wo.events.values_list("to_status", flat=True))
    assert transitions == ["DRAFT", "PLANNED", "ASSIGNED", "DISPATCHED", "IN_PROGRESS", "ON_HOLD",
                           "IN_PROGRESS", "IN_PROGRESS", "COMPLETED", "SUPERVISOR_REVIEW", "CLOSED"]
    assert WorkOrderEvent.objects.for_organization(org_a).filter(work_order=wo).count() == 11


def test_invalid_state_and_cross_tenant_site_are_rejected(org_a, site_a1, site_b1, owner_a):
    from apps.tenancy.models import Membership
    owner_membership = Membership.objects.get(organization=org_a, user=owner_a)
    wo = make_order(org_a, site_a1, owner_membership)
    with pytest.raises(InvalidTransition):
        services.dispatch_work_order(wo, membership=owner_membership, actor=owner_a)
    with pytest.raises(NotFound):
        services.create_work_order(org_a, site_id=site_b1.pk, membership=owner_membership, actor=owner_a,
                                   title="Cross tenant")


def test_assignment_rejects_overlapping_bookings(org_a, site_a1, make_member):
    planner = make_member(org_a, "planner-conflict@alpha.test", "maintenance_planner")
    tech = make_member(org_a, "tech-conflict@alpha.test", "technician")
    start = timezone.now() + timedelta(days=1)
    end = start + timedelta(hours=2)
    first = make_order(org_a, site_a1, planner, title="First")
    second = make_order(org_a, site_a1, planner, title="Second")
    services.plan_work_order(first, membership=planner, actor=planner.user, planned_start=start, planned_end=end)
    services.plan_work_order(second, membership=planner, actor=planner.user, planned_start=start, planned_end=end)
    services.assign_work_order(first, membership=planner, actor=planner.user, assignee_id=tech.pk)
    with pytest.raises(Conflict, match="conflicting"):
        services.assign_work_order(second, membership=planner, actor=planner.user, assignee_id=tech.pk)


def test_api_and_ui_are_registered(org_a, site_a1, owner_a, as_user):
    api = as_user(owner_a, org_a)
    response = api.post("/api/v1/work-orders/", {"site": str(site_a1.pk), "title": "UI/API check",
        "work_type": "PREVENTIVE", "priority": "HIGH"}, format="json")
    assert response.status_code == 201, response.data
    assert response.data["status"] == "DRAFT"
    ui = Client()
    ui.force_login(owner_a)
    assert ui.get(reverse("workorders:list")).status_code == 200


def test_detail_tabs_and_operator_boards_render_without_server_errors(org_a, site_a1, owner_a):
    from apps.tenancy.models import Membership

    membership = Membership.objects.get(organization=org_a, user=owner_a)
    wo = make_order(org_a, site_a1, membership, title="Detail tabs smoke test")
    ui = Client()
    ui.force_login(owner_a)
    assert ui.get(reverse("workorders:detail", args=[wo.pk])).status_code == 200
    for tab in ("overview", "assignment", "checklist", "parts", "labor", "notes",
                "attachments", "timeline", "closure"):
        response = ui.get(reverse("workorders:tab", args=[wo.pk, tab]))
        assert response.status_code == 200, f"{tab} returned {response.status_code}: {response.content[:500]!r}"
    assert ui.get(reverse("workorders:schedule")).status_code == 200
    assert ui.get(reverse("workorders:mine")).status_code == 200


def test_work_order_crud_edits_are_audited_but_status_is_workflow_only(org_a, site_a1, site_a2, owner_a, as_user):
    api = as_user(owner_a, org_a)
    created = api.post("/api/v1/work-orders/", {
        "site": str(site_a1.pk), "title": "Updateable work order", "work_type": "PREVENTIVE",
        "priority": "MEDIUM", "source_type": "MANUAL", "source_reference": "",
    }, format="json")
    assert created.status_code == 201, created.data

    edited = api.patch(f"/api/v1/work-orders/{created.data['id']}/", {
        "title": "Updated work order", "priority": "HIGH", "due_at": (timezone.now() + timedelta(days=1)).isoformat(),
        "site": str(site_a2.pk),
    }, format="json")
    assert edited.status_code == 200, edited.data
    assert edited.data["title"] == "Updated work order"
    assert edited.data["priority"] == "HIGH"
    assert str(edited.data["site"]) == str(site_a2.pk)
    assert edited.data["status"] == WorkOrder.Status.DRAFT
    assert WorkOrderEvent.objects.filter(work_order_id=created.data["id"], action="updated").exists()

    status_edit = api.patch(f"/api/v1/work-orders/{created.data['id']}/", {"status": "CLOSED"}, format="json")
    assert status_edit.status_code == 400
    assert WorkOrder.objects.get(pk=created.data["id"]).status == WorkOrder.Status.DRAFT

    ui = Client()
    ui.force_login(owner_a)
    assert ui.get(reverse("workorders:edit", args=[created.data["id"]])).status_code == 200
    detail = ui.get(reverse("workorders:detail", args=[created.data["id"]]))
    assert detail.status_code == 200
    assert b"Current status: Draft" in detail.content
    assert b"Next: plan the work" in detail.content


def test_api_persists_completed_before_separate_review_transition(org_a, site_a1, owner_a, make_member, as_user):
    planner = Membership.objects.get(organization=org_a, user=owner_a)
    tech = make_member(org_a, "separate-review-tech@alpha.test", "technician")
    wo = make_order(org_a, site_a1, planner, title="Separate review transition",
                    checklist_items=[{"label": "Inspect asset", "required": True, "completed": False}])
    start = timezone.now() + timedelta(days=2)
    services.plan_work_order(wo, membership=planner, actor=owner_a, planned_start=start,
                             planned_end=start + timedelta(hours=2))
    wo = services.assign_work_order(wo, membership=planner, actor=owner_a, assignee_id=tech.pk)
    wo = services.dispatch_work_order(wo, membership=planner, actor=owner_a)
    wo = services.start_work_order(wo, membership=tech, actor=tech.user)
    api = as_user(tech.user, org_a)
    completed = api.post(f"/api/v1/work-orders/{wo.pk}/complete/", {
        "resolution_notes": "Inspection completed; readings are within normal limits.",
        "checklist_items": [{"label": "Inspect asset", "required": True, "completed": True}],
    }, format="json")
    assert completed.status_code == 200, completed.data
    assert completed.data["status"] == WorkOrder.Status.COMPLETED

    ui = Client()
    ui.force_login(tech.user)
    detail = ui.get(reverse("workorders:detail", args=[wo.pk]))
    assert detail.status_code == 200
    assert b"Submit for review" in detail.content
    completion_form = ui.get(reverse("workorders:action", args=[wo.pk, "complete"]))
    assert completion_form.status_code == 200
    assert b'name="checklist_item_0"' in completion_form.content
    assert b"Checklist JSON" not in completion_form.content

    submitted = api.post(f"/api/v1/work-orders/{wo.pk}/submit-for-review/", {}, format="json")
    assert submitted.status_code == 200, submitted.data
    assert submitted.data["status"] == WorkOrder.Status.SUPERVISOR_REVIEW


def test_api_full_workflow_and_close_is_idempotent(org_a, site_a1, owner_a, make_member, as_user):
    planner = make_member(org_a, "api-flow-planner@alpha.test", "maintenance_planner")
    tech = make_member(org_a, "api-flow-tech@alpha.test", "technician")
    supervisor = make_member(org_a, "api-flow-supervisor@alpha.test", "supervisor")
    client = as_user(planner.user, org_a)

    created = client.post("/api/v1/work-orders/", {
        "site": str(site_a1.pk), "title": "API end-to-end work order", "work_type": "PREVENTIVE",
        "priority": "HIGH", "checklist_items": [{"label": "Inspect unit", "required": True}],
    }, format="json")
    assert created.status_code == 201, created.data
    wo_id = created.data["id"]
    assert client.get("/api/v1/work-orders/").status_code == 200
    assert client.get(f"/api/v1/work-orders/{wo_id}/").status_code == 200
    assert client.patch(f"/api/v1/work-orders/{wo_id}/", {"title": "API end-to-end work order updated"},
                        format="json").status_code == 200
    assert client.patch(f"/api/v1/work-orders/{wo_id}/", {"status": "CLOSED"},
                        format="json").status_code == 400

    response = client.post(f"/api/v1/work-orders/{wo_id}/checklist/", {
        "items": [{"label": "Inspect unit", "required": True}],
    }, format="json")
    assert response.status_code == 200, response.data
    start = timezone.now() + timedelta(days=4)
    response = client.post(f"/api/v1/work-orders/{wo_id}/plan/", {
        "planned_start": start.isoformat(), "planned_end": (start + timedelta(hours=2)).isoformat(),
    }, format="json")
    assert response.status_code == 200, response.data
    response = client.post(f"/api/v1/work-orders/{wo_id}/assign/", {
        "assigned_to": str(tech.pk), "expected_version": response.data["version"],
    }, format="json")
    assert response.status_code == 200, response.data
    response = client.post(f"/api/v1/work-orders/{wo_id}/dispatch/", {}, format="json")
    assert response.status_code == 200, response.data

    technician_api = as_user(tech.user, org_a)
    wrong_technician = make_member(org_a, "api-unassigned-tech@alpha.test", "technician")
    wrong_technician_api = as_user(wrong_technician.user, org_a)
    assert wrong_technician_api.post(f"/api/v1/work-orders/{wo_id}/start/", {}, format="json").status_code == 404
    response = technician_api.post(f"/api/v1/work-orders/{wo_id}/start/", {}, format="json")
    assert response.status_code == 200, response.data
    response = technician_api.post(f"/api/v1/work-orders/{wo_id}/hold/", {"reason": "Waiting for access"}, format="json")
    assert response.status_code == 200, response.data
    response = technician_api.post(f"/api/v1/work-orders/{wo_id}/resume/", {}, format="json")
    assert response.status_code == 200, response.data
    response = technician_api.post(f"/api/v1/work-orders/{wo_id}/notes/", {"text": "Inspection is underway."}, format="json")
    assert response.status_code == 200, response.data
    response = technician_api.post(f"/api/v1/work-orders/{wo_id}/labor/", {
        "work_date": timezone.localdate().isoformat(), "hours": "1.25", "description": "Inspected the unit",
    }, format="json")
    assert response.status_code == 201, response.data
    response = technician_api.post(f"/api/v1/work-orders/{wo_id}/materials/", {
        "item_name": "Filter element", "quantity": "1", "unit": "each",
    }, format="json")
    assert response.status_code == 201, response.data

    wo = WorkOrder.objects.get(pk=wo_id)
    from django.core.files.uploadedfile import SimpleUploadedFile
    from apps.files import services as file_services
    attachment = file_services.attach(SimpleUploadedFile("inspection.txt", b"checked and safe"), target=wo,
        organization=org_a, user=tech.user, description="Inspection evidence", read_permission="work_order.view")
    response = technician_api.post(f"/api/v1/work-orders/{wo_id}/evidence/", {
        "attachment_id": str(attachment.pk),
    }, format="json")
    assert response.status_code == 201, response.data
    response = technician_api.post(f"/api/v1/work-orders/{wo_id}/complete/", {
        "resolution_notes": "Inspection complete; equipment is safe and operating correctly.",
        "checklist_items": [{"label": "Inspect unit", "required": True, "completed": True}],
    }, format="json")
    assert response.status_code == 200, response.data
    response = technician_api.post(f"/api/v1/work-orders/{wo_id}/submit-for-review/", {}, format="json")
    assert response.status_code == 200, response.data
    assert technician_api.get(f"/api/v1/work-orders/{wo_id}/events/").status_code == 200

    supervisor_api = as_user(supervisor.user, org_a)
    response = supervisor_api.post(f"/api/v1/work-orders/{wo_id}/close/", {
        "comment": "Work completed and verified.",
    }, format="json")
    assert response.status_code == 200, response.data
    assert response.data["status"] == WorkOrder.Status.CLOSED
    assert response.data["closure_approvals"][0]["decision"] == ClosureApproval.Decision.APPROVED
    assert str(response.data["closed_by"]) == str(supervisor.user_id)
    repeated = supervisor_api.post(f"/api/v1/work-orders/{wo_id}/close/", {
        "comment": "Work completed and verified.",
    }, format="json")
    assert repeated.status_code == 200, repeated.data
    assert ClosureApproval.objects.filter(work_order_id=wo_id).count() == 1
    assert WorkOrderEvent.objects.filter(work_order_id=wo_id, action="closed").count() == 1
    assert AuditLog.objects.filter(organization=org_a, action="work_order.closed", target_id=str(wo_id)).exists()


def test_api_return_for_rework_requires_comment_and_notifies_assignee(org_a, site_a1, owner_a, make_member, as_user):
    planner = Membership.objects.get(organization=org_a, user=owner_a)
    tech = make_member(org_a, "api-rework-tech@alpha.test", "technician")
    supervisor = make_member(org_a, "api-rework-supervisor@alpha.test", "supervisor")
    wo = make_review_order(org_a, site_a1, planner, tech)
    client = as_user(supervisor.user, org_a)

    missing = client.post(f"/api/v1/work-orders/{wo.pk}/return-for-rework/", {"comment": "  "}, format="json")
    assert missing.status_code == 400
    returned = client.post(f"/api/v1/work-orders/{wo.pk}/return-for-rework/", {
        "comment": "Please correct the inspection notes.",
    }, format="json")
    assert returned.status_code == 200, returned.data
    assert returned.data["status"] == WorkOrder.Status.IN_PROGRESS
    assert returned.data["closure_approvals"][0]["decision"] == ClosureApproval.Decision.RETURNED
    assert Notification.objects.filter(recipient=tech.user, source="work_order",
                                       title="Work order returned for rework").exists()
    assert AuditLog.objects.filter(organization=org_a, action="work_order.review_rejected",
                                   target_id=str(wo.pk)).exists()


def test_close_permissions_invalid_state_missing_record_and_openapi(org_a, site_a1, owner_a, make_member, as_user):
    planner = Membership.objects.get(organization=org_a, user=owner_a)
    tech = make_member(org_a, "api-close-auth-tech@alpha.test", "technician")
    supervisor = make_member(org_a, "api-close-auth-supervisor@alpha.test", "supervisor")
    operations = make_member(org_a, "api-close-auth-operations@alpha.test", "operations_manager")
    draft = make_order(org_a, site_a1, planner, title="Not ready to close")
    review = make_review_order(org_a, site_a1, planner, tech)
    supervisor_api = as_user(supervisor.user, org_a)

    invalid = supervisor_api.post(f"/api/v1/work-orders/{draft.pk}/close/", {"comment": "Close"}, format="json")
    assert invalid.status_code == 409
    missing = supervisor_api.post("/api/v1/work-orders/00000000-0000-0000-0000-000000000000/close/",
                                  {"comment": "Close"}, format="json")
    assert missing.status_code == 404
    technician_api = as_user(tech.user, org_a)
    forbidden = technician_api.post(f"/api/v1/work-orders/{review.pk}/close/", {"comment": "Close"}, format="json")
    assert forbidden.status_code == 403
    operations_api = as_user(operations.user, org_a)
    assert operations_api.post(f"/api/v1/work-orders/{review.pk}/close/", {"comment": "Close"},
                               format="json").status_code == 403
    own_scope = make_member(org_a, "api-other-tech@alpha.test", "technician")
    other_api = as_user(own_scope.user, org_a)
    assert other_api.get(f"/api/v1/work-orders/{review.pk}/").status_code == 404
    assert other_api.post(f"/api/v1/work-orders/{review.pk}/start/", {}, format="json").status_code == 404
    from rest_framework.test import APIClient
    assert APIClient().get("/api/v1/work-orders/").status_code == 401

    ui = Client()
    ui.force_login(supervisor.user)
    detail = ui.get(reverse("workorders:detail", args=[review.pk]))
    assert detail.status_code == 200
    assert b"Approve and close" in detail.content
    assert b"Return for rework" in detail.content
    close_form = ui.get(reverse("workorders:action", args=[review.pk, "close"]))
    assert close_form.status_code == 200
    assert b"data-confirm=" in close_form.content

    planned = make_order(org_a, site_a1, planner, title="Supervisor assignment permissions")
    scheduled_start = timezone.now() + timedelta(days=10)
    planned = services.plan_work_order(planned, membership=planner, actor=owner_a,
        planned_start=scheduled_start, planned_end=scheduled_start + timedelta(hours=2))
    assigned = supervisor_api.post(f"/api/v1/work-orders/{planned.pk}/assign/", {
        "assigned_to": str(tech.pk), "expected_version": planned.version,
    }, format="json")
    assert assigned.status_code == 200, assigned.data
    dispatched = supervisor_api.post(f"/api/v1/work-orders/{planned.pk}/dispatch/", {}, format="json")
    assert dispatched.status_code == 200, dispatched.data
    assert technician_api.post(f"/api/v1/work-orders/{planned.pk}/start/", {}, format="json").status_code == 200
    held = supervisor_api.post(f"/api/v1/work-orders/{planned.pk}/hold/", {
        "reason": "Supervisor placed work on hold",
    }, format="json")
    assert held.status_code == 200, held.data

    schema = supervisor_api.get("/api/v1/schema/?format=json", HTTP_ACCEPT="application/json")
    assert schema.status_code == 200
    schema_data = schema.json()
    paths = schema_data["paths"]
    for suffix in ("/close/", "/return-for-rework/", "/review/", "/submit-for-review/", "/notes/"):
        assert any(path.endswith(suffix) for path in paths), suffix


def test_legacy_review_endpoint_remains_compatible(org_a, site_a1, owner_a, make_member, as_user):
    planner = Membership.objects.get(organization=org_a, user=owner_a)
    tech = make_member(org_a, "api-legacy-review-tech@alpha.test", "technician")
    supervisor = make_member(org_a, "api-legacy-review-supervisor@alpha.test", "supervisor")
    wo = make_review_order(org_a, site_a1, planner, tech)
    reviewed = as_user(supervisor.user, org_a).post(f"/api/v1/work-orders/{wo.pk}/review/", {
        "approved": False, "notes": "Please add the missing result.",
    }, format="json")
    assert reviewed.status_code == 200, reviewed.data
    assert reviewed.data["status"] == WorkOrder.Status.IN_PROGRESS
    assert ClosureApproval.objects.filter(work_order_id=wo.pk, decision=ClosureApproval.Decision.RETURNED).exists()


def test_corrective_completion_requires_shared_attachment(org_a, site_a1, make_member):
    planner = make_member(org_a, "evidence-planner@alpha.test", "maintenance_planner")
    tech = make_member(org_a, "evidence-tech@alpha.test", "technician")
    wo = services.create_work_order(org_a, site_id=site_a1.pk, membership=planner, actor=planner.user,
        title="Corrective without evidence", work_type=WorkOrder.WorkType.CORRECTIVE,
        checklist_items=[{"label": "Repair", "required": True, "completed": False}])
    start = timezone.now() + timedelta(days=5)
    wo = services.plan_work_order(wo, membership=planner, actor=planner.user, planned_start=start,
                                  planned_end=start + timedelta(hours=1))
    wo = services.assign_work_order(wo, membership=planner, actor=planner.user, assignee_id=tech.pk)
    wo = services.dispatch_work_order(wo, membership=planner, actor=planner.user)
    wo = services.start_work_order(wo, membership=tech, actor=tech.user)
    services.record_labor(wo, membership=tech, actor=tech.user, work_date=timezone.localdate(), hours="1",
                          description="Performed the corrective maintenance")
    with pytest.raises(Conflict, match="evidence"):
        services.complete_work_order(wo, membership=tech, actor=tech.user,
            resolution_notes="Corrective repair completed and function verified.",
            checklist_items=[{"label": "Repair", "required": True, "completed": True}])
