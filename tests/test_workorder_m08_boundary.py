from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.exceptions import Conflict
from apps.workorders import services
from apps.workorders.checklist_adapter import record_external_validation
from apps.workorders.models import WorkOrder

pytestmark = pytest.mark.django_db


def make_order(org, site, membership, **kwargs):
    from uuid import uuid4
    from apps.assets import services as asset_services
    from apps.assets.models import AssetCategory
    title = kwargs.pop("title", "M08 checklist adapter")
    category = AssetCategory.objects.for_organization(org).filter(name="M08 Adapter Test Assets").first()
    if category is None:
        category = asset_services.create_category(org, name="M08 Adapter Test Assets", actor=membership.user)
    asset = asset_services.create_asset(org, site=site, category=category, actor=membership.user,
        asset_tag=f"M08-{uuid4().hex[:10].upper()}", name=title)
    kwargs.setdefault("priority", WorkOrder.Priority.MEDIUM)
    kwargs.setdefault("work_type", WorkOrder.WorkType.PREVENTIVE)
    return services.create_work_order(org, site_id=site.pk, membership=membership, actor=membership.user,
        title=title, asset=asset, source_type=WorkOrder.SourceType.MANUAL, source_reference="", **kwargs)


def plan_order(order, *, membership, actor, planned_start, planned_end):
    return services.plan_work_order(order, membership=membership, actor=actor,
        planned_start=planned_start, planned_end=planned_end, due_at=planned_end + timedelta(days=1),
        estimated_hours=1, work_type=order.work_type, priority=order.priority)


def start_preventive_order(org_a, site_a1, make_member):
    planner = make_member(org_a, "m08-planner@example.test", "maintenance_planner")
    technician = make_member(org_a, "m08-tech@example.test", "technician")
    order = make_order(org_a, site_a1, planner, title="M08 adapter boundary",
                       work_type=WorkOrder.WorkType.PREVENTIVE)
    start = timezone.now() + timedelta(days=2)
    order = plan_order(order, membership=planner, actor=planner.user, planned_start=start,
                       planned_end=start + timedelta(hours=2))
    order = services.assign_work_order(order, membership=planner, actor=planner.user,
                                       assignee_id=technician.pk)
    order = services.dispatch_work_order(order, membership=planner, actor=planner.user)
    order = services.start_work_order(order, membership=technician, actor=technician.user)
    return order, planner, technician


def set_provider_result(order, status="COMPLETED", validation_state="VALID"):
    order.checklist_instance_ref = "m08-instance:demo-001"
    if status == "COMPLETED" and validation_state != "VALID":
        order.checklist_status = status
        order.checklist_validation_state = validation_state
        order.checklist_completed_at = timezone.now()
        order.checklist_validated_at = timezone.now()
        order.save(update_fields=["checklist_instance_ref", "checklist_status", "checklist_validation_state",
                                  "checklist_completed_at", "checklist_validated_at", "updated_at"])
        return
    record_external_validation(order, status=status, validation_state=validation_state,
                               completed_at=timezone.now() if status == "COMPLETED" else None)
    order.save(update_fields=["checklist_instance_ref", "checklist_status", "checklist_validation_state",
                              "checklist_completed_at", "checklist_validated_at", "updated_at"])


def test_work_type_sets_required_template_metadata(org_a, site_a1, make_member):
    planner = make_member(org_a, "m08-policy@example.test", "maintenance_planner")
    order = make_order(org_a, site_a1, planner, work_type=WorkOrder.WorkType.PREVENTIVE)
    assert order.status == WorkOrder.Status.DRAFT
    assert order.checklist_required is True
    assert order.checklist_template_ref == "M08-WO-PREVENTIVE"
    assert order.checklist_status == "PENDING"
    assert order.checklist_validation_state == "PENDING"
    assert not order.checklist_instance_ref


def test_planning_preserves_checklist_reference(org_a, site_a1, make_member):
    planner = make_member(org_a, "m08-plan@example.test", "maintenance_planner")
    order = make_order(org_a, site_a1, planner, work_type=WorkOrder.WorkType.PREVENTIVE)
    start = timezone.now() + timedelta(days=2)
    order = plan_order(order, membership=planner, actor=planner.user, planned_start=start,
                       planned_end=start + timedelta(hours=2))
    assert order.status == WorkOrder.Status.PLANNED
    assert order.checklist_template_ref == "M08-WO-PREVENTIVE"
    assert order.checklist_status == "PENDING"


def test_editing_work_type_recalculates_reference_policy(org_a, site_a1, make_member):
    planner = make_member(org_a, "m08-edit@example.test", "maintenance_planner")
    order = make_order(org_a, site_a1, planner, work_type=WorkOrder.WorkType.PREVENTIVE)
    order = services.update_work_order(order, membership=planner, actor=planner.user,
        work_type=WorkOrder.WorkType.CORRECTIVE)
    assert order.checklist_required is True
    assert order.checklist_template_ref == "M08-WO-CORRECTIVE"
    assert order.checklist_status == "PENDING"


def test_provisional_notes_never_allow_completion(org_a, site_a1, make_member):
    order, _, technician = start_preventive_order(org_a, site_a1, make_member)
    order.provisional_checklist = [{"label": "Apparently checked", "completed": True}]
    order.save(update_fields=["provisional_checklist", "updated_at"])
    with pytest.raises(Conflict, match="Checklist completion is required"):
        services.complete_work_order(order, membership=technician, actor=technician.user,
            resolution_notes="Work completed and verified successfully.")
    order.refresh_from_db()
    assert order.status == WorkOrder.Status.IN_PROGRESS
    assert not order.resolution_notes


@pytest.mark.parametrize(("status", "validation"), [("FAILED", "INVALID"), ("PENDING", "PENDING"),
                                                        ("COMPLETED", "PENDING")])
def test_invalid_provider_checklist_blocks_completion(org_a, site_a1, make_member, status, validation):
    order, _, technician = start_preventive_order(org_a, site_a1, make_member)
    set_provider_result(order, status, validation)
    with pytest.raises(Conflict, match="Checklist completion is required"):
        services.complete_work_order(order, membership=technician, actor=technician.user,
            resolution_notes="Work completed and verified successfully.")
    order.refresh_from_db()
    assert order.status == WorkOrder.Status.IN_PROGRESS


def test_valid_provider_result_allows_completion_and_closure_revalidates(org_a, site_a1, make_member):
    planner = make_member(org_a, "m08-close-planner@example.test", "maintenance_planner")
    supervisor = make_member(org_a, "m08-close-supervisor@example.test", "supervisor")
    order, _, technician = start_preventive_order(org_a, site_a1, make_member)
    set_provider_result(order)
    order = services.complete_work_order(order, membership=technician, actor=technician.user,
        resolution_notes="Work completed and verified successfully.")
    assert order.status == WorkOrder.Status.COMPLETED
    order = services.submit_for_review(order, membership=technician, actor=technician.user)
    assert order.status == WorkOrder.Status.SUPERVISOR_REVIEW

    order.checklist_status = "FAILED"
    order.checklist_validation_state = "INVALID"
    order.save(update_fields=["checklist_status", "checklist_validation_state", "updated_at"])
    with pytest.raises(Conflict, match="Checklist completion is required"):
        services.close_work_order(order, membership=supervisor, actor=supervisor.user,
                                  comment="Reviewed and approved.")
    order.refresh_from_db()
    assert order.status == WorkOrder.Status.SUPERVISOR_REVIEW


def test_no_checklist_work_type_passes_adapter(org_a, site_a1, make_member):
    from apps.workorders.checklist_adapter import get_checklist_status
    planner = make_member(org_a, "m08-none@example.test", "maintenance_planner")
    order = make_order(org_a, site_a1, planner, work_type=None)
    assert order.checklist_required is False
    assert order.checklist_status == "NOT_REQUIRED"
    assert get_checklist_status(order).is_valid is True


def test_m06_does_not_define_checklist_template_or_answer_models():
    from django.apps import apps
    assert not [model for model in apps.get_models()
                if model._meta.app_label == "workorders"
                and model.__name__ in {"ChecklistTemplate", "ChecklistQuestion", "ChecklistAnswer",
                                       "ChecklistFinding", "ChecklistVersion"}]


def test_api_reference_and_validation_endpoints_remain_pending(org_a, site_a1, make_member, as_user):
    planner = make_member(org_a, "m08-api@example.test", "maintenance_planner")
    order = make_order(org_a, site_a1, planner, work_type=WorkOrder.WorkType.PREVENTIVE)
    api = as_user(planner.user, org_a)
    status_url = f"/api/v1/work-orders/{order.pk}/checklist-status/"
    pending = api.get(status_url)
    assert pending.status_code == 200
    assert pending.data["status"] == "PENDING"
    assert pending.data["is_valid"] is False
    assert pending.data["integration_status"] == "Checklist Engine integration pending."

    reference = api.post(f"/api/v1/work-orders/{order.pk}/checklist-reference/", {
        "template_ref": "M08-WO-PREVENTIVE", "instance_ref": "m08-instance:api-01",
    }, format="json")
    assert reference.status_code == 200, reference.data
    order.refresh_from_db()
    assert order.checklist_status == "PENDING"

    validation = api.post(f"/api/v1/work-orders/{order.pk}/validate-checklist/", {}, format="json")
    assert validation.status_code == 409
    assert validation.data["is_valid"] is False
    assert validation.data["integration_status"] == "Checklist Engine integration pending."


def test_closed_checklist_references_are_read_only(org_a, site_a1, make_member):
    planner = make_member(org_a, "m08-readonly-planner@example.test", "maintenance_planner")
    supervisor = make_member(org_a, "m08-readonly-supervisor@example.test", "supervisor")
    order, _, technician = start_preventive_order(org_a, site_a1, make_member)
    set_provider_result(order)
    order = services.complete_work_order(order, membership=technician, actor=technician.user,
        resolution_notes="Work completed and verified successfully.")
    order = services.submit_for_review(order, membership=technician, actor=technician.user)
    order = services.close_work_order(order, membership=supervisor, actor=supervisor.user,
                                      comment="Approved after checklist validation.")
    with pytest.raises(Conflict, match="read-only after work is assigned"):
        services.register_checklist_reference(order, membership=planner, actor=planner.user,
            template_ref="M08-WO-PREVENTIVE", instance_ref="m08-instance:changed")
