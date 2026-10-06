from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import Conflict, InvalidTransition, NotFound, PermissionDenied, ValidationFailed
from apps.core.sequences import next_number
from apps.files.models import Attachment
from apps.rbac import services as rbac
from apps.sites.models import Site
from apps.tenancy.models import Membership

from .models import ClosureApproval, WorkOrder, WorkOrderEvent, WorkOrderEvidence, WorkOrderLabor, WorkOrderMaterial
from .assignment_rules import ACTIVE_ASSIGNMENT_STATES, validate_assignment

TRANSITIONS = {
    WorkOrder.Status.DRAFT: {WorkOrder.Status.PLANNED, WorkOrder.Status.CANCELLED},
    WorkOrder.Status.PLANNED: {WorkOrder.Status.ASSIGNED, WorkOrder.Status.DRAFT, WorkOrder.Status.CANCELLED},
    WorkOrder.Status.ASSIGNED: {WorkOrder.Status.ASSIGNED, WorkOrder.Status.DISPATCHED, WorkOrder.Status.PLANNED,
                                 WorkOrder.Status.CANCELLED},
    WorkOrder.Status.DISPATCHED: {WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ASSIGNED, WorkOrder.Status.CANCELLED},
    WorkOrder.Status.IN_PROGRESS: {WorkOrder.Status.ON_HOLD, WorkOrder.Status.COMPLETED},
    WorkOrder.Status.ON_HOLD: {WorkOrder.Status.IN_PROGRESS},
    WorkOrder.Status.SUPERVISOR_REVIEW: {WorkOrder.Status.CLOSED, WorkOrder.Status.IN_PROGRESS},
    WorkOrder.Status.COMPLETED: {WorkOrder.Status.SUPERVISOR_REVIEW},
}
def allowed_actions(wo, membership):
    """Return the current user's actionable workflow controls for this order."""
    def permitted(code):
        return bool(membership and rbac.has_permission(membership, code, wo.site))

    is_assignee = bool(membership and wo.assigned_to_id == membership.pk)
    actions = []
    if wo.status in (WorkOrder.Status.DRAFT, WorkOrder.Status.PLANNED, WorkOrder.Status.ASSIGNED):
        if permitted("work_order.update"):
            actions.append({"operation": "edit", "label": "Edit details", "kind": "link"})
    if wo.status == WorkOrder.Status.DRAFT and permitted("work_order.plan"):
        actions.append({"operation": "plan", "label": "Plan work", "kind": "link"})
    elif wo.status == WorkOrder.Status.PLANNED and permitted("work_order.assign"):
        actions.append({"operation": "assign", "label": "Assign technician", "kind": "link"})
    elif wo.status == WorkOrder.Status.ASSIGNED and permitted("work_order.dispatch"):
        actions.append({"operation": "dispatch", "label": "Dispatch", "kind": "post",
                        "confirm": "Dispatch this work order?"})
    elif wo.status == WorkOrder.Status.DISPATCHED and is_assignee and permitted("work_order.start"):
        actions.append({"operation": "start", "label": "Start work", "kind": "post",
                        "confirm": "Start this work order?"})
    elif wo.status == WorkOrder.Status.IN_PROGRESS:
        if permitted("work_order.hold") and (is_assignee or permitted("work_order.review")):
            actions.append({"operation": "hold", "label": "Put on hold", "kind": "link"})
        if is_assignee and permitted("work_order.complete"):
            actions.append({"operation": "complete", "label": "Complete work", "kind": "link"})
    elif wo.status == WorkOrder.Status.ON_HOLD and is_assignee and permitted("work_order.start"):
        actions.append({"operation": "resume", "label": "Resume work", "kind": "post"})
    elif wo.status == WorkOrder.Status.COMPLETED and is_assignee and permitted("work_order.complete"):
        actions.append({"operation": "submit_for_review", "label": "Submit for review", "kind": "post",
                        "confirm": "Submit this completed work order for supervisor review?"})
    elif wo.status == WorkOrder.Status.SUPERVISOR_REVIEW and (
            permitted("work_order.review") or permitted("work_order.close")):
        if permitted("work_order.close"):
            actions.append({"operation": "close", "label": "Approve and close", "kind": "link",
                            "confirm": "Approve and close this work order?"})
        if permitted("work_order.review"):
            actions.append({"operation": "return_for_rework", "label": "Return for rework", "kind": "link"})
    return actions


def _need(membership, code, site):
    if (not membership or not membership.is_active or membership.organization_id != site.organization_id
            or not rbac.has_permission(membership, code, site)):
        raise PermissionDenied("You do not have permission to perform this work order action.")


def _transition(work_order, target):
    if target not in TRANSITIONS.get(work_order.status, set()):
        raise InvalidTransition(f"Cannot move work order from {work_order.status} to {target}.",
                                code="invalid_work_order_transition")


def _snapshot(wo):
    return {"status": wo.status, "title": wo.title, "description": wo.description,
            "source_type": wo.source_type, "source_reference": wo.source_reference,
            "asset_reference": wo.asset_reference, "service_request_reference": wo.service_request_reference,
            "site_id": str(wo.site_id), "work_type": wo.work_type, "priority": wo.priority,
            "due_at": wo.due_at.isoformat() if wo.due_at else None,
            "estimated_hours": str(wo.estimated_hours),
            "assigned_to": str(wo.assigned_to_id) if wo.assigned_to_id else None,
            "closed_by": str(wo.closed_by_id) if wo.closed_by_id else None, "version": wo.version,
            "planned_start": wo.planned_start.isoformat() if wo.planned_start else None,
            "planned_end": wo.planned_end.isoformat() if wo.planned_end else None}


def _record(wo, *, actor, action, before=None, notes="", metadata=None, request=None):
    now = timezone.now()
    WorkOrder.objects.filter(pk=wo.pk).update(version=F("version") + 1, updated_at=now)
    wo.version += 1
    wo.updated_at = now
    WorkOrderEvent.objects.create(organization=wo.organization, work_order=wo, actor=actor,
                                  assigned_to=wo.assigned_to, action=action,
                                  from_status=(before or {}).get("status", ""),
                                  to_status=wo.status, reason=notes, metadata=metadata or {})
    audit.record(f"work_order.{action}", actor=actor, organization=wo.organization, target=wo,
                 before=before, after=_snapshot(wo), metadata=metadata or {}, request=request)
    _notify_transition(wo, action, actor)


def _notify_transition(wo, action, actor):
    recipients = set()
    title = body = ""
    if action in ("assigned", "dispatched") and wo.assigned_to_id:
        recipients.add(wo.assigned_to.user_id)
        title = "Work order assigned" if action == "assigned" else "Work order dispatched"
        body = f"{wo.order_number}: {wo.title}"
    elif action == "held":
        recipients.add(wo.created_by_id)
        title, body = "Work order on hold", f"{wo.order_number}: {wo.hold_reason or wo.title}"
    elif action == "completed":
        title, body = "Work order awaiting review", f"{wo.order_number}: {wo.title} is completed."
        recipients.update(m.user_id for m in Membership.objects.for_organization(wo.organization).filter(
            status=Membership.Status.ACTIVE).select_related("user")
            if rbac.has_permission(m, "work_order.review", wo.site))
    elif action == "review_rejected" and wo.assigned_to_id:
        recipients.add(wo.assigned_to.user_id)
        title, body = "Work order returned for rework", f"{wo.order_number}: {wo.supervisor_notes}"
    elif action == "closed":
        recipients.add(wo.created_by_id)
        title, body = "Work order closed", f"{wo.order_number}: {wo.title} was approved and closed."
    recipients.discard(getattr(actor, "pk", None))
    if recipients:
        from apps.notifications.services import notify
        notify(wo.organization, recipients, title=title, body=body,
               link=f"/app/work-orders/{wo.pk}/", source="work_order")


def get_work_order(membership, pk):
    try:
        return WorkOrder.objects.for_organization(membership.organization).select_related(
            "site", "assigned_to__user", "created_by").get(pk=pk)
    except WorkOrder.DoesNotExist as exc:
        raise NotFound("Work order not found.") from exc


@transaction.atomic
def create_work_order(organization, *, site_id, title, actor, membership, request=None, **fields):
    site = Site.objects.for_organization(organization).filter(pk=site_id).first()
    if site is None:
        raise NotFound("Site not found.")
    _need(membership, "work_order.create", site)
    if membership.organization_id != organization.pk:
        raise NotFound("Site not found.")
    if site.status != Site.Status.ACTIVE:
        raise ValidationFailed("New work orders cannot be created for an inactive site.", code="site_inactive")
    title = (title or "").strip()
    if not title:
        raise ValidationFailed("A work order title is required.")
    source_type = fields.get("source_type", WorkOrder.SourceType.MANUAL)
    source_reference = (fields.get("source_reference") or "").strip()
    if source_type == WorkOrder.SourceType.MANUAL and fields.get("service_request_reference") and not source_reference:
        source_type = WorkOrder.SourceType.REQUEST
        source_reference = fields["service_request_reference"].strip()
        fields["source_type"] = source_type
    if source_type != WorkOrder.SourceType.MANUAL and not source_reference:
        raise ValidationFailed("Add the reference for the selected work-order source.", code="source_reference_required")
    fields["source_reference"] = source_reference
    if source_type == WorkOrder.SourceType.REQUEST:
        fields["service_request_reference"] = source_reference
    start, end = fields.get("planned_start"), fields.get("planned_end")
    if start and end and end <= start:
        raise ValidationFailed("Planned end must be after planned start.")
    due_at = fields.get("due_at")
    if due_at and start and due_at < start:
        raise ValidationFailed("Due date cannot be before the planned start.", code="due_before_start")
    checklist = fields.get("checklist_items", [])
    if not isinstance(checklist, list) or any(not isinstance(i, dict) or not str(i.get("label", "")).strip()
                                              for i in checklist):
        raise ValidationFailed("Checklist entries require a label.")
    wo = WorkOrder(organization=organization, site=site,
                   order_number=next_number(organization, "work_order", "WO"),
                   title=title, created_by=actor, **fields)
    try:
        wo.full_clean()
        wo.save()
    except ValidationError as exc:
        raise ValidationFailed("Work order is invalid.", details={"fields": exc.message_dict if hasattr(exc, "message_dict") else exc.messages}) from exc
    _record(wo, actor=actor, action="created", request=request)
    return wo


@transaction.atomic
def update_work_order(wo, *, membership, actor, request=None, **values):
    """Edit mutable work-order details; lifecycle status is changed only by workflow services."""
    # Lock only the work-order row: assigned_to is nullable, and PostgreSQL
    # rejects FOR UPDATE on the nullable side of a select_related join.
    wo = WorkOrder.objects.select_for_update(of=("self",)).select_related("site", "assigned_to").get(pk=wo.pk)
    _need(membership, "work_order.update", wo.site)
    if wo.status not in (WorkOrder.Status.DRAFT, WorkOrder.Status.PLANNED, WorkOrder.Status.ASSIGNED):
        raise Conflict("Work-order details can only be edited before dispatch.", code="work_order_locked")
    unknown = set(values) - {
        "title", "description", "source_type", "source_reference", "asset_reference",
        "service_request_reference", "work_type", "priority", "planned_start", "planned_end",
        "due_at", "estimated_hours", "site",
    }
    if unknown:
        raise ValidationFailed("Unsupported work-order fields.", details={"fields": sorted(unknown)})
    if "service_request_reference" in values and "source_reference" not in values and "source_type" not in values:
        legacy_reference = (values["service_request_reference"] or "").strip()
        values["source_type"] = WorkOrder.SourceType.REQUEST if legacy_reference else WorkOrder.SourceType.MANUAL
        values["source_reference"] = legacy_reference
    if "site" in values:
        new_site = values["site"]
        if new_site.organization_id != wo.organization_id or new_site.status != Site.Status.ACTIVE:
            raise NotFound("Site not found.")
        _need(membership, "work_order.update", new_site)
        if wo.status != WorkOrder.Status.DRAFT and new_site.pk != wo.site_id:
            raise Conflict("The site cannot be changed after the work order is planned.", code="site_locked")
        wo.site = new_site
    for name, value in values.items():
        if name != "site":
            setattr(wo, name, value)
    wo.source_reference = (wo.source_reference or "").strip()
    if wo.source_type != WorkOrder.SourceType.MANUAL and not wo.source_reference:
        raise ValidationFailed("Add the reference for the selected work-order source.", code="source_reference_required")
    wo.service_request_reference = wo.source_reference if wo.source_type == WorkOrder.SourceType.REQUEST else ""
    wo.title = (wo.title or "").strip()
    if not wo.title:
        raise ValidationFailed("A work order title is required.")
    if wo.planned_start and wo.planned_end and wo.planned_end <= wo.planned_start:
        raise ValidationFailed("Planned end must be after planned start.")
    if wo.due_at and wo.planned_start and wo.due_at < wo.planned_start:
        raise ValidationFailed("Due date cannot be before the planned start.", code="due_before_start")
    if wo.estimated_hours < 0:
        raise ValidationFailed("Estimated hours cannot be negative.")
    if wo.assigned_to_id and wo.planned_start and wo.planned_end:
        conflict = WorkOrder.objects.for_organization(wo.organization).filter(
            assigned_to=wo.assigned_to, status__in=ACTIVE_ASSIGNMENT_STATES,
            planned_start__lt=wo.planned_end, planned_end__gt=wo.planned_start,
        ).exclude(pk=wo.pk).exists()
        if conflict:
            raise Conflict("Technician already has a conflicting work order in this time window.",
                           code="technician_conflict")
    before = _snapshot(WorkOrder.objects.select_related("site").get(pk=wo.pk))
    try:
        wo.full_clean()
        wo.save()
    except ValidationError as exc:
        raise ValidationFailed("Work order is invalid.", details={"fields": exc.message_dict if hasattr(exc, "message_dict") else exc.messages}) from exc
    _record(wo, actor=actor, action="updated", before=before, request=request)
    return wo


@transaction.atomic
def plan_work_order(wo, *, membership, actor, planned_start, planned_end, estimated_hours=None, request=None):
    _need(membership, "work_order.plan", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    _transition(wo, WorkOrder.Status.PLANNED)
    if not planned_start or not planned_end or planned_end <= planned_start:
        raise ValidationFailed("Planned end must be after planned start.")
    if wo.due_at and wo.due_at < planned_start:
        raise ValidationFailed("Due date cannot be before the planned start.", code="due_before_start")
    if estimated_hours is not None and Decimal(estimated_hours) < 0:
        raise ValidationFailed("Estimated hours cannot be negative.")
    before = _snapshot(wo)
    wo.status, wo.planned_start, wo.planned_end = WorkOrder.Status.PLANNED, planned_start, planned_end
    if estimated_hours is not None:
        wo.estimated_hours = estimated_hours
    wo.save()
    _record(wo, actor=actor, action="planned", before=before, request=request)
    return wo


@transaction.atomic
def assign_work_order(wo, *, membership, actor, assignee_id, expected_version=None, request=None):
    _need(membership, "work_order.assign", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    if expected_version is not None and expected_version != wo.version:
        raise Conflict("This work order changed since you loaded it. Refresh and retry the assignment.",
                       code="work_order_version_conflict")
    if wo.status != WorkOrder.Status.ASSIGNED:
        _transition(wo, WorkOrder.Status.ASSIGNED)
    assigned = Membership.objects.select_for_update().select_related("user").filter(
        organization=wo.organization, pk=assignee_id).first()
    if assigned is None:
        raise ValidationFailed("Technician was not found in this organization.", code="invalid_technician")
    warnings = validate_assignment(wo, assigned)
    before = _snapshot(wo)
    wo.status, wo.assigned_to = WorkOrder.Status.ASSIGNED, assigned
    wo.save(update_fields=["status", "assigned_to", "updated_at"])
    _record(wo, actor=actor, action="assigned", before=before,
            metadata={"membership_id": str(assigned.pk), "warnings": warnings}, request=request)
    return wo


@transaction.atomic
def dispatch_work_order(wo, *, membership, actor, request=None):
    _need(membership, "work_order.dispatch", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    _transition(wo, WorkOrder.Status.DISPATCHED)
    if not wo.assigned_to_id:
        raise ValidationFailed("Assign a technician before dispatch.")
    before = _snapshot(wo)
    wo.status, wo.dispatched_at = WorkOrder.Status.DISPATCHED, timezone.now()
    wo.save(update_fields=["status", "dispatched_at", "updated_at"])
    _record(wo, actor=actor, action="dispatched", before=before, request=request)
    return wo


@transaction.atomic
def start_work_order(wo, *, membership, actor, request=None):
    _need(membership, "work_order.start", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    _transition(wo, WorkOrder.Status.IN_PROGRESS)
    if wo.assigned_to_id != membership.pk:
        raise PermissionDenied("Only the assigned technician may start this work order.")
    before = _snapshot(wo)
    wo.status, wo.started_at = WorkOrder.Status.IN_PROGRESS, timezone.now()
    wo.save(update_fields=["status", "started_at", "updated_at"])
    _record(wo, actor=actor, action="started", before=before, request=request)
    return wo


def _assert_assignee_or_dispatcher(wo, membership):
    if wo.assigned_to_id != membership.pk and not rbac.has_permission(membership, "work_order.dispatch", wo.site):
        raise PermissionDenied("Only the assigned technician or an authorized dispatcher may execute this work order.")


def _assert_assignee_or_authorized(wo, membership, permission):
    if (wo.assigned_to_id != membership.pk and
            not rbac.has_permission(membership, permission, wo.site)):
        raise PermissionDenied("Only the assigned technician or an authorized supervisor may perform this action.")


@transaction.atomic
def hold_work_order(wo, *, membership, actor, reason, request=None):
    _need(membership, "work_order.hold", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    _transition(wo, WorkOrder.Status.ON_HOLD)
    _assert_assignee_or_authorized(wo, membership, "work_order.hold")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationFailed("A reason is required to put a work order on hold.")
    before = _snapshot(wo)
    wo.status, wo.hold_reason = WorkOrder.Status.ON_HOLD, reason
    wo.save(update_fields=["status", "hold_reason", "updated_at"])
    _record(wo, actor=actor, action="held", before=before, notes=reason, request=request)
    return wo


@transaction.atomic
def resume_work_order(wo, *, membership, actor, request=None):
    _need(membership, "work_order.start", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    _transition(wo, WorkOrder.Status.IN_PROGRESS)
    _assert_assignee_or_authorized(wo, membership, "work_order.start")
    before = _snapshot(wo)
    wo.status, wo.hold_reason = WorkOrder.Status.IN_PROGRESS, ""
    wo.save(update_fields=["status", "hold_reason", "updated_at"])
    _record(wo, actor=actor, action="resumed", before=before, request=request)
    return wo


def _check_completion(wo, notes):
    if len((notes or "").strip()) < 10:
        raise ValidationFailed("Resolution notes must contain at least 10 characters.", code="resolution_notes_required")
    if not isinstance(wo.checklist_items, list) or any(not isinstance(item, dict) for item in wo.checklist_items):
        raise Conflict("Work order checklist data is invalid.", code="invalid_checklist")
    required = [item for item in wo.checklist_items if item.get("required", True)]
    if not required or any(not item.get("completed") for item in required):
        raise Conflict("Complete all required checklist items before submitting for review.",
                       code="checklist_incomplete")
    if wo.work_type in (WorkOrder.WorkType.CORRECTIVE, WorkOrder.WorkType.INSTALLATION) and not wo.completion_evidence.exists():
        raise Conflict("Attach completion evidence for this work type.", code="evidence_required")


@transaction.atomic
def complete_work_order(wo, *, membership, actor, resolution_notes, checklist_items=None, request=None):
    _need(membership, "work_order.complete", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    _transition(wo, WorkOrder.Status.COMPLETED)
    if wo.assigned_to_id != membership.pk:
        raise PermissionDenied("Only the assigned technician may complete this work order.")
    if checklist_items is not None:
        if not isinstance(checklist_items, list) or any(not isinstance(i, dict) or "label" not in i for i in checklist_items):
            raise ValidationFailed("Checklist items must be a list of objects with a label.")
        if any(not isinstance(i, dict) or not str(i.get("label", "")).strip() for i in checklist_items):
            raise ValidationFailed("Checklist entries require a label.")
        wo.checklist_items = checklist_items
    _check_completion(wo, resolution_notes)
    before = _snapshot(wo)
    wo.status, wo.resolution_notes, wo.completed_at = (WorkOrder.Status.COMPLETED,
                                                       resolution_notes.strip(), timezone.now())
    wo.save(update_fields=["status", "resolution_notes", "completed_at", "checklist_items", "updated_at"])
    _record(wo, actor=actor, action="completed", before=before, request=request)
    return wo


@transaction.atomic
def submit_for_review(wo, *, membership, actor, request=None):
    """Persist the documented COMPLETED -> SUPERVISOR_REVIEW transition separately."""
    _need(membership, "work_order.complete", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    _transition(wo, WorkOrder.Status.SUPERVISOR_REVIEW)
    if wo.assigned_to_id != membership.pk:
        raise PermissionDenied("Only the assigned technician may submit this work order for review.")
    _check_completion(wo, wo.resolution_notes)
    before = _snapshot(wo)
    wo.status = WorkOrder.Status.SUPERVISOR_REVIEW
    wo.save(update_fields=["status", "updated_at"])
    _record(wo, actor=actor, action="submitted_for_review", before=before, request=request)
    return wo


@transaction.atomic
def review_work_order(wo, *, membership, actor, approved, notes="", request=None, require_comment=False):
    """Single implementation for legacy review and explicit close/return actions."""
    code = "work_order.close" if approved else "work_order.review"
    _need(membership, code, wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    notes = (notes or "").strip()
    if approved and wo.status == WorkOrder.Status.CLOSED:
        # The close endpoint is safe to retry after a network timeout.
        if wo.closure_approvals.filter(decision=ClosureApproval.Decision.APPROVED).exists():
            return wo
    if wo.status != WorkOrder.Status.SUPERVISOR_REVIEW:
        raise InvalidTransition("Only work orders awaiting supervisor review can be reviewed.")
    if approved and require_comment and not notes:
        raise ValidationFailed("A closure comment is required.", code="closure_comment_required")
    if not approved and not notes:
        raise ValidationFailed("A reason is required when rejecting a work order.")
    before = _snapshot(wo)
    if approved:
        _check_completion(wo, wo.resolution_notes)
        if not wo.labor_entries.exists():
            raise Conflict("Record labor before closing the work order.", code="labor_required")
        _transition(wo, WorkOrder.Status.CLOSED)
        wo.status, wo.closed_at, wo.closed_by, wo.supervisor_notes = (
            WorkOrder.Status.CLOSED, timezone.now(), actor, notes)
        action = "closed"
    else:
        _transition(wo, WorkOrder.Status.IN_PROGRESS)
        wo.status, wo.supervisor_notes = WorkOrder.Status.IN_PROGRESS, notes
        action = "review_rejected"
    wo.save(update_fields=["status", "closed_at", "closed_by", "supervisor_notes", "updated_at"])
    ClosureApproval.objects.create(organization=wo.organization, work_order=wo, approver=actor,
                                   decision=(ClosureApproval.Decision.APPROVED if approved
                                             else ClosureApproval.Decision.RETURNED), comment=notes)
    _record(wo, actor=actor, action=action, before=before, notes=notes, request=request)
    return wo


def close_work_order(wo, *, membership, actor, comment, request=None):
    return review_work_order(wo, membership=membership, actor=actor, approved=True, notes=comment,
                             request=request, require_comment=True)


def return_for_rework(wo, *, membership, actor, comment, request=None):
    return review_work_order(wo, membership=membership, actor=actor, approved=False, notes=comment,
                             request=request)


@transaction.atomic
def cancel_work_order(wo, *, membership, actor, reason, request=None):
    _need(membership, "work_order.cancel", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    _transition(wo, WorkOrder.Status.CANCELLED)
    if wo.started_at:
        raise Conflict("A work order cannot be cancelled after work has started.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationFailed("A cancellation reason is required.")
    before = _snapshot(wo)
    wo.status = WorkOrder.Status.CANCELLED
    wo.save(update_fields=["status", "updated_at"])
    _record(wo, actor=actor, action="cancelled", before=before, notes=reason, request=request)
    return wo


@transaction.atomic
def set_checklist(wo, *, membership, actor, items, request=None):
    _need(membership, "work_order.update", wo.site)
    if wo.status not in (WorkOrder.Status.DRAFT, WorkOrder.Status.PLANNED, WorkOrder.Status.ASSIGNED):
        raise Conflict("Checklist requirements cannot be changed after dispatch.")
    if not isinstance(items, list) or not items or any(not isinstance(i, dict) or not str(i.get("label", "")).strip() for i in items):
        raise ValidationFailed("Add at least one checklist item with a label.")
    before = _snapshot(wo)
    wo.checklist_items = [{"label": str(i["label"]).strip()[:200], "required": bool(i.get("required", True)),
                           "completed": False} for i in items]
    wo.save(update_fields=["checklist_items", "updated_at"])
    _record(wo, actor=actor, action="checklist_updated", before=before, request=request)
    return wo


@transaction.atomic
def record_labor(wo, *, membership, actor, work_date, hours, description, request=None):
    _need(membership, "work_order.record", wo.site)
    _assert_assignee_or_dispatcher(wo, membership)
    if wo.status not in (WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD):
        raise Conflict("Labor can only be recorded during active work.")
    try:
        hours = Decimal(str(hours))
    except Exception as exc:
        raise ValidationFailed("Hours must be a decimal between 0 and 24.") from exc
    if hours <= 0 or hours > 24 or work_date > date.today() or not description.strip():
        raise ValidationFailed("Labor requires 0 < hours <= 24, a non-future date, and a description.")
    entry = WorkOrderLabor.objects.create(organization=wo.organization, work_order=wo, technician=membership,
                                          entered_by=actor, work_date=work_date, hours=hours,
                                          description=description.strip())
    _record(wo, actor=actor, action="labor_recorded", metadata={"labor_id": str(entry.pk), "hours": str(hours)},
            request=request)
    return entry


@transaction.atomic
def record_work_update(wo, *, membership, actor, update, request=None):
    _need(membership, "work_order.record", wo.site)
    _assert_assignee_or_dispatcher(wo, membership)
    if wo.status not in (WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD):
        raise Conflict("Work updates can only be recorded while work is in progress or on hold.")
    update = (update or "").strip()
    if len(update) < 5:
        raise ValidationFailed("Enter a work update of at least 5 characters.")
    _record(wo, actor=actor, action="work_update", notes=update, request=request)
    return wo


@transaction.atomic
def record_material(wo, *, membership, actor, item_name, quantity, unit="", part_reference="", notes="", request=None):
    _need(membership, "work_order.record", wo.site)
    _assert_assignee_or_dispatcher(wo, membership)
    if wo.status not in (WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD):
        raise Conflict("Materials can only be recorded during active work.")
    try:
        quantity = Decimal(str(quantity))
    except Exception as exc:
        raise ValidationFailed("Quantity must be a positive decimal.") from exc
    if quantity <= 0 or not item_name.strip():
        raise ValidationFailed("Material name and positive quantity are required.")
    entry = WorkOrderMaterial.objects.create(organization=wo.organization, work_order=wo, entered_by=actor,
        item_name=item_name.strip(), quantity=quantity, unit=unit.strip(), part_reference=part_reference.strip(),
        notes=notes.strip())
    _record(wo, actor=actor, action="material_recorded", metadata={"material_id": str(entry.pk),
            "quantity": str(quantity), "inventory_movement": False}, request=request)
    return entry


@transaction.atomic
def add_evidence(wo, *, membership, actor, attachment_id, request=None):
    _need(membership, "work_order.attach", wo.site)
    attachment = Attachment.objects.for_organization(wo.organization).filter(pk=attachment_id).first()
    if attachment is None:
        raise NotFound("Evidence file not found.")
    if attachment.object_id != str(wo.pk):
        raise ValidationFailed("Evidence must already be attached to this work order.")
    before = _snapshot(wo)
    WorkOrderEvidence.objects.get_or_create(organization=wo.organization, work_order=wo, attachment=attachment,
                                            defaults={"linked_by": actor})
    _record(wo, actor=actor, action="evidence_linked", before=before,
            metadata={"attachment_id": str(attachment.pk)}, request=request)
    return attachment


def closure_blockers(wo):
    blockers = []
    if len((wo.resolution_notes or "").strip()) < 10:
        blockers.append({"code": "resolution_notes_required", "message": "Resolution notes are required."})
    if not wo.checklist_items or any(i.get("required", True) and not i.get("completed")
                                     for i in wo.checklist_items if isinstance(i, dict)):
        blockers.append({"code": "checklist_incomplete", "message": "Required checklist items are incomplete."})
    if wo.work_type in (WorkOrder.WorkType.CORRECTIVE, WorkOrder.WorkType.INSTALLATION) and not wo.completion_evidence.exists():
        blockers.append({"code": "evidence_required", "message": "Completion evidence is required."})
    if not wo.labor_entries.exists():
        blockers.append({"code": "labor_required", "message": "Labor must be recorded."})
    return blockers


def evidence_for(wo):
    return Attachment.objects.for_organization(wo.organization).filter(work_order_links__work_order=wo)
