"""M06 service layer (work-order lifecycle, assignment, labor / material capture, evidence, closure rules).

Callers (API/UI) resolve objects through organization- and site-scoped selectors and check the permission for the
order's site first; this layer enforces the rest: tenant ownership of references, the state machine, assignment
validity (active technician with start rights, no overlapping commitment), the assignee-only rule for execution,
closure rules, append-only events, one audit record per change, all in one transaction.

Boundary with M05: when a work order was created from a service request, the request-side effects (start
service, resolve, rework, cancel) are applied through ``incidents.services.on_work_order_*``; M06 never edits
request rows itself. M08 checklists are consulted through ``checklist_blockers``; M09 stock through
``part_blockers`` (closure) and ``inventory.services.on_work_order_closed / _cancelled`` (D-042).
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import Conflict, PermissionDenied, ValidationFailed
from apps.core.sequences import next_number
from apps.files import services as files
from apps.files.models import Attachment
from apps.rbac import services as rbac

from .models import WorkOrder, WorkOrderEvent, WorkOrderLabor, WorkOrderMaterial
from .workflow import (
    ACTION_PERMISSIONS,
    ACTIVE_ASSIGNMENT_STATES,
    ASSIGNED,
    COMPLETED,
    DISPATCHED,
    EXECUTION_ACTIONS,
    EXECUTION_STATES,
    REASON_REQUIRED,
    SUPERVISOR_REVIEW,
    TERMINAL_STATES,
    WORK_ORDER_STATUS,
)

SNAPSHOT = ["title", "description", "work_type", "priority", "planned_start", "planned_end", "estimated_hours"]
_EDITABLE = set(SNAPSHOT)
EVIDENCE_REQUIRED_TYPES = ("CORRECTIVE", "INSTALLATION")  # D-036: completion needs >= 1 evidence file
_RECORD_STATES = EXECUTION_STATES + (COMPLETED, SUPERVISOR_REVIEW)


def _sla():
    """M11 contract (lazy import: M11 depends on the M06 models)."""
    from apps.sla import services as sla

    return sla


def _title(title: str) -> str:
    title = (title or "").strip()
    if len(title) < 3:
        raise ValidationFailed("A title of at least 3 characters is required.", code="title_required")
    return title


def _decimal(value, what: str, *, maximum: Decimal | None = None) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationFailed(f"{what} must be a number.", code="invalid_number") from exc
    if not d.is_finite() or d <= 0 or (maximum is not None and d > maximum):
        limit = f" and at most {maximum}" if maximum is not None else ""
        raise ValidationFailed(f"{what} must be greater than zero{limit}.", code="invalid_number")
    return d


def _check_window(start, end):
    if start is not None and end is not None and end < start:
        raise ValidationFailed("The planned end cannot be before the planned start.", code="plan_window_invalid")


def _event(wo, action, from_status, to_status, *, reason="", actor, assigned_to=None):
    WorkOrderEvent(organization=wo.organization, work_order=wo, action=action, from_status=from_status,
                   to_status=to_status, reason=(reason or "")[:500], actor=actor,
                   assigned_to=assigned_to).save()


# --- create / edit -------------------------------------------------------------------------------------------


@transaction.atomic
def create_work_order(org, *, asset, actor, title: str, description: str = "", work_type: str = "CORRECTIVE",
                      priority: str = "MEDIUM", source_request=None, planned_start=None, planned_end=None,
                      estimated_hours=None, request=None, source_type: str = "", source_id=None) -> WorkOrder:
    if asset.organization_id != org.pk:
        raise ValidationFailed("Asset belongs to a different organization.", code="cross_tenant_asset")
    if asset.status in ("RETIRED", "DISPOSED"):
        raise Conflict("Retired or disposed assets cannot receive work orders.", code="asset_terminal")
    if work_type not in WorkOrder.WorkType.values:
        raise ValidationFailed("Unknown work type.", code="invalid_work_type")
    if priority not in WorkOrder.Priority.values:
        raise ValidationFailed("Unknown priority.", code="invalid_priority")
    if source_request is not None:
        if source_request.organization_id != org.pk or source_request.asset_id != asset.pk:
            raise ValidationFailed("The request does not belong to this asset / organization.",
                                   code="request_mismatch")
    if source_request is not None:
        source_type, source_id = "SERVICE_REQUEST", source_request.pk
    if bool(source_type) != (source_id is not None) or source_type not in ("", *WorkOrder.SourceType.values):
        raise ValidationFailed("A work-order source needs both a type and an id.", code="invalid_source")
    _check_window(planned_start, planned_end)
    wo = WorkOrder(source_type=source_type, source_id=source_id,
        organization=org, number=next_number(org, "work_order", "WO"), title=_title(title),
        description=(description or "").strip(), work_type=work_type, priority=priority, asset=asset,
        site=asset.site, source_request=source_request, created_by=actor, planned_start=planned_start,
        planned_end=planned_end,
        estimated_hours=_decimal(estimated_hours, "Estimated hours", maximum=Decimal("9999"))
        if estimated_hours not in (None, "") else None)
    wo.save()
    _event(wo, "create", "", wo.status, actor=actor)
    _sla().on_work_order_created(wo)  # M11: work orders without a request get their own SLA
    audit.record("work_order.created", actor=actor, organization=org, target=wo,
                 after={**audit.snapshot(wo, SNAPSHOT), "number": wo.number, "asset": asset.asset_tag,
                        "site": asset.site.code, "source_request": source_request.number if source_request else None,
                        "source_type": source_type, "source_id": source_id},
                 request=request)
    return wo


@transaction.atomic
def update_work_order(wo: WorkOrder, *, actor, request=None, **changes) -> WorkOrder:
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    if wo.status not in ("DRAFT", "PLANNED"):
        raise Conflict("Only draft or planned work orders can be edited.", code="work_order_locked")
    unknown = set(changes) - _EDITABLE
    if unknown:
        raise ValidationFailed(f"Fields cannot be edited: {', '.join(sorted(unknown))}.", code="field_not_editable")
    before = audit.snapshot(wo, SNAPSHOT)
    if "title" in changes:
        wo.title = _title(changes["title"])
    if "description" in changes:
        wo.description = (changes["description"] or "").strip()
    if "work_type" in changes:
        if changes["work_type"] not in WorkOrder.WorkType.values:
            raise ValidationFailed("Unknown work type.", code="invalid_work_type")
        wo.work_type = changes["work_type"]
    if "priority" in changes:
        if changes["priority"] not in WorkOrder.Priority.values:
            raise ValidationFailed("Unknown priority.", code="invalid_priority")
        wo.priority = changes["priority"]
    if "planned_start" in changes:
        wo.planned_start = changes["planned_start"]
    if "planned_end" in changes:
        wo.planned_end = changes["planned_end"]
    if "estimated_hours" in changes:
        eh = changes["estimated_hours"]
        wo.estimated_hours = _decimal(eh, "Estimated hours", maximum=Decimal("9999")) if eh not in (None, "") else None
    _check_window(wo.planned_start, wo.planned_end)
    wo.save()
    after = audit.snapshot(wo, SNAPSHOT)
    if after != before:
        audit.record("work_order.updated", actor=actor, organization=wo.organization, target=wo, before=before,
                     after=after, request=request)
    return wo


# --- assignment rules ------------------------------------------------------------------------------------------


def check_technician(wo: WorkOrder, technician) -> None:
    """Invalid technician allocation is prevented: same organization, ACTIVE, allowed to start work at the order's
    site, and not committed to an overlapping planned window on another live work order."""
    if technician is None or technician.organization_id != wo.organization_id:
        raise ValidationFailed("Technician not found in this organization.", code="cross_tenant_technician")
    if not technician.is_active:
        raise Conflict("The technician is not active.", code="technician_inactive")
    if not rbac.has_permission(technician, "work_order.start", wo.site_id):
        raise Conflict("This user cannot execute work orders at the order's site.", code="technician_not_allowed")
    if wo.planned_start and wo.planned_end:
        clash = WorkOrder.objects.for_organization(wo.organization).filter(
            assigned_to=technician, status__in=ACTIVE_ASSIGNMENT_STATES, planned_start__lt=wo.planned_end,
            planned_end__gt=wo.planned_start).exclude(pk=wo.pk).first()
        if clash is not None:
            raise Conflict(f"The technician is already committed to {clash.number} in this time window.",
                           code="technician_conflict", details={"work_order": clash.number})


def assert_can_execute(wo: WorkOrder, membership) -> None:
    """Execution (start / hold / resume / complete) belongs to the assignee; dispatchers may act for them."""
    if wo.assigned_to_id == membership.pk:
        return
    if rbac.has_permission(membership, "work_order.dispatch", wo.site_id):
        return
    raise PermissionDenied("Only the assigned technician (or a dispatcher) can do this.", code="not_assignee")


def _can_record(wo, membership) -> bool:
    return (wo.assigned_to_id == membership.pk or rbac.has_permission(membership, "work_order.dispatch", wo.site_id)
            or rbac.has_permission(membership, "work_order.review", wo.site_id))


# --- closure rules -----------------------------------------------------------------------------------------------


def evidence_for(wo: WorkOrder):
    from django.contrib.contenttypes.models import ContentType

    return Attachment.objects.for_organization(wo.organization).filter(
        content_type=ContentType.objects.get_for_model(WorkOrder), object_id=str(wo.pk))


def closure_blockers(wo: WorkOrder) -> list[str]:
    """Why this order cannot be closed yet. Empty = closable.

    Implemented: resolution notes, at least one labor entry (time capture, D-036), for corrective / installation
    work at least one evidence file, and M08 checklist completion (``checklists.services.checklist_blockers`` reads
    the persisted inspections; no required checklist = no extra blocker) and M09 (parts issued to the order must be
    consumed or returned first; no part lines = no extra blocker)."""
    blockers = []
    if len((wo.resolution_notes or "").strip()) < 10:
        blockers.append("Resolution notes are missing.")
    if not WorkOrderLabor.objects.for_organization(wo.organization).filter(work_order=wo).exists():
        blockers.append("No labor / time has been recorded.")
    if wo.work_type in EVIDENCE_REQUIRED_TYPES and not evidence_for(wo).exists():
        blockers.append("Evidence (photo / file) is required for this type of work.")
    return blockers + checklist_blockers(wo) + part_blockers(wo)


def part_blockers(wo: WorkOrder) -> list[str]:
    """M09 contract: parts issued to the order that were neither consumed nor returned (lazy import)."""
    from apps.inventory import services as inventory

    return inventory.part_blockers(wo)


def checklist_blockers(wo: WorkOrder) -> list[str]:
    """M08 contract: required checklists that are not completed (lazy import: M08 depends on M06 models)."""
    from apps.checklists import services as checklists

    return checklists.checklist_blockers(wo)


# --- lifecycle -----------------------------------------------------------------------------------------------------


def _hook_request(wo: WorkOrder, name: str, *, actor, request):
    if wo.source_request_id:
        from apps.incidents import services as incidents

        getattr(incidents, name)(wo.source_request, actor=actor, request=request)


def _notify(wo: WorkOrder, member, title: str):
    from apps.notifications import services as notifications

    if member is not None:
        notifications.notify(wo.organization, [member.user], title=title, body=wo.title,
                             link=f"/app/work-orders/{wo.pk}/", source="workorders")


@transaction.atomic
def transition(wo: WorkOrder, *, action: str, actor, membership, reason: str = "", request=None,
               **data) -> WorkOrder:
    """Applies one lifecycle action. ``data`` carries the action's inputs (plan: planned_start / planned_end /
    estimated_hours / priority; assign: technician; complete: resolution_notes)."""
    if action not in ACTION_PERMISSIONS:
        raise ValidationFailed("Unknown action.", code="unknown_action")
    wo = WorkOrder.objects.select_related("asset", "source_request").select_for_update(of=("self",)).get(pk=wo.pk)
    WORK_ORDER_STATUS.get(wo.status, action)  # invalid transition -> 409 before anything else
    reason = (reason or "").strip()
    if action in REASON_REQUIRED and len(reason) < 3:
        raise ValidationFailed("A reason is required.", code="reason_required")
    if action in EXECUTION_ACTIONS:
        assert_can_execute(wo, membership)
    now = timezone.now()
    before = {"status": wo.status, "assigned_to": str(wo.assigned_to_id) if wo.assigned_to_id else None}

    if action == "plan":
        for key in ("planned_start", "planned_end", "estimated_hours", "priority"):
            if data.get(key) not in (None, ""):
                setattr(wo, key, _decimal(data[key], "Estimated hours", maximum=Decimal("9999"))
                        if key == "estimated_hours" else data[key])
        if wo.priority not in WorkOrder.Priority.values:
            raise ValidationFailed("Unknown priority.", code="invalid_priority")
        if not wo.planned_start or not wo.planned_end:
            raise ValidationFailed("Planned start and end are required to plan a work order.", code="plan_incomplete")
        _check_window(wo.planned_start, wo.planned_end)
    elif action == "assign":
        technician = data.get("technician")
        check_technician(wo, technician)
        wo.assigned_to = technician
    elif action == "dispatch":
        wo.dispatched_at = now
    elif action == "start":
        wo.started_at = wo.started_at or now
        wo.hold_reason = ""
    elif action == "hold":
        wo.hold_reason = reason
    elif action == "resume":
        wo.hold_reason = ""
    elif action == "complete":
        notes = (data.get("resolution_notes") or wo.resolution_notes or "").strip()
        if len(notes) < 10:
            raise ValidationFailed("Resolution notes (at least 10 characters) are required to complete the work.",
                                   code="resolution_notes_required")
        if wo.work_type in EVIDENCE_REQUIRED_TYPES and not evidence_for(wo).exists():
            raise ValidationFailed("Attach at least one photo / file as evidence before completing.",
                                   code="evidence_required")
        pending = checklist_blockers(wo)
        if pending:
            raise Conflict("Complete the required checklist(s) before completing the work: " + " ".join(pending),
                           code="checklist_incomplete", details={"blockers": pending})
        wo.resolution_notes = notes
        wo.completed_at = now
    elif action == "start_review":
        wo.review_started_at = now
    elif action == "close":
        blockers = closure_blockers(wo)
        if blockers:
            raise Conflict("The work order cannot be closed yet: " + " ".join(blockers), code="closure_blocked",
                           details={"blockers": blockers})
        wo.closed_at, wo.closed_by = now, actor
    elif action == "reject_review":
        wo.completed_at = wo.review_started_at = None

    previous, new = WORK_ORDER_STATUS.apply(wo, action)
    wo.save()
    _event(wo, action, previous, new, reason=reason, actor=actor, assigned_to=wo.assigned_to)
    _sla().on_work_order_changed(wo, previous, new)  # M11 hook (pause states, response / resolution timers)
    audit.record(
        "work_order.status_changed", actor=actor, organization=wo.organization, target=wo, before=before,
        after={"status": new, "assigned_to": str(wo.assigned_to_id) if wo.assigned_to_id else None},
        metadata={"action": action, "reason": reason, "number": wo.number}, request=request)

    if action == "assign":
        _notify(wo, wo.assigned_to, f"{wo.number} assigned to you")
    elif action == "dispatch":
        _notify(wo, wo.assigned_to, f"{wo.number} dispatched - ready to start")
    elif action == "start":
        _hook_asset(wo, "on_work_order_started", actor=actor, request=request)
        _hook_request(wo, "on_work_order_started", actor=actor, request=request)
    elif action == "complete":
        _hook_request(wo, "on_work_order_completed", actor=actor, request=request)
    elif action == "reject_review":
        _hook_request(wo, "on_work_order_reworked", actor=actor, request=request)
        _notify(wo, wo.assigned_to, f"{wo.number} returned for rework: {reason}")
    elif action == "cancel":
        _hook_request(wo, "on_work_order_cancelled", actor=actor, request=request)
        _hook_inventory(wo, "on_work_order_cancelled", actor=actor, request=request)
    elif action == "close":
        _hook_inventory(wo, "on_work_order_closed", actor=actor, request=request)
        _hook_asset(wo, "on_work_order_closed", actor=actor, request=request)
    return wo


def _hook_asset(wo: WorkOrder, name: str, *, actor, request):
    """M02 contract (D-058): the work-order lifecycle drives the asset status through the asset state machine."""
    from apps.assets import services as assets

    getattr(assets, name)(wo, actor=actor, request=request)


def _hook_inventory(wo: WorkOrder, name: str, *, actor, request):
    from apps.inventory import services as inventory

    getattr(inventory, name)(wo, actor=actor, request=request)


@transaction.atomic
def reassign(wo: WorkOrder, *, technician, reason: str, actor, request=None) -> WorkOrder:
    """Changes the technician of an ASSIGNED / DISPATCHED order (state unchanged). Caller holds work_order.assign."""
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    if wo.status not in (ASSIGNED, DISPATCHED):
        raise Conflict("Only assigned or dispatched work orders can be re-assigned.", code="invalid_transition",
                       details={"state": wo.status, "action": "reassign"})
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValidationFailed("A reason is required.", code="reason_required")
    check_technician(wo, technician)
    previous = wo.assigned_to
    wo.assigned_to = technician
    wo.save()
    _event(wo, "reassign", wo.status, wo.status, reason=reason, actor=actor, assigned_to=technician)
    audit.record("work_order.reassigned", actor=actor, organization=wo.organization, target=wo,
                 before={"assigned_to": str(previous.pk) if previous else None},
                 after={"assigned_to": str(technician.pk)}, metadata={"reason": reason}, request=request)
    _notify(wo, technician, f"{wo.number} assigned to you")
    return wo


# --- labor / material / evidence ---------------------------------------------------------------------------------------


def assert_recordable(wo: WorkOrder, membership):
    if wo.status not in _RECORD_STATES:
        raise Conflict("Labor, material and evidence are recorded while work is in progress or in review.",
                       code="work_order_not_recordable", details={"state": wo.status})
    if not _can_record(wo, membership):
        raise PermissionDenied("Only the assigned technician, a dispatcher or a reviewer can record on this order.",
                               code="not_assignee")


@transaction.atomic
def record_labor(wo: WorkOrder, *, technician, work_date, hours, notes: str = "", actor, membership,
                 request=None) -> WorkOrderLabor:
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    assert_recordable(wo, membership)
    if technician.organization_id != wo.organization_id:
        raise ValidationFailed("Technician not found in this organization.", code="cross_tenant_technician")
    if technician.pk != membership.pk and not rbac.has_permission(membership, "work_order.dispatch", wo.site_id):
        raise PermissionDenied("You can only record your own time.", code="not_self")
    if work_date > timezone.localdate() + timedelta(days=1):
        raise ValidationFailed("The work date cannot be in the future.", code="future_date")
    row = WorkOrderLabor(organization=wo.organization, work_order=wo, technician=technician, work_date=work_date,
                         hours=_decimal(hours, "Hours", maximum=Decimal("24")), notes=(notes or "").strip()[:300],
                         recorded_by=actor)
    row.save()
    audit.record("work_order.labor_recorded", actor=actor, organization=wo.organization, target=wo,
                 after={"technician": technician.user.email, "hours": str(row.hours), "date": str(work_date)},
                 request=request)
    return row


@transaction.atomic
def record_material(wo: WorkOrder, *, description: str, quantity, unit: str = "pcs", part_number: str = "",
                    actor, membership, request=None, part_line=None) -> WorkOrderMaterial:
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    assert_recordable(wo, membership)
    description = (description or "").strip()
    if not description:
        raise ValidationFailed("Describe the material used.", code="description_required")
    row = WorkOrderMaterial(organization=wo.organization, work_order=wo, description=description[:200],
                            part_number=(part_number or "").strip()[:60], unit=(unit or "pcs").strip()[:20] or "pcs",
                            quantity=_decimal(quantity, "Quantity", maximum=Decimal("9999999")), recorded_by=actor,
                            part_line=part_line)
    row.save()
    audit.record("work_order.material_recorded", actor=actor, organization=wo.organization, target=wo,
                 after={"description": row.description, "quantity": str(row.quantity), "unit": row.unit},
                 request=request)
    return row


@transaction.atomic
def add_evidence(wo: WorkOrder, uploaded, *, description: str = "", actor, membership, request=None):
    if wo.status in TERMINAL_STATES:
        raise Conflict("Evidence cannot be added to a closed or cancelled work order.", code="work_order_locked")
    if not _can_record(wo, membership) and not rbac.has_permission(membership, "work_order.update", wo.site_id):
        raise PermissionDenied("You cannot attach files to this work order.", code="not_assignee")
    att = files.attach(uploaded, target=wo, organization=wo.organization, user=actor, description=description,
                       request=request)
    audit.record("work_order.evidence_added", actor=actor, organization=wo.organization, target=wo,
                 metadata={"file": att.original_name, "attachment": str(att.pk)}, request=request)
    return att
