from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core.exceptions import Conflict, InvalidTransition, NotFound, PermissionDenied, ValidationFailed
from apps.notifications.models import Notification
from apps.tenancy.models import Membership, Organization
from apps.workorders import services
from apps.workorders.forms import CreateWorkOrderForm, PlanForm
from apps.workorders.models import ClosureApproval, WorkOrder, WorkOrderAssignment, WorkOrderEvent

pytestmark = pytest.mark.django_db


def make_order(org, site, membership, *, title="Pump inspection", **kwargs):
    from apps.assets import services as asset_services
    from apps.assets.models import AssetCategory

    category = AssetCategory.objects.for_organization(org).filter(name="Work Order Test Assets").first()
    if category is None:
        category = asset_services.create_category(org, name="Work Order Test Assets", actor=membership.user)
    asset = asset_services.create_asset(org, site=site, category=category, actor=membership.user,
                                        asset_tag=f"WO-{uuid4().hex[:12].upper()}", name=title[:80])
    if kwargs.get("planned_start") and kwargs.get("planned_end"):
        kwargs.setdefault("due_at", kwargs["planned_end"] + timedelta(days=1))
        kwargs.setdefault("estimated_hours", 1)
    kwargs.setdefault("priority", WorkOrder.Priority.MEDIUM)
    kwargs.setdefault("work_type", WorkOrder.WorkType.PREVENTIVE)
    return services.create_work_order(org, site_id=site.pk, membership=membership, actor=membership.user,
                                      title=title, asset=asset, source_type=WorkOrder.SourceType.MANUAL,
                                      source_reference="", **kwargs)


def plan_order(work_order, *, membership, actor, planned_start, planned_end, **kwargs):
    """Test helper supplies valid planning inputs unless a test overrides them."""
    kwargs.setdefault("work_type", work_order.work_type or WorkOrder.WorkType.PREVENTIVE)
    kwargs.setdefault("priority", work_order.priority or WorkOrder.Priority.MEDIUM)
    kwargs.setdefault("due_at", planned_end + timedelta(days=1))
    kwargs.setdefault("estimated_hours", 1)
    return services.plan_work_order(work_order, membership=membership, actor=actor,
                                    planned_start=planned_start, planned_end=planned_end, **kwargs)


def mark_m08_checklist_valid(work_order):
    """Test-provider callback; provisional M06 notes are not used as evidence."""
    from apps.workorders.checklist_adapter import record_external_validation
    work_order.checklist_instance_ref = f"test-m08:{work_order.pk}"
    record_external_validation(work_order, status="COMPLETED", validation_state="VALID",
                               completed_at=timezone.now())
    work_order.save(update_fields=["checklist_instance_ref", "checklist_status", "checklist_validation_state",
                                   "checklist_completed_at", "checklist_validated_at", "updated_at"])
    return work_order


def attach_test_evidence(wo, organization, membership, *, filename="work-proof.txt"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.files import services as file_services

    attachment = file_services.attach(SimpleUploadedFile(filename, b"work completed and checked"), target=wo,
        organization=organization, user=membership.user, description="Work completion evidence",
        read_permission="work_order.view")
    services.add_evidence(wo, membership=membership, actor=membership.user, attachment_id=attachment.pk)


def make_review_order(org, site, planner, tech):
    wo = make_order(org, site, planner, title="API supervisor decision",
                    provisional_checklist=[{"label": "Inspect unit"}])
    start = timezone.now() + timedelta(days=3)
    wo = plan_order(wo, membership=planner, actor=planner.user, planned_start=start,
                                  planned_end=start + timedelta(hours=2))
    wo = services.assign_work_order(wo, membership=planner, actor=planner.user, assignee_id=tech.pk)
    wo = services.dispatch_work_order(wo, membership=planner, actor=planner.user)
    wo = services.start_work_order(wo, membership=tech, actor=tech.user)
    attach_test_evidence(wo, wo.organization, tech)
    services.record_labor(wo, membership=tech, actor=tech.user, work_date=timezone.localdate(), hours="1.0",
                          description="Completed the assigned inspection")
    mark_m08_checklist_valid(wo)
    wo = services.complete_work_order(wo, membership=tech, actor=tech.user,
        resolution_notes="Inspection completed and the asset is operating normally.")
    return services.submit_for_review(wo, membership=tech, actor=tech.user)


def test_full_work_order_lifecycle_is_audited(org_a, site_a1, make_member, owner_a):
    planner = make_member(org_a, "planner-wo@alpha.test", "maintenance_planner")
    tech = make_member(org_a, "technician-wo@alpha.test", "technician")
    supervisor = make_member(org_a, "supervisor-wo@alpha.test", "supervisor")
    wo = make_order(org_a, site_a1, planner, provisional_checklist=[{"label": "Inspect unit"}])
    start = timezone.now() + timedelta(hours=2)
    end = start + timedelta(hours=3)
    plan_order(wo, membership=planner, actor=planner.user, planned_start=start,
                             planned_end=end, estimated_hours=2)
    wo = services.assign_work_order(wo, membership=planner, actor=planner.user, assignee_id=tech.pk)
    wo = services.dispatch_work_order(wo, membership=planner, actor=planner.user)
    wo = services.start_work_order(wo, membership=tech, actor=tech.user)
    wo = services.hold_work_order(wo, membership=tech, actor=tech.user, reason="Waiting for safe access")
    wo = services.resume_work_order(wo, membership=tech, actor=tech.user)
    services.record_labor(wo, membership=tech, actor=tech.user, work_date=timezone.localdate(), hours="1.50",
                          description="Inspected and tested the pump")
    with pytest.raises(Conflict, match="Checklist completion is required"):
        services.complete_work_order(wo, membership=tech, actor=tech.user,
                                     resolution_notes="Inspection completed and unit is safe.")
    attach_test_evidence(wo, org_a, tech)
    mark_m08_checklist_valid(wo)
    wo = services.complete_work_order(wo, membership=tech, actor=tech.user,
        resolution_notes="Inspection completed and unit is safe.")
    assert wo.status == WorkOrder.Status.COMPLETED
    wo = services.submit_for_review(wo, membership=tech, actor=tech.user)
    assert wo.status == WorkOrder.Status.SUPERVISOR_REVIEW
    wo = services.review_work_order(wo, membership=supervisor, actor=supervisor.user, approved=True,
                                    notes="Reviewed and approved.")
    assert wo.status == WorkOrder.Status.CLOSED
    transitions = list(wo.events.values_list("to_status", flat=True))
    assert transitions == ["DRAFT", "PLANNED", "ASSIGNED", "DISPATCHED", "IN_PROGRESS", "ON_HOLD",
                           "IN_PROGRESS", "IN_PROGRESS", "IN_PROGRESS", "COMPLETED", "SUPERVISOR_REVIEW", "CLOSED"]
    assert WorkOrderEvent.objects.for_organization(org_a).filter(work_order=wo).count() == 12
    for action in ("planned", "assigned", "dispatched", "started", "held", "resumed", "completed",
                   "submitted_for_review", "closed"):
        assert AuditLog.objects.filter(organization=org_a, action=f"work_order.{action}",
                                       target_id=str(wo.pk)).exists(), action


def test_transition_table_allows_only_approved_flow_and_rework(org_a, site_a1, owner_a):
    expected = {
        WorkOrder.Status.DRAFT: {WorkOrder.Status.PLANNED},
        WorkOrder.Status.PLANNED: {WorkOrder.Status.ASSIGNED},
        WorkOrder.Status.ASSIGNED: {WorkOrder.Status.DISPATCHED},
        WorkOrder.Status.DISPATCHED: {WorkOrder.Status.IN_PROGRESS},
        WorkOrder.Status.IN_PROGRESS: {WorkOrder.Status.ON_HOLD, WorkOrder.Status.COMPLETED},
        WorkOrder.Status.ON_HOLD: {WorkOrder.Status.IN_PROGRESS},
        WorkOrder.Status.COMPLETED: {WorkOrder.Status.SUPERVISOR_REVIEW},
        WorkOrder.Status.SUPERVISOR_REVIEW: {WorkOrder.Status.CLOSED, WorkOrder.Status.IN_PROGRESS},
    }
    assert services.TRANSITIONS == expected
    assert WorkOrder.Status.CLOSED not in services.TRANSITIONS


def test_request_source_inherits_source_owned_values_and_rejects_priority_override(
        org_a, site_a1, owner_a, make_asset):
    from apps.incidents import services as incident_services
    from apps.incidents.workflow import APPROVED

    membership = Membership.objects.get(organization=org_a, user=owner_a)
    asset = make_asset(org_a, site_a1, "FORM-SOURCE-INHERIT-ASSET")
    source = _approved_source(org_a, asset, membership, kind="SERVICE_REQUEST", title="Approved repair")
    wo = incident_services.create_work_order_for_request(source, actor=owner_a, membership=membership,
        title="Repair task", description="caller value should not replace source")
    source.refresh_from_db()
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.DRAFT
    assert wo.source_request_id == source.pk
    assert wo.site_id == source.site_id and wo.asset_id == source.asset_id
    assert wo.priority == WorkOrder.Priority.HIGH
    assert wo.description == source.description
    assert source.status == "WORK_ORDER_CREATED"
    assert source.reported_by_id == membership.pk
    assert AuditLog.objects.filter(target_id=str(source.pk), action="incident.status_changed",
        metadata__action="link_work_order").exists()

    another_asset = make_asset(org_a, site_a1, "FORM-SOURCE-MISMATCH-ASSET")
    second_source = _approved_source(org_a, another_asset, membership, kind="SERVICE_REQUEST",
                                     title="Priority mismatch case")
    with pytest.raises(ValidationFailed, match="site must match"):
        services.create_work_order(org_a, site_id=uuid4(), actor=owner_a, membership=membership,
            title="Wrong source site", source_type=WorkOrder.SourceType.REQUEST,
            source_reference="", source_request=second_source, asset=another_asset)
    with pytest.raises(ValidationFailed, match="Priority must match"):
        incident_services.create_work_order_for_request(second_source, actor=owner_a, membership=membership,
            priority=WorkOrder.Priority.LOW)
    second_source.refresh_from_db()
    assert second_source.status == APPROVED
    assert not WorkOrder.objects.filter(source_request=second_source).exists()


def test_work_order_assignment_is_persisted_with_valid_schedule(org_a, site_a1, make_member, owner_a):
    planner = Membership.objects.get(organization=org_a, user=owner_a)
    technician = make_member(org_a, "assignment-record-tech@alpha.test", "technician")
    wo = make_order(org_a, site_a1, planner)
    start = timezone.now() + timedelta(days=2)
    wo = plan_order(wo, membership=planner, actor=owner_a,
        planned_start=start, planned_end=start + timedelta(hours=3))
    wo = services.assign_work_order(wo, membership=planner, actor=owner_a, assignee_id=technician.pk)
    assignment = WorkOrderAssignment.objects.get(work_order=wo)
    assert assignment.technician_id == technician.pk
    assert assignment.scheduled_start == wo.planned_start
    assert assignment.scheduled_end == wo.planned_end
    assert assignment.assigned_by_id == owner_a.pk
    with pytest.raises(InvalidTransition):
        services.assign_work_order(wo, membership=planner, actor=owner_a, assignee_id=technician.pk)


def test_assignment_can_reschedule_and_persists_the_same_window_on_order_and_assignment(
        org_a, site_a1, make_member):
    planner = make_member(org_a, "reschedule-planner@alpha.test", "maintenance_planner")
    technician = make_member(org_a, "reschedule-tech@alpha.test", "technician")
    wo = make_order(org_a, site_a1, planner, title="Reschedule at assignment")
    original_start = timezone.now() + timedelta(days=8)
    wo = plan_order(wo, membership=planner, actor=planner.user,
        planned_start=original_start, planned_end=original_start + timedelta(hours=3))
    changed_start = original_start + timedelta(hours=2)
    changed_end = changed_start + timedelta(hours=4)

    assigned = services.assign_work_order(wo, membership=planner, actor=planner.user,
        assignee_id=technician.pk, scheduled_start=changed_start, scheduled_end=changed_end)

    assignment = WorkOrderAssignment.objects.get(work_order=assigned)
    assigned.refresh_from_db()
    assert assigned.planned_start == assignment.scheduled_start == changed_start
    assert assigned.planned_end == assignment.scheduled_end == changed_end
    assert WorkOrderEvent.objects.filter(work_order=assigned, action="assigned").exists()


def test_dispatch_requires_active_assignment_record(org_a, site_a1, make_member):
    planner = make_member(org_a, "dispatch-guard-planner@alpha.test", "maintenance_planner")
    technician = make_member(org_a, "dispatch-guard-tech@alpha.test", "technician")
    wo = make_order(org_a, site_a1, planner, title="Dispatch assignment guard")
    start = timezone.now() + timedelta(days=9)
    wo = plan_order(wo, membership=planner, actor=planner.user,
        planned_start=start, planned_end=start + timedelta(hours=2))
    wo = services.assign_work_order(wo, membership=planner, actor=planner.user,
                                   assignee_id=technician.pk)
    WorkOrderAssignment.objects.filter(work_order=wo).delete()

    with pytest.raises(ValidationFailed, match="valid schedule"):
        services.dispatch_work_order(wo, membership=planner, actor=planner.user)

    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.ASSIGNED
    assert not WorkOrderEvent.objects.filter(work_order=wo, action="dispatched").exists()


def test_invalid_state_and_cross_tenant_site_are_rejected(org_a, site_a1, site_b1, owner_a):
    from apps.tenancy.models import Membership
    owner_membership = Membership.objects.get(organization=org_a, user=owner_a)
    wo = make_order(org_a, site_a1, owner_membership)
    with pytest.raises(InvalidTransition):
        services.dispatch_work_order(wo, membership=owner_membership, actor=owner_a)
    with pytest.raises(NotFound):
        services.create_work_order(org_a, site_id=site_b1.pk, membership=owner_membership, actor=owner_a,
                                   title="Cross tenant")


def test_assignment_rejects_overlapping_active_work_order(org_a, site_a1, make_member):
    planner = make_member(org_a, "planner-conflict@alpha.test", "maintenance_planner")
    tech = make_member(org_a, "tech-conflict@alpha.test", "technician")
    start = timezone.now() + timedelta(days=1)
    end = start + timedelta(hours=2)
    first = make_order(org_a, site_a1, planner, title="First")
    second = make_order(org_a, site_a1, planner, title="Second")
    plan_order(first, membership=planner, actor=planner.user, planned_start=start, planned_end=end)
    plan_order(second, membership=planner, actor=planner.user,
                             planned_start=start + timedelta(hours=1), planned_end=end + timedelta(hours=1))
    services.assign_work_order(first, membership=planner, actor=planner.user, assignee_id=tech.pk)
    with pytest.raises(Conflict, match="overlapping active assignment"):
        services.assign_work_order(second, membership=planner, actor=planner.user, assignee_id=tech.pk)


def test_api_and_ui_are_registered(org_a, site_a1, owner_a, as_user, make_asset):
    api = as_user(owner_a, org_a)
    asset = make_asset(org_a, site_a1, "API-WO-ASSET")
    planned_start = timezone.now() + timedelta(days=3)
    equal_schedule = api.post("/api/v1/work-orders/", {"site": str(site_a1.pk), "asset": str(asset.pk),
        "title": "Invalid schedule",
        "work_type": "PREVENTIVE", "priority": "HIGH", "planned_start": planned_start.isoformat(),
        "planned_end": planned_start.isoformat(), "due_at": (planned_start + timedelta(hours=1)).isoformat(),
        "estimated_hours": "1.00"}, format="json")
    assert equal_schedule.status_code == 400
    assert "planned_end" in equal_schedule.data["error"]["details"]["fields"]
    response = api.post("/api/v1/work-orders/", {"site": str(site_a1.pk), "asset": str(asset.pk),
        "title": "UI/API check",
        "work_type": "PREVENTIVE", "priority": "HIGH", "planned_start": planned_start.isoformat(),
        "planned_end": (planned_start + timedelta(hours=1)).isoformat(),
        "due_at": (planned_start + timedelta(hours=2)).isoformat(), "estimated_hours": "1.00"}, format="json")
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
    overview = ui.get(reverse("workorders:detail", args=[wo.pk]))
    assert overview.status_code == 200
    assert b"Overview" in overview.content
    assert b"Assignment and schedule" not in overview.content
    assignment = ui.get(reverse("workorders:detail", args=[wo.pk]), {"tab": "assignment"})
    assert assignment.status_code == 200
    assert b"Assignment and schedule" in assignment.content
    assert b"aria-current=\"page\"" in assignment.content
    for tab in ("overview", "assignment", "checklist", "parts", "labor", "notes",
                "attachments", "timeline", "closure"):
        response = ui.get(reverse("workorders:tab", args=[wo.pk, tab]))
        assert response.status_code == 200, f"{tab} returned {response.status_code}: {response.content[:500]!r}"
    assert ui.get(reverse("workorders:schedule")).status_code == 200
    assert ui.get(reverse("workorders:mine")).status_code == 200


def test_my_jobs_is_limited_to_the_logged_in_technician(org_a, site_a1, owner_a, make_member):
    planner = Membership.objects.get(organization=org_a, user=owner_a)
    technician = make_member(org_a, "mine-jobs-tech@alpha.test", "technician")
    another_technician = make_member(org_a, "mine-jobs-other-tech@alpha.test", "technician")
    start = timezone.now() + timedelta(days=4)
    own = make_order(org_a, site_a1, planner, title="Assigned only to signed-in technician")
    other = make_order(org_a, site_a1, planner, title="Assigned to another technician")
    for order in (own, other):
        plan_order(order, membership=planner, actor=owner_a,
            planned_start=start, planned_end=start + timedelta(hours=1))
        start += timedelta(days=1)
    services.assign_work_order(own, membership=planner, actor=owner_a, assignee_id=technician.pk)
    services.assign_work_order(other, membership=planner, actor=owner_a, assignee_id=another_technician.pk)

    ui = Client()
    ui.force_login(technician.user)
    response = ui.get(reverse("workorders:mine"))
    assert response.status_code == 200
    assert [item.pk for item in response.context["work_orders"]] == [own.pk]
    assert own.order_number.encode() in response.content
    assert other.order_number.encode() not in response.content


def test_status_cards_only_filter_scoped_work_order_list(org_a, site_a1, make_member):
    planner = make_member(org_a, "status-filter-planner@alpha.test", "maintenance_planner")
    make_order(org_a, site_a1, planner, title="Status filter draft")
    planned = make_order(org_a, site_a1, planner, title="Status filter planned",
                         priority=WorkOrder.Priority.HIGH)
    start = timezone.now() + timedelta(days=4)
    plan_order(planned, membership=planner, actor=planner.user, planned_start=start,
                             planned_end=start + timedelta(hours=1))

    ui = Client()
    ui.force_login(planner.user)
    response = ui.get(reverse("workorders:list"), {"status": WorkOrder.Status.PLANNED,
                                                   "priority": WorkOrder.Priority.HIGH})
    assert response.status_code == 200
    assert [order.pk for order in response.context["page_obj"].object_list] == [planned.pk]
    assert b'<span class="wo-flow-step">Draft</span>' in response.content
    assert b'<a class="wo-flow-step' not in response.content
    assert b'<div class="wo-status-card' in response.content
    assert b'<a class="wo-status-card' not in response.content
    assert f'href="{reverse("workorders:list")}?status=PLANNED"'.encode() not in response.content
    assert b'<option value="PLANNED" selected' in response.content
    assert b"Update status" not in response.content
    counts = {item["code"]: item["count"] for item in response.context["status_cards"]}
    assert counts[WorkOrder.Status.DRAFT] == 1
    assert counts[WorkOrder.Status.PLANNED] == 1
    assert b"Assign technician" in response.content


def test_empty_work_order_list_has_scoped_empty_state_and_zero_dashboard_counts(org_a, site_a1, owner_a):
    ui = Client()
    ui.force_login(owner_a)
    response = ui.get(reverse("workorders:list"))

    assert response.status_code == 200
    assert b"No work orders found. Create your first work order to get started." in response.content
    assert b'href="/app/work-orders/new/"' in response.content
    assert all(card["count"] == 0 for card in response.context["status_cards"])
    assert response.context["has_visible_work_orders"] is False


def test_work_order_create_updates_database_backed_dashboard_count(org_a, site_a1, owner_a):
    ui = Client()
    ui.force_login(owner_a)
    assert all(card["count"] == 0 for card in
               ui.get(reverse("workorders:list")).context["status_cards"])

    membership = Membership.objects.get(organization=org_a, user=owner_a)
    make_order(org_a, site_a1, membership, title="Database-backed dashboard row")
    response = ui.get(reverse("workorders:list"))
    counts = {card["code"]: card["count"] for card in response.context["status_cards"]}

    assert counts[WorkOrder.Status.DRAFT] == 1
    assert response.context["page_obj"].paginator.count == 1
    assert b"Database-backed dashboard row" in response.content


def test_work_order_api_get_does_not_insert_records(org_a, owner_a, as_user):
    from apps.workorders.models import WorkOrder

    client = as_user(owner_a, org_a)
    before = WorkOrder.objects.for_organization(org_a).count()
    response = client.get("/api/v1/work-orders/")

    assert response.status_code == 200
    assert WorkOrder.objects.for_organization(org_a).count() == before == 0


def test_database_transition_writes_status_history_and_invalid_transition_is_atomic(
        org_a, site_a1, owner_a):
    from apps.audit.models import AuditLog
    from apps.core.exceptions import InvalidTransition

    planner = Membership.objects.get(organization=org_a, user=owner_a)
    wo = make_order(org_a, site_a1, planner, title="Transition persists in database")
    with pytest.raises(InvalidTransition):
        services.dispatch_work_order(wo, membership=planner, actor=owner_a)
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.DRAFT
    assert not wo.events.filter(action="dispatched").exists()

    start = timezone.now() + timedelta(days=4)
    wo = plan_order(wo, membership=planner, actor=owner_a,
        planned_start=start, planned_end=start + timedelta(hours=2))
    wo.refresh_from_db()
    event = wo.events.get(action="planned")
    assert wo.status == WorkOrder.Status.PLANNED
    assert event.from_status == WorkOrder.Status.DRAFT
    assert event.to_status == WorkOrder.Status.PLANNED
    assert AuditLog.objects.filter(target_id=str(wo.pk), action="work_order.planned").exists()


def test_seed_command_is_explicit_complete_and_idempotent(tmp_path, monkeypatch):
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from django.test import override_settings


    monkeypatch.delenv("FIELDOPS_ALLOW_DEMO_SEED", raising=False)
    with override_settings(DEBUG=True, MEDIA_ROOT=tmp_path):
        call_command("seed_dev_demo", verbosity=0)
        with pytest.raises(CommandError, match="will not fabricate checklist completion"):
            call_command("seed_workorders", verbosity=0)
        organization = Organization.objects.get(name="Demo Organization")
        seeded = WorkOrder.objects.for_organization(organization)
        assert seeded.count() == 0


def test_production_work_order_page_and_seed_guard_never_insert_demo_data(
        org_a, site_a1, owner_a, monkeypatch):
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from django.test import override_settings

    monkeypatch.delenv("FIELDOPS_ALLOW_DEMO_SEED", raising=False)
    ui = Client()
    ui.force_login(owner_a)
    with override_settings(DEBUG=False):
        response = ui.get(reverse("workorders:list"))
        assert response.status_code == 200
        assert b"No work orders found. Create your first work order to get started." in response.content
        with pytest.raises(CommandError, match="Development seed blocked"):
            call_command("seed_workorders", count=9)
    assert WorkOrder.objects.for_organization(org_a).count() == 0


def test_create_work_order_form_contains_only_creation_fields(
        org_a, site_a1, make_member, make_asset):
    planner = make_member(org_a, "create-form-planner@alpha.test", "maintenance_planner")
    asset = make_asset(org_a, site_a1, "CREATE-FORM-PUMP")

    blank = CreateWorkOrderForm({}, membership=planner)
    assert not blank.is_valid()
    assert list(blank.fields["source_type"].choices)[0] == ("", "Select a source")
    assert WorkOrder.SourceType.PM in {value for value, _label in blank.fields["source_type"].choices}
    planning_fields = {"work_type", "priority", "planned_start", "planned_end", "due_at", "estimated_hours"}
    assert not planning_fields & set(blank.fields)
    for name in ("site", "asset", "component"):
        assert "form-select" in blank.fields[name].widget.attrs.get("class", "")
    assert site_a1.code in str(blank["site"])
    assert asset.asset_tag in str(blank["asset"])
    assert "Site is required." in blank.errors["site"]
    assert "Asset is required." in blank.errors["asset"]
    assert "Enter a work-order title." in blank.errors["title"]
    assert "Choose a work-order source." in blank.errors["source_type"]
    create_ui = Client()
    create_ui.force_login(planner.user)
    create_html = create_ui.get(reverse("workorders:create"))
    assert create_html.status_code == 200
    for field_name in planning_fields:
        assert f'name="{field_name}"'.encode() not in create_html.content
    for optional in ("description", "checklist"):
        assert optional not in blank.errors

    valid_data = {"source_type": WorkOrder.SourceType.MANUAL, "site": str(site_a1.pk),
                  "asset": str(asset.pk), "title": "Replace pump seal", "description": ""}
    valid = CreateWorkOrderForm(valid_data, membership=planner)
    assert valid.is_valid(), valid.errors

    for invalid_title in ("Pump 2 repair", "Pump-repair", "Pump_repair"):
        title_form = CreateWorkOrderForm({**valid_data, "title": invalid_title}, membership=planner)
        assert not title_form.is_valid()
        assert "Title can contain letters and spaces only." in title_form.errors["title"]

    alphabetic_title = CreateWorkOrderForm({**valid_data, "title": "Pump repair"}, membership=planner)
    assert alphabetic_title.is_valid(), alphabetic_title.errors

    draft_only = CreateWorkOrderForm({"source_type": WorkOrder.SourceType.MANUAL,
        "site": str(site_a1.pk), "asset": str(asset.pk), "title": "Draft without schedule"}, membership=planner)
    assert draft_only.is_valid(), draft_only.errors

def _valid_create_payload(site, asset, *, source_type="MANUAL", source_request="", **overrides):
    payload = {"source_type": source_type, "source_request": str(source_request) if source_request else "",
        "site": str(site.pk), "asset": str(asset.pk), "component": "", "title": "Create test order",
        "description": "Create form integration scenario"}
    payload.update(overrides)
    return payload


def _approved_source(org, asset, membership, *, kind, title):
    from apps.incidents import services as incident_services

    source = incident_services.create_request(org, asset=asset, reporter=membership, title=title,
        description="Approved work request for create-form coverage.", kind=kind, severity="HIGH",
        occurred_at=timezone.now() - timedelta(hours=2), actor=membership.user)
    source = incident_services.transition(source, action="triage", actor=membership.user)
    return incident_services.transition(source, action="approve", actor=membership.user)


def test_create_form_saves_manual_draft_relationships_and_audit(
        org_a, site_a1, owner_a, make_asset):
    asset = make_asset(org_a, site_a1, "FORM-MANUAL-ASSET")
    ui = Client()
    ui.force_login(owner_a)
    response = ui.post(reverse("workorders:create"), _valid_create_payload(site_a1, asset,
        work_type="CORRECTIVE", priority="HIGH", planned_start="2030-01-01T09:00",
        planned_end="2030-01-01T10:00", due_at="2030-01-01T11:00", estimated_hours="1.00"))

    assert response.status_code == 302
    wo = WorkOrder.objects.get(title="Create test order")
    wo.refresh_from_db()
    assert wo.order_number.startswith("WO-")
    assert wo.asset_id == asset.pk
    assert wo.asset_reference == asset.asset_tag
    assert wo.site_id == site_a1.pk
    assert wo.source_type == WorkOrder.SourceType.MANUAL
    assert wo.source_request_id is None
    assert wo.status == WorkOrder.Status.DRAFT
    assert wo.work_type is None and wo.priority is None
    assert wo.planned_start is None and wo.planned_end is None and wo.due_at is None
    assert wo.estimated_hours is None
    assert wo.created_by_id == owner_a.pk and wo.created_at is not None
    assert wo.events.filter(action="created", to_status=WorkOrder.Status.DRAFT).exists()
    assert AuditLog.objects.filter(target_id=str(wo.pk), action="work_order.created").exists()
    assert b"Status" not in ui.get(reverse("workorders:create")).content


@pytest.mark.parametrize("kind,source_type", [
    ("SERVICE_REQUEST", WorkOrder.SourceType.REQUEST),
    ("INCIDENT", WorkOrder.SourceType.INCIDENT),
])
def test_create_form_links_approved_request_or_incident_through_m05_flow(
        org_a, site_a1, owner_a, make_asset, kind, source_type):
    membership = Membership.objects.get(organization=org_a, user=owner_a)
    asset = make_asset(org_a, site_a1, f"FORM-{kind}-ASSET")
    source = _approved_source(org_a, asset, membership, kind=kind, title=f"Approved {kind} source")
    ui = Client()
    ui.force_login(owner_a)
    form_page = ui.get(reverse("workorders:create"))
    assert form_page.status_code == 200
    assert f"{source.number} · Approved {kind} source".encode() in form_page.content
    source_select = form_page.content.split(b'name="source_request"', 1)[1].split(b'</select>', 1)[0]
    assert b'class="form-select"' in source_select
    response = ui.post(reverse("workorders:create"), _valid_create_payload(
        site_a1, asset, source_type=source_type, source_request=source.pk))

    assert response.status_code == 302
    wo = WorkOrder.objects.get(source_request=source)
    source.refresh_from_db()
    assert wo.source_request_id == source.pk
    assert wo.source_type == source_type
    assert wo.source_reference == source.number
    assert wo.asset_id == asset.pk and wo.site_id == site_a1.pk
    assert wo.priority == WorkOrder.Priority.HIGH
    assert wo.status == WorkOrder.Status.DRAFT
    assert wo.work_type is None
    assert wo.checklist_required is False
    assert wo.checklist_template_ref == ""
    assert wo.checklist_status == "NOT_REQUIRED"
    assert source.status == "WORK_ORDER_CREATED"


def test_create_form_rejects_missing_source_pm_and_direct_status(
        org_a, site_a1, owner_a, make_asset):
    asset = make_asset(org_a, site_a1, "FORM-SOURCE-GUARD-ASSET")
    ui = Client()
    ui.force_login(owner_a)
    data = _valid_create_payload(site_a1, asset, source_type=WorkOrder.SourceType.REQUEST)
    missing = ui.post(reverse("workorders:create"), data)
    assert missing.status_code == 400
    assert b"Select a valid approved incident or service request." in missing.content
    pm = ui.post(reverse("workorders:create"), _valid_create_payload(
        site_a1, asset, source_type=WorkOrder.SourceType.PM))
    assert pm.status_code == 400
    assert b"originating preventive-maintenance schedule reference" in pm.content
    pm_with_reference = ui.post(reverse("workorders:create"), _valid_create_payload(
        site_a1, asset, source_type=WorkOrder.SourceType.PM, source_reference="PM-SCHEDULE-001"))
    assert pm_with_reference.status_code == 302
    pm_order = WorkOrder.objects.get(source_type=WorkOrder.SourceType.PM)
    assert pm_order.source_reference == "PM-SCHEDULE-001"
    assert pm_order.work_type is None
    assert pm_order.checklist_status == "NOT_REQUIRED"
    direct_status = ui.post(reverse("workorders:create"), _valid_create_payload(
        site_a1, asset, status=WorkOrder.Status.CLOSED))
    assert direct_status.status_code == 400
    assert b"Status is assigned by the workflow" in direct_status.content
    assert WorkOrder.objects.for_organization(org_a).count() == 1


def test_create_form_validates_site_asset_status_and_component_relation(
        org_a, site_a1, site_a2, owner_a, make_asset):
    from apps.assets import hierarchy as asset_hierarchy
    from apps.assets import services as asset_services

    asset = make_asset(org_a, site_a1, "FORM-ASSET-VALIDATION")
    other_site_asset = make_asset(org_a, site_a2, "FORM-ASSET-OTHER-SITE")
    parent = make_asset(org_a, site_a1, "FORM-ASSET-PARENT")
    other_parent = make_asset(org_a, site_a1, "FORM-ASSET-OTHER-PARENT")
    child = make_asset(org_a, site_a1, "FORM-ASSET-CHILD")
    link = asset_hierarchy.add_component(other_parent, child, actor=owner_a)
    ui = Client()
    ui.force_login(owner_a)

    wrong_site = ui.post(reverse("workorders:create"),
        _valid_create_payload(site_a1, other_site_asset))
    assert wrong_site.status_code == 400
    assert b"Invalid asset for the selected site." in wrong_site.content
    wrong_component = ui.post(reverse("workorders:create"),
        _valid_create_payload(site_a1, parent, component=str(link.pk)))
    assert wrong_component.status_code == 400
    assert b"Invalid component for the selected asset." in wrong_component.content

    inactive = asset_services.change_status(asset, action="start_maintenance",
        reason="Testing the inactive-asset validation", actor=owner_a)
    inactive_response = ui.post(reverse("workorders:create"), _valid_create_payload(site_a1, asset))
    assert inactive.status == "UNDER_MAINTENANCE"
    assert inactive_response.status_code == 400
    assert b"Selected asset is inactive." in inactive_response.content
    assert WorkOrder.objects.for_organization(org_a).count() == 0


def test_create_form_denies_unauthorized_users_and_reports_empty_parent_data(
        org_a, site_a1, owner_a, make_member, make_org):
    from django.test import Client

    auditor = make_member(org_a, "workorder-create-auditor@alpha.test", "auditor")
    denied = Client()
    denied.force_login(auditor.user)
    assert denied.get(reverse("workorders:create")).status_code == 403

    empty_org = make_org("Work Order Empty Setup", "empty-owner@alpha.test")
    empty_owner = Membership.objects.select_related("user").get(
        organization=empty_org, user__email="empty-owner@alpha.test").user
    ui = Client()
    ui.force_login(empty_owner)
    response = ui.get(reverse("workorders:create"))
    assert response.status_code == 200
    assert b"No permitted sites" in response.content
    assert WorkOrder.objects.for_organization(empty_org).count() == 0


def test_plan_form_requires_valid_schedule_and_due_dates():
    blank = PlanForm({})
    assert not blank.is_valid()
    assert "Enter the planned start date and time." in blank.errors["planned_start"]
    assert "Enter the planned end date and time." in blank.errors["planned_end"]
    assert "Enter the due date and time before planning this work order." in blank.errors["due_at"]

    valid = PlanForm({"work_type": "CORRECTIVE", "priority": "MEDIUM",
                      "planned_start": "2030-01-01T09:00", "planned_end": "2030-01-01T11:00",
                      "due_at": "2030-01-01T11:01", "estimated_hours": "2.00"})
    assert valid.is_valid(), valid.errors

    invalid_window = PlanForm({"work_type": "CORRECTIVE", "priority": "MEDIUM",
                               "planned_start": "2030-01-01T11:00", "planned_end": "2030-01-01T09:00",
                               "due_at": "2030-01-01T09:00", "estimated_hours": "2.00"})
    assert not invalid_window.is_valid()
    assert "Planned end must be later than planned start." in invalid_window.errors["planned_end"]
    assert "Due date must be later than planned end." in invalid_window.errors["due_at"]


def test_ui_workflow_plan_assign_dispatch_execute_and_supervisor_close(
        org_a, site_a1, make_member):
    planner = make_member(org_a, "ui-flow-planner@alpha.test", "maintenance_planner")
    technician = make_member(org_a, "ui-flow-tech@alpha.test", "technician")
    supervisor = make_member(org_a, "ui-flow-supervisor@alpha.test", "supervisor")
    wo = make_order(org_a, site_a1, planner, title="UI end-to-end workflow",
                    provisional_checklist=[{"label": "Inspect equipment"}])
    planner_ui = Client()
    planner_ui.force_login(planner.user)

    planned_start = timezone.now() + timedelta(days=5)
    planned_end = planned_start + timedelta(hours=2)
    plan_response = planner_ui.post(reverse("workorders:action", args=[wo.pk, "plan"]), {
        "work_type": "PREVENTIVE", "priority": "MEDIUM",
        "planned_start": timezone.localtime(planned_start).strftime("%Y-%m-%dT%H:%M"),
        "planned_end": timezone.localtime(planned_end).strftime("%Y-%m-%dT%H:%M"),
        "due_at": timezone.localtime(planned_end + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
        "estimated_hours": "2.00",
    })
    assert plan_response.status_code == 302
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.PLANNED

    assign_page = planner_ui.get(reverse("workorders:action", args=[wo.pk, "assign"]))
    assert assign_page.status_code == 200
    assert f'value="{wo.version}"'.encode() in assign_page.content
    assigned_response = planner_ui.post(reverse("workorders:action", args=[wo.pk, "assign"]), {
        "assigned_to": str(technician.pk), "expected_version": str(wo.version),
    })
    assert assigned_response.status_code == 302
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.ASSIGNED
    assert wo.assigned_to_id == technician.pk

    dispatch_response = planner_ui.post(reverse("workorders:action", args=[wo.pk, "dispatch"]))
    assert dispatch_response.status_code == 302
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.DISPATCHED

    technician_ui = Client()
    technician_ui.force_login(technician.user)
    assert technician_ui.post(reverse("workorders:action", args=[wo.pk, "start"])).status_code == 302
    assert technician_ui.post(reverse("workorders:action", args=[wo.pk, "hold"]), {
        "reason": "Waiting for safe site access",
    }).status_code == 302
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.ON_HOLD
    assert technician_ui.post(reverse("workorders:action", args=[wo.pk, "resume"])).status_code == 302
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.IN_PROGRESS

    labor_url = reverse("workorders:action", args=[wo.pk, "labor"])
    invalid_labor = technician_ui.post(labor_url, {
        "work_date": (timezone.localdate() + timedelta(days=1)).isoformat(),
        "hours": "0", "description": "Future labor entry",
    })
    assert invalid_labor.status_code == 400
    assert b"Work date cannot be in the future" in invalid_labor.content
    labor_response = technician_ui.post(labor_url, {
        "work_date": timezone.localdate().isoformat(), "hours": "1.50",
        "description": "Inspected equipment and verified operation",
    })
    assert labor_response.status_code == 302 and "?tab=labor" in labor_response["Location"]
    labor_tab = technician_ui.get(reverse("workorders:tab", args=[wo.pk, "labor"]))
    assert labor_tab.status_code == 200
    assert b"1.50 hours" in labor_tab.content
    material_url = reverse("workorders:action", args=[wo.pk, "materials"])
    invalid_material = technician_ui.post(material_url, {
        "item_name": "Drive belt", "part_reference": "BELT-42", "quantity": "0",
    })
    assert invalid_material.status_code == 400
    assert b"Ensure this value is greater than or equal to 0.01" in invalid_material.content
    assert b"Record each part or material used" not in invalid_material.content
    material_response = technician_ui.post(material_url, {
        "item_name": "Drive belt", "part_reference": "BELT-42", "quantity": "1.00",
        "unit": "each", "notes": "Replaced worn belt",
    })
    assert material_response.status_code == 302 and "?tab=parts" in material_response["Location"]
    parts_tab = technician_ui.get(reverse("workorders:tab", args=[wo.pk, "parts"]))
    assert parts_tab.status_code == 200
    assert b"Drive belt" in parts_tab.content and b"BELT-42" in parts_tab.content
    attach_test_evidence(wo, org_a, technician)
    mark_m08_checklist_valid(wo)
    assert technician_ui.post(reverse("workorders:action", args=[wo.pk, "complete"]), {
        "resolution_notes": "Inspection complete; equipment is operating within normal limits.",
    }).status_code == 302
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.COMPLETED
    assert wo.resolution_notes == "Inspection complete; equipment is operating within normal limits."
    assert technician_ui.post(reverse("workorders:action", args=[wo.pk, "submit_for_review"])).status_code == 302
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.SUPERVISOR_REVIEW

    supervisor_ui = Client()
    supervisor_ui.force_login(supervisor.user)
    close_page = supervisor_ui.get(reverse("workorders:action", args=[wo.pk, "close"]))
    assert close_page.status_code == 200
    closed = supervisor_ui.post(reverse("workorders:action", args=[wo.pk, "close"]), {
        "comment": "Reviewed checklist and confirmed the reported resolution.",
    })
    assert closed.status_code == 302
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.CLOSED
    assert wo.closed_by_id == supervisor.user_id


def test_ui_complete_uses_adapter_and_omits_inline_checklist_answers(org_a, site_a1, make_member):
    planner = make_member(org_a, "ui-checklist-planner@alpha.test", "maintenance_planner")
    technician = make_member(org_a, "ui-checklist-tech@alpha.test", "technician")
    wo = make_order(org_a, site_a1, planner, title="Checklist completion validation",
                    provisional_checklist=[{"label": "Inspect drive assembly"}])
    start = timezone.now() + timedelta(days=2)
    wo = plan_order(wo, membership=planner, actor=planner.user, planned_start=start,
        planned_end=start + timedelta(hours=2), due_at=start + timedelta(days=1), estimated_hours=1)
    wo = services.assign_work_order(wo, membership=planner, actor=planner.user, assignee_id=technician.pk)
    wo = services.dispatch_work_order(wo, membership=planner, actor=planner.user)
    wo = services.start_work_order(wo, membership=technician, actor=technician.user)
    attach_test_evidence(wo, org_a, technician)

    ui = Client()
    ui.force_login(technician.user)
    url = reverse("workorders:action", args=[wo.pk, "complete"])
    page = ui.get(url)
    assert page.status_code == 200
    assert b"Checklist Pending" in page.content
    assert b'name="checklist_item_0"' not in page.content

    response = ui.post(url, {"resolution_notes": "Drive inspection completed and readings recorded."})
    assert response.status_code == 400
    assert b"Checklist completion is required before completing this work order." in response.content
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.IN_PROGRESS


def test_ui_complete_shows_compact_checklist_and_evidence_states(org_a, site_a1, make_member):
    planner = make_member(org_a, "ui-empty-checklist-planner@alpha.test", "maintenance_planner")
    technician = make_member(org_a, "ui-empty-checklist-tech@alpha.test", "technician")
    wo = make_order(org_a, site_a1, planner, title="Missing completion prerequisites",
                    work_type=WorkOrder.WorkType.CORRECTIVE)
    start = timezone.now() + timedelta(days=2)
    wo = plan_order(wo, membership=planner, actor=planner.user, planned_start=start,
        planned_end=start + timedelta(hours=2), due_at=start + timedelta(days=1), estimated_hours=1)
    wo = services.assign_work_order(wo, membership=planner, actor=planner.user, assignee_id=technician.pk)
    wo = services.dispatch_work_order(wo, membership=planner, actor=planner.user)
    wo = services.start_work_order(wo, membership=technician, actor=technician.user)

    ui = Client()
    ui.force_login(technician.user)
    page = ui.get(reverse("workorders:action", args=[wo.pk, "complete"]))
    assert page.status_code == 200
    assert b"Checklist Pending" in page.content
    assert b"Evidence required" in page.content and b"Attach" in page.content
    assert b"alert-warning" not in page.content
    assert b"Complete work" in page.content
    assert b"Resolution notes are saved when completion succeeds" not in page.content

    notes = "Replaced the damaged seal and verified normal operation."
    blocked = ui.post(reverse("workorders:action", args=[wo.pk, "complete"]), {
        "resolution_notes": notes,
    })
    assert blocked.status_code == 400
    assert b"Checklist completion is required before completing this work order." in blocked.content
    assert notes.encode() in blocked.content
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.IN_PROGRESS
    assert not wo.resolution_notes

    from django.core.files.uploadedfile import SimpleUploadedFile
    evidence_url = reverse("workorders:action", args=[wo.pk, "evidence"]) + "?return_to=complete"
    evidence_page = ui.get(evidence_url)
    assert evidence_page.status_code == 200
    response = ui.post(evidence_url, {
        "file": SimpleUploadedFile("repair-proof.txt", b"Repair completed and tested."),
        "description": "Repair completion evidence", "return_to": "complete",
    })
    assert response.status_code == 302
    assert response["Location"] == reverse("workorders:action", args=[wo.pk, "complete"])
    assert wo.evidence_links.count() == 1


def test_planner_cannot_replace_m08_checklist_with_provisional_notes_after_dispatch(org_a, site_a1, make_member):
    planner = make_member(org_a, "checklist-recovery-planner@alpha.test", "maintenance_planner")
    technician = make_member(org_a, "checklist-recovery-tech@alpha.test", "technician")
    wo = make_order(org_a, site_a1, planner, title="Missing checklist recovery")
    start = timezone.now() + timedelta(days=2)
    wo = plan_order(wo, membership=planner, actor=planner.user, planned_start=start,
        planned_end=start + timedelta(hours=2), due_at=start + timedelta(days=1), estimated_hours=1)
    wo = services.assign_work_order(wo, membership=planner, actor=planner.user, assignee_id=technician.pk)
    wo = services.dispatch_work_order(wo, membership=planner, actor=planner.user)
    wo = services.start_work_order(wo, membership=technician, actor=technician.user)

    with pytest.raises(Conflict, match="only be edited before dispatch"):
        services.set_checklist(wo, membership=planner, actor=planner.user,
            items=[{"label": "Verify repaired equipment", "required": True}])
    wo.refresh_from_db()
    assert not wo.provisional_checklist


def test_schedule_board_assignment_form_updates_work_order(org_a, site_a1, make_member):
    planner = make_member(org_a, "schedule-planner@alpha.test", "maintenance_planner")
    technician = make_member(org_a, "schedule-tech@alpha.test", "technician")
    wo = make_order(org_a, site_a1, planner, title="Schedule board assignment")
    draft = make_order(org_a, site_a1, planner, title="Draft must stay off schedule")
    start = timezone.now() + timedelta(days=7)
    wo = plan_order(wo, membership=planner, actor=planner.user, planned_start=start,
                                  planned_end=start + timedelta(hours=2))
    ui = Client()
    ui.force_login(planner.user)
    board = ui.get(reverse("workorders:schedule"), {"day": timezone.localtime(start).date().isoformat()})
    assert board.status_code == 200
    assert wo.order_number.encode() in board.content
    assert draft.order_number.encode() not in board.content
    assert b"Technician schedule" in board.content
    assert b"wo-schedule-status-cards" not in board.content
    assert b"00:00" not in board.content and b"24:00" not in board.content
    assert b">Assign</button>" in board.content
    assert b"Planned work" in board.content
    assert f'name="expected_version" value="{wo.version}"'.encode() in board.content

    response = ui.post(reverse("workorders:schedule_assign", args=[wo.pk]), {
        "assigned_to": str(technician.pk), "expected_version": str(wo.version),
        "day": timezone.localtime(start).date().isoformat(),
    }, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert b'hx-swap-oob="outerHTML"' not in response.content
    assert b"Technician assigned and schedule refreshed." in response.content
    assert b"Assigned" in response.content
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.ASSIGNED
    assert wo.assigned_to_id == technician.pk
    assert WorkOrderEvent.objects.filter(work_order=wo, action="assigned").exists()

    dispatched = ui.post(reverse("workorders:schedule_dispatch", args=[wo.pk]), {
        "expected_version": str(wo.version), "day": timezone.localtime(start).date().isoformat(),
    }, HTTP_HX_REQUEST="true")
    assert dispatched.status_code == 200
    assert b'hx-swap-oob="outerHTML"' not in dispatched.content
    assert b"Work order dispatched and schedule refreshed." in dispatched.content
    assert b"Dispatched" in dispatched.content
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.DISPATCHED
    assert WorkOrderEvent.objects.filter(work_order=wo, action="dispatched").exists()


def test_schedule_board_date_scope_includes_midnight_spans_and_simple_date_filter(org_a, site_a1, make_member):
    planner = make_member(org_a, "schedule-date-planner@alpha.test", "maintenance_planner")
    selected = timezone.localdate() + timedelta(days=5)
    day_start = timezone.make_aware(timezone.datetime(selected.year, selected.month, selected.day))

    exact = make_order(org_a, site_a1, planner, title="Selected date exact")
    exact = plan_order(exact, membership=planner, actor=planner.user,
                       planned_start=day_start + timedelta(hours=10),
                       planned_end=day_start + timedelta(hours=11))
    spanning = make_order(org_a, site_a1, planner, title="Selected date overnight span")
    spanning = plan_order(spanning, membership=planner, actor=planner.user,
                          planned_start=day_start - timedelta(hours=1),
                          planned_end=day_start + timedelta(hours=1))
    previous = make_order(org_a, site_a1, planner, title="Previous date only")
    previous = plan_order(previous, membership=planner, actor=planner.user,
                          planned_start=day_start - timedelta(days=1, hours=4),
                          planned_end=day_start - timedelta(days=1, hours=3))

    ui = Client()
    ui.force_login(planner.user)
    response = ui.get(reverse("workorders:schedule"), {"day": selected.isoformat()})
    assert response.status_code == 200
    assert exact.order_number.encode() in response.content
    assert spanning.order_number.encode() in response.content
    assert previous.order_number.encode() not in response.content
    assert f'value="{selected.isoformat()}"'.encode() in response.content
    assert b"Previous day" not in response.content
    assert b"Next day" not in response.content
    assert b">Today</a>" not in response.content

    today = timezone.localdate()
    today_page = ui.get(reverse("workorders:schedule"))
    assert f'value="{today.isoformat()}"'.encode() in today_page.content
    assert f'value="{today.isoformat()}"'.encode() in today_page.content


def test_schedule_board_date_combines_with_technician_and_site_filters(
        org_a, site_a1, site_a2, make_scoped_member, make_member):
    planner = make_scoped_member(org_a, "schedule-combined-planner@alpha.test", "maintenance_planner",
                                [site_a1, site_a2])
    tech_one = make_member(org_a, "schedule-filter-tech-one@alpha.test", "technician")
    tech_two = make_member(org_a, "schedule-filter-tech-two@alpha.test", "technician")
    selected = timezone.localdate() + timedelta(days=6)
    day_start = timezone.make_aware(timezone.datetime(selected.year, selected.month, selected.day))

    first = make_order(org_a, site_a1, planner, title="Site one scheduled job")
    first = plan_order(first, membership=planner, actor=planner.user,
                       planned_start=day_start + timedelta(hours=9), planned_end=day_start + timedelta(hours=10))
    first = services.assign_work_order(first, membership=planner, actor=planner.user,
                                       assignee_id=tech_one.pk)
    second = make_order(org_a, site_a2, planner, title="Site two scheduled job")
    second = plan_order(second, membership=planner, actor=planner.user,
                        planned_start=day_start + timedelta(hours=11), planned_end=day_start + timedelta(hours=12))
    second = services.assign_work_order(second, membership=planner, actor=planner.user,
                                        assignee_id=tech_two.pk)

    ui = Client()
    ui.force_login(planner.user)
    response = ui.get(reverse("workorders:schedule"), {
        "day": selected.isoformat(), "technician": str(tech_one.pk), "site": str(site_a1.pk),
    })
    assert response.status_code == 200
    assert first.order_number.encode() in response.content
    assert second.order_number.encode() not in response.content


def test_schedule_board_date_and_overdue_filter(org_a, site_a1, make_member):
    planner = make_member(org_a, "schedule-overdue-planner@alpha.test", "maintenance_planner")
    selected = timezone.localdate() - timedelta(days=10)
    day_start = timezone.make_aware(timezone.datetime(selected.year, selected.month, selected.day))
    overdue = make_order(org_a, site_a1, planner, title="Overdue scheduled job")
    overdue = plan_order(overdue, membership=planner, actor=planner.user,
                         planned_start=day_start + timedelta(hours=9), planned_end=day_start + timedelta(hours=10),
                         due_at=day_start + timedelta(hours=11))
    not_overdue = make_order(org_a, site_a1, planner, title="Not overdue scheduled job")
    not_overdue = plan_order(not_overdue, membership=planner, actor=planner.user,
                             planned_start=day_start + timedelta(hours=12), planned_end=day_start + timedelta(hours=13),
                             due_at=timezone.now() + timedelta(days=3))

    ui = Client()
    ui.force_login(planner.user)
    response = ui.get(reverse("workorders:schedule"), {"day": selected.isoformat(), "overdue": "1"})
    assert response.status_code == 200
    assert overdue.order_number.encode() in response.content
    assert not_overdue.order_number.encode() not in response.content


def test_assignment_ui_hides_and_rejects_inactive_technician_and_stale_version(
        org_a, site_a1, make_member, owner_a):
    planner = make_member(org_a, "assignment-protection-planner@alpha.test", "maintenance_planner")
    technician = make_member(org_a, "assignment-protection-tech@alpha.test", "technician")
    busy_technician = make_member(org_a, "assignment-protection-busy@alpha.test", "technician")
    inactive = make_member(org_a, "assignment-protection-inactive@alpha.test", "technician", active=False)
    wo = make_order(org_a, site_a1, planner, title="Assignment validation")
    start = timezone.now() + timedelta(days=12)
    wo = plan_order(wo, membership=planner, actor=planner.user, planned_start=start,
                                  planned_end=start + timedelta(hours=2))
    occupied = make_order(org_a, site_a1, planner, title="Technician already booked")
    occupied = plan_order(occupied, membership=planner, actor=planner.user,
        planned_start=start + timedelta(hours=1), planned_end=start + timedelta(hours=3))
    services.assign_work_order(occupied, membership=planner, actor=planner.user,
                               assignee_id=busy_technician.pk)

    ui = Client()
    ui.force_login(planner.user)
    assign_page = ui.get(reverse("workorders:action", args=[wo.pk, "assign"]))
    assert assign_page.status_code == 200
    assert str(technician.pk).encode() in assign_page.content
    assert str(busy_technician.pk).encode() not in assign_page.content
    assert str(inactive.pk).encode() not in assign_page.content
    owner_membership = Membership.objects.get(organization=org_a, user=owner_a)
    assert str(owner_membership.pk).encode() not in assign_page.content
    with pytest.raises(ValidationFailed, match="Technician role"):
        services.assign_work_order(wo, membership=planner, actor=planner.user, assignee_id=owner_membership.pk)

    board = ui.get(reverse("workorders:schedule"), {"day": timezone.localtime(start).date().isoformat()})
    assert board.status_code == 200
    assert f'value="{technician.pk}"'.encode() in board.content
    assignment_select_start = board.content.index(f'id="technician-{wo.pk}"'.encode())
    assignment_select_end = board.content.index(b"</select>", assignment_select_start)
    assignment_select = board.content[assignment_select_start:assignment_select_end]
    assert f'value="{busy_technician.pk}"'.encode() not in assignment_select
    assert f'value="{owner_membership.pk}"'.encode() not in board.content

    inactive_post = ui.post(reverse("workorders:schedule_assign", args=[wo.pk]), {
        "assigned_to": str(inactive.pk), "expected_version": str(wo.version),
    })
    assert inactive_post.status_code == 302
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.PLANNED
    assert wo.assigned_to_id is None
    with pytest.raises(ValidationFailed, match="inactive"):
        services.assign_work_order(wo, membership=planner, actor=planner.user, assignee_id=inactive.pk)

    with pytest.raises(Conflict, match="changed"):
        services.assign_work_order(wo, membership=planner, actor=planner.user, assignee_id=technician.pk,
                                   expected_version=wo.version - 1)
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.PLANNED
    assert wo.assigned_to_id is None


def test_assignment_allows_a_distinct_non_overlapping_window(org_a, site_a1, make_member):
    planner = make_member(org_a, "sequential-jobs-planner@alpha.test", "maintenance_planner")
    technician = make_member(org_a, "sequential-jobs-tech@alpha.test", "technician")
    start = timezone.now() + timedelta(days=20)
    first = plan_order(make_order(org_a, site_a1, planner, title="Morning job"),
                       membership=planner, actor=planner.user, planned_start=start,
                       planned_end=start + timedelta(hours=2))
    services.assign_work_order(first, membership=planner, actor=planner.user,
                               assignee_id=technician.pk)
    second_start = start + timedelta(hours=3)
    second = plan_order(make_order(org_a, site_a1, planner, title="Afternoon job"),
                        membership=planner, actor=planner.user, planned_start=second_start,
                        planned_end=second_start + timedelta(hours=2))

    assigned = services.assign_work_order(second, membership=planner, actor=planner.user,
                                          assignee_id=technician.pk)
    assert assigned.status == WorkOrder.Status.ASSIGNED
    assert assigned.assigned_to_id == technician.pk


@pytest.mark.django_db(transaction=True)
def test_concurrent_assignments_allow_only_one_winner(org_a, site_a1, make_member):
    from django.db import close_old_connections, connections

    planner = make_member(org_a, "concurrent-assignment-planner@alpha.test", "maintenance_planner")
    technicians = [
        make_member(org_a, f"concurrent-assignment-tech-{index}@alpha.test", "technician")
        for index in range(2)
    ]
    wo = make_order(org_a, site_a1, planner, title="Concurrent assignment protection")
    start = timezone.now() + timedelta(days=15)
    wo = plan_order(wo, membership=planner, actor=planner.user, planned_start=start,
                                  planned_end=start + timedelta(hours=2))
    original_version = wo.version
    gate = Barrier(2)

    def assign(technician_id):
        close_old_connections()
        try:
            gate.wait(timeout=10)
            current = WorkOrder.objects.get(pk=wo.pk)
            services.assign_work_order(current, membership=planner, actor=planner.user,
                                       assignee_id=technician_id, expected_version=original_version)
            return "assigned"
        except Conflict:
            return "conflict"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(assign, [technician.pk for technician in technicians]))

    assert sorted(outcomes) == ["assigned", "conflict"]
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.ASSIGNED
    assert wo.assigned_to_id in {technician.pk for technician in technicians}
    assert WorkOrderEvent.objects.filter(work_order=wo, action="assigned").count() == 1


def test_work_order_crud_edits_are_audited_but_status_is_workflow_only(
        org_a, site_a1, site_a2, owner_a, as_user, make_asset):
    api = as_user(owner_a, org_a)
    planned_start = timezone.now() + timedelta(days=3)
    asset1 = make_asset(org_a, site_a1, "UPDATE-WO-ASSET-1")
    asset2 = make_asset(org_a, site_a2, "UPDATE-WO-ASSET-2")
    child2 = make_asset(org_a, site_a2, "UPDATE-WO-COMPONENT")
    from apps.assets import hierarchy as asset_hierarchy
    asset_hierarchy.add_component(asset2, child2, actor=owner_a)
    created = api.post("/api/v1/work-orders/", {
        "site": str(site_a1.pk), "asset": str(asset1.pk), "title": "Updateable work order", "work_type": "PREVENTIVE",
        "priority": "MEDIUM", "source_type": "MANUAL",
        "planned_start": planned_start.isoformat(), "planned_end": (planned_start + timedelta(hours=1)).isoformat(),
        "due_at": (planned_start + timedelta(hours=2)).isoformat(), "estimated_hours": "1.00",
    }, format="json")
    assert created.status_code == 201, created.data

    edited = api.patch(f"/api/v1/work-orders/{created.data['id']}/", {
        "title": "Updated work order", "priority": "HIGH", "asset": str(asset2.pk),
        "component": str(child2.pk),
        "due_at": (planned_start + timedelta(days=1)).isoformat(),
        "site": str(site_a2.pk),
    }, format="json")
    assert edited.status_code == 200, edited.data
    assert edited.data["title"] == "Updated work order"
    assert edited.data["priority"] == "HIGH"
    assert edited.data["component_reference"] == child2.asset_tag
    assert edited.data["asset"] == asset2.pk
    assert str(edited.data["site"]) == str(site_a2.pk)
    assert edited.data["status"] == WorkOrder.Status.DRAFT
    assert WorkOrderEvent.objects.filter(work_order_id=created.data["id"], action="updated").exists()

    status_edit = api.patch(f"/api/v1/work-orders/{created.data['id']}/", {"status": "CLOSED"}, format="json")
    assert status_edit.status_code == 400
    assert WorkOrder.objects.get(pk=created.data["id"]).status == WorkOrder.Status.DRAFT

    direct_model_write = WorkOrder.objects.get(pk=created.data["id"])
    direct_model_write.status = WorkOrder.Status.CLOSED
    from django.core.exceptions import ValidationError
    with pytest.raises(ValidationError, match="workflow transition service"):
        direct_model_write.save()

    ui = Client()
    ui.force_login(owner_a)
    edit_page = ui.get(reverse("workorders:edit", args=[created.data["id"]]))
    assert edit_page.status_code == 200
    assert "status" not in edit_page.context["form"].fields
    assert {"title", "description", "site", "asset",
            "component", "work_type", "priority", "planned_start", "planned_end", "due_at",
            "estimated_hours"} <= set(edit_page.context["form"].fields)
    assert "source_type" not in edit_page.context["form"].fields
    assert b">Plan<" not in edit_page.content
    detail = ui.get(reverse("workorders:detail", args=[created.data["id"]]))
    assert detail.status_code == 200
    assert b"Current status: Draft" in detail.content
    assert b"Next: plan the work" not in detail.content


def test_draft_save_and_status_scoped_edit_rules(org_a, site_a1, owner_a, make_asset, make_member, as_user):
    planner = Membership.objects.get(organization=org_a, user=owner_a)
    technician = make_member(org_a, "status-scoped-tech@alpha.test", "technician")
    asset = make_asset(org_a, site_a1, "STATUS-SCOPED-ASSET")
    ui = Client()
    ui.force_login(owner_a)
    draft_saved = ui.post(reverse("workorders:create"), {
        "source_type": "MANUAL", "site": str(site_a1.pk), "asset": str(asset.pk),
        "title": "Draft without planning",
    })
    assert draft_saved.status_code == 302
    unscheduled = WorkOrder.objects.get(title="Draft without planning")
    assert unscheduled.status == WorkOrder.Status.DRAFT
    assert unscheduled.planned_start is None and unscheduled.work_type is None
    edit_url = reverse("workorders:edit", args=[unscheduled.pk])
    edit_page = ui.get(edit_url)
    assert edit_page.status_code == 200
    assert "status" not in edit_page.context["form"].fields
    assert "source_type" not in edit_page.context["form"].fields
    assert b">Plan<" not in edit_page.content
    saved = ui.post(edit_url, {
        "site": str(site_a1.pk), "asset": str(asset.pk), "component": "",
        "title": "Updated Draft Title", "description": "Changed details",
        "work_type": WorkOrder.WorkType.CORRECTIVE, "priority": WorkOrder.Priority.HIGH,
        "planned_start": "", "planned_end": "", "due_at": "", "estimated_hours": "",
    })
    assert saved.status_code == 302
    unscheduled.refresh_from_db()
    assert unscheduled.title == "Updated Draft Title"
    assert unscheduled.priority == WorkOrder.Priority.HIGH
    assert unscheduled.status == WorkOrder.Status.DRAFT

    api = as_user(owner_a, org_a)
    created = api.post("/api/v1/work-orders/", {
        "site": str(site_a1.pk), "asset": str(asset.pk), "title": "Incomplete draft",
        "source_type": "MANUAL",
    }, format="json")
    assert created.status_code == 201, created.data
    order = WorkOrder.objects.get(pk=created.data["id"])
    assert order.status == WorkOrder.Status.DRAFT
    assert order.work_type is None and order.planned_start is None

    changed = api.patch(f"/api/v1/work-orders/{order.pk}/", {"title": "Edited draft"}, format="json")
    assert changed.status_code == 200, changed.data
    assert changed.data["status"] == WorkOrder.Status.DRAFT

    start = timezone.now() + timedelta(days=5)
    planned = plan_order(order, membership=planner, actor=owner_a, planned_start=start,
        planned_end=start + timedelta(hours=2), due_at=start + timedelta(hours=3), estimated_hours=1)
    blocked = api.patch(f"/api/v1/work-orders/{planned.pk}/", {"title": "Must remain read only"}, format="json")
    assert blocked.status_code in (400, 409)
    planned.refresh_from_db()
    assert planned.title == "Edited draft" and planned.status == WorkOrder.Status.PLANNED

    ui = Client()
    ui.force_login(owner_a)
    edit_page = ui.get(reverse("workorders:edit", args=[planned.pk]))
    assert edit_page.status_code == 200
    assert set(edit_page.context["form"].fields) == {
        "work_type", "priority", "planned_start", "planned_end", "due_at", "estimated_hours"}
    assert b">Plan<" not in edit_page.content
    planned_form_save = ui.post(reverse("workorders:edit", args=[planned.pk]), {
        "work_type": planned.work_type, "priority": planned.priority,
        "planned_start": timezone.localtime(planned.planned_start).strftime("%Y-%m-%dT%H:%M"),
        "planned_end": timezone.localtime(planned.planned_end).strftime("%Y-%m-%dT%H:%M"),
        "due_at": timezone.localtime(planned.due_at).strftime("%Y-%m-%dT%H:%M"),
        "estimated_hours": "1.50",
    })
    assert planned_form_save.status_code == 302, planned_form_save.context["form"].errors
    planned.refresh_from_db()
    assert planned.estimated_hours == Decimal("1.50")
    assert planned.status == WorkOrder.Status.PLANNED
    assert planned.events.filter(action="updated").exists()
    planned = services.assign_work_order(planned, membership=planner, actor=owner_a, assignee_id=technician.pk)
    assert [item["operation"] for item in services.allowed_actions(planned, planner)] == ["dispatch"]
    assert ui.get(reverse("workorders:edit", args=[planned.pk])).status_code == 403


def test_invalid_plan_stays_draft_and_reports_field_errors(org_a, site_a1, owner_a):
    planner = Membership.objects.get(organization=org_a, user=owner_a)
    order = make_order(org_a, site_a1, planner, title="Invalid plan dates")
    ui = Client()
    ui.force_login(owner_a)
    response = ui.post(reverse("workorders:action", args=[order.pk, "plan"]), {
        "work_type": WorkOrder.WorkType.CORRECTIVE, "priority": WorkOrder.Priority.MEDIUM,
        "planned_start": "2030-04-10T10:00", "planned_end": "2030-04-10T10:00",
        "due_at": "2030-04-10T12:00", "estimated_hours": "1.00",
    })
    assert response.status_code == 400
    assert "planned_end" in response.context["form"].errors
    order.refresh_from_db()
    assert order.status == WorkOrder.Status.DRAFT
    assert not order.events.filter(action="planned").exists()


def test_preventive_schedule_source_cannot_create_duplicate_work_orders(org_a, site_a1, owner_a, make_asset):
    asset = make_asset(org_a, site_a1, "PM-SCHEDULE-ASSET")
    kwargs = {"organization": org_a, "site_id": site_a1.pk, "asset": asset,
        "actor": owner_a, "membership": Membership.objects.get(organization=org_a, user=owner_a),
        "title": "Preventive service", "source_type": WorkOrder.SourceType.PM,
        "source_reference": "PM-SCHEDULE-ALPHA-01"}
    services.create_work_order(**kwargs)
    with pytest.raises(Conflict, match="already exists for this preventive-maintenance schedule"):
        services.create_work_order(**kwargs)


def test_role_specific_actions_and_closed_work_order_is_view_only(org_a, site_a1, make_member, owner_a):
    planner = Membership.objects.get(organization=org_a, user=owner_a)
    technician = make_member(org_a, "actions-technician@alpha.test", "technician")
    supervisor = make_member(org_a, "actions-supervisor@alpha.test", "supervisor")
    planned = make_order(org_a, site_a1, planner, title="Action matrix")
    start = timezone.now() + timedelta(days=6)
    planned = plan_order(planned, membership=planner, actor=owner_a,
        planned_start=start, planned_end=start + timedelta(hours=2))
    assert [action["operation"] for action in services.allowed_actions(planned, planner)] == ["edit", "assign"]
    planned = services.assign_work_order(planned, membership=planner, actor=owner_a, assignee_id=technician.pk)
    assert [action["operation"] for action in services.allowed_actions(planned, planner)] == ["dispatch"]
    planned = services.dispatch_work_order(planned, membership=planner, actor=owner_a)
    planned = services.start_work_order(planned, membership=technician, actor=technician.user)
    actions = [action["operation"] for action in services.allowed_actions(planned, technician)]
    assert actions == ["hold", "complete"]
    with pytest.raises(PermissionDenied):
        services.close_work_order(planned, membership=technician, actor=technician.user,
                                  comment="Unauthorized close")

    review_technician = make_member(org_a, "actions-review-technician@alpha.test", "technician")
    reviewed = make_review_order(org_a, site_a1, planner, review_technician)
    closed = services.close_work_order(reviewed, membership=supervisor, actor=supervisor.user,
                                       comment="Approved after review")
    assert services.allowed_actions(closed, supervisor) == []
    ui = Client()
    ui.force_login(supervisor.user)
    response = ui.get(reverse("workorders:list"))
    row = response.content.split(f'id="workorder-{closed.pk}"'.encode(), 1)[1].split(b"</tr>", 1)[0]
    assert b">View</a>" in row
    assert b"Edit details" not in row
    assert b"Dispatch" not in row


def test_transition_service_rejects_stale_versions_and_does_not_change_status(org_a, site_a1, owner_a):
    planner = Membership.objects.get(organization=org_a, user=owner_a)
    wo = make_order(org_a, site_a1, planner, title="Optimistic transition version")
    start = timezone.now() + timedelta(days=3)
    with pytest.raises(Conflict, match="changed since you loaded it"):
        plan_order(wo, membership=planner, actor=owner_a,
            planned_start=start, planned_end=start + timedelta(hours=2),
            due_at=start + timedelta(hours=3), expected_version=wo.version + 1)
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.DRAFT
    assert not WorkOrderEvent.objects.filter(work_order=wo, action="planned").exists()


def test_htmx_status_action_refreshes_workflow_and_rejects_stale_retry(org_a, site_a1, owner_a, make_member):
    planner = Membership.objects.get(organization=org_a, user=owner_a)
    technician = make_member(org_a, "htmx-action-tech@alpha.test", "technician")
    wo = make_order(org_a, site_a1, planner, title="HTMX dashboard transition")
    start = timezone.now() + timedelta(days=3)
    wo = plan_order(wo, membership=planner, actor=owner_a,
        planned_start=start, planned_end=start + timedelta(hours=1), due_at=start + timedelta(hours=2))
    wo = services.assign_work_order(wo, membership=planner, actor=owner_a, assignee_id=technician.pk)
    version_before = wo.version

    ui = Client()
    ui.force_login(owner_a)
    response = ui.post(reverse("workorders:action", args=[wo.pk, "dispatch"]),
        {"expected_version": str(version_before)}, HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="wo-workflow-panel")
    assert response.status_code == 200
    assert b"Current status: Dispatched" in response.content
    assert "workOrderChanged" in response.headers["HX-Trigger"]
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.DISPATCHED

    stale_retry = ui.post(reverse("workorders:action", args=[wo.pk, "dispatch"]),
        {"expected_version": str(version_before)}, HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="wo-workflow-panel")
    assert stale_retry.status_code == 200
    assert stale_retry.content == b""
    assert stale_retry.headers["HX-Reswap"] == "none"
    assert "workOrderActionError" in stale_retry.headers["HX-Trigger"]
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.DISPATCHED
    assert WorkOrderEvent.objects.filter(work_order=wo, action="dispatched").count() == 1


def test_api_persists_completed_before_separate_review_transition(org_a, site_a1, owner_a, make_member, as_user):
    planner = Membership.objects.get(organization=org_a, user=owner_a)
    tech = make_member(org_a, "separate-review-tech@alpha.test", "technician")
    wo = make_order(org_a, site_a1, planner, title="Separate review transition",
                    provisional_checklist=[{"label": "Inspect asset"}])
    start = timezone.now() + timedelta(days=2)
    plan_order(wo, membership=planner, actor=owner_a, planned_start=start,
                             planned_end=start + timedelta(hours=2))
    wo = services.assign_work_order(wo, membership=planner, actor=owner_a, assignee_id=tech.pk)
    wo = services.dispatch_work_order(wo, membership=planner, actor=owner_a)
    wo = services.start_work_order(wo, membership=tech, actor=tech.user)
    attach_test_evidence(wo, org_a, tech)
    api = as_user(tech.user, org_a)
    mark_m08_checklist_valid(wo)
    completed = api.post(f"/api/v1/work-orders/{wo.pk}/complete/", {
        "resolution_notes": "Inspection completed; readings are within normal limits.",
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
    assert b'name="checklist_item_0"' not in completion_form.content
    assert b"Checklist JSON" not in completion_form.content

    submitted = api.post(f"/api/v1/work-orders/{wo.pk}/submit-for-review/", {}, format="json")
    assert submitted.status_code == 200, submitted.data
    assert submitted.data["status"] == WorkOrder.Status.SUPERVISOR_REVIEW


def test_api_full_workflow_and_close_is_idempotent(org_a, site_a1, owner_a, make_member, as_user, make_asset):
    planner = make_member(org_a, "api-flow-planner@alpha.test", "maintenance_planner")
    tech = make_member(org_a, "api-flow-tech@alpha.test", "technician")
    supervisor = make_member(org_a, "api-flow-supervisor@alpha.test", "supervisor")
    client = as_user(planner.user, org_a)

    planned_start = timezone.now() + timedelta(days=3)
    asset = make_asset(org_a, site_a1, "API-FLOW-WO-ASSET")
    created = client.post("/api/v1/work-orders/", {
        "site": str(site_a1.pk), "asset": str(asset.pk),
        "title": "API end-to-end work order", "work_type": "PREVENTIVE",
        "priority": "HIGH",
        "planned_start": planned_start.isoformat(), "planned_end": (planned_start + timedelta(hours=1)).isoformat(),
        "due_at": (planned_start + timedelta(hours=2)).isoformat(), "estimated_hours": "1.00",
    }, format="json")
    assert created.status_code == 201, created.data
    wo_id = created.data["id"]
    assert client.get("/api/v1/work-orders/").status_code == 200
    assert client.get(f"/api/v1/work-orders/{wo_id}/").status_code == 200
    assert client.patch(f"/api/v1/work-orders/{wo_id}/", {"title": "API end-to-end work order updated"},
                        format="json").status_code == 200
    assert client.patch(f"/api/v1/work-orders/{wo_id}/", {"status": "CLOSED"},
                        format="json").status_code == 400

    response = client.post(f"/api/v1/work-orders/{wo_id}/provisional-checklist/", {
        "items": [{"label": "Inspect unit", "required": True}],
    }, format="json")
    assert response.status_code == 200, response.data
    start = timezone.now() + timedelta(days=4)
    response = client.post(f"/api/v1/work-orders/{wo_id}/plan/", {
        "work_type": "CORRECTIVE", "priority": "MEDIUM", "estimated_hours": "1.00",
        "planned_start": start.isoformat(), "planned_end": (start + timedelta(hours=2)).isoformat(),
        "due_at": (start + timedelta(hours=3)).isoformat(),
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
    mark_m08_checklist_valid(wo)
    response = technician_api.post(f"/api/v1/work-orders/{wo_id}/complete/", {
        "resolution_notes": "Inspection complete; equipment is safe and operating correctly.",
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
    planned = plan_order(planned, membership=planner, actor=owner_a,
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


def test_corrective_completion_requires_shared_attachment(org_a, site_a1, make_member, make_asset):
    planner = make_member(org_a, "evidence-planner@alpha.test", "maintenance_planner")
    tech = make_member(org_a, "evidence-tech@alpha.test", "technician")
    asset = make_asset(org_a, site_a1, "EVIDENCE-WO-ASSET")
    wo = services.create_work_order(org_a, site_id=site_a1.pk, asset=asset, membership=planner, actor=planner.user,
        title="Corrective without evidence", work_type=WorkOrder.WorkType.CORRECTIVE,
        provisional_checklist=[{"label": "Repair"}])
    start = timezone.now() + timedelta(days=5)
    wo = plan_order(wo, membership=planner, actor=planner.user, planned_start=start,
                                  planned_end=start + timedelta(hours=1))
    wo = services.assign_work_order(wo, membership=planner, actor=planner.user, assignee_id=tech.pk)
    wo = services.dispatch_work_order(wo, membership=planner, actor=planner.user)
    wo = services.start_work_order(wo, membership=tech, actor=tech.user)
    services.record_labor(wo, membership=tech, actor=tech.user, work_date=timezone.localdate(), hours="1",
                          description="Performed the corrective maintenance")
    mark_m08_checklist_valid(wo)
    with pytest.raises(Conflict, match="evidence"):
        services.complete_work_order(wo, membership=tech, actor=tech.user,
            resolution_notes="Corrective repair completed and function verified.")
