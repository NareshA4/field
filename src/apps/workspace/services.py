from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import PermissionDenied, ValidationFailed
from apps.rbac import services as rbac
from apps.workorders import services as wo_services
from apps.workorders.models import WorkOrder

from .models import TravelLog, WorkNote
from . import selectors

if TYPE_CHECKING:
    from django.http import HttpRequest
    from apps.accounts.models import User
    from apps.tenancy.models import Membership


def assert_can_execute_job(work_order: WorkOrder, membership: Membership) -> None:
    """Enforce that the caller is the assigned technician or an authorized dispatcher."""
    if not membership or not membership.is_active:
        raise PermissionDenied("Active membership required.")
    if work_order.organization_id != membership.organization_id:
        raise PermissionDenied("Work order belongs to another organization.")
    is_assignee = work_order.assigned_to_id == membership.pk
    is_dispatcher = rbac.has_permission(membership, "work_order.dispatch", work_order.site)
    if not (is_assignee or is_dispatcher):
        raise PermissionDenied("Only the assigned technician or an authorized dispatcher may execute this work order.")


@transaction.atomic
def start_job(work_order: WorkOrder, *, membership: Membership, actor: User, request: HttpRequest | None = None) -> WorkOrder:
    """Technician starts the assigned work order (calls M06 start transition)."""
    assert_can_execute_job(work_order, membership)
    order = wo_services.start_work_order(work_order, membership=membership, actor=actor, request=request)
    audit.record(
        action="workspace.job_started",
        organization=order.organization,
        actor=actor,
        target=order,
        metadata={"notes": f"Started work order {order.order_number} from execution drawer"},
        request=request,
    )
    return order


@transaction.atomic
def pause_job(work_order: WorkOrder, *, reason: str, membership: Membership, actor: User, request: HttpRequest | None = None) -> WorkOrder:
    """Technician pauses the active job, placing it ON_HOLD with a mandatory reason."""
    assert_can_execute_job(work_order, membership)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationFailed("A reason is required to pause the job.")
    order = wo_services.hold_work_order(work_order, membership=membership, actor=actor, reason=reason, request=request)
    audit.record(
        action="workspace.job_paused",
        organization=order.organization,
        actor=actor,
        target=order,
        metadata={"reason": reason, "notes": f"Paused work order {order.order_number}: {reason}"},
        request=request,
    )
    return order


@transaction.atomic
def resume_job(work_order: WorkOrder, *, membership: Membership, actor: User, request: HttpRequest | None = None) -> WorkOrder:
    """Technician resumes the paused job from ON_HOLD to IN_PROGRESS."""
    assert_can_execute_job(work_order, membership)
    order = wo_services.resume_work_order(work_order, membership=membership, actor=actor, request=request)
    audit.record(
        action="workspace.job_resumed",
        organization=order.organization,
        actor=actor,
        target=order,
        metadata={"notes": f"Resumed work order {order.order_number} from execution drawer"},
        request=request,
    )
    return order


@transaction.atomic
def log_travel(
    work_order: WorkOrder,
    *,
    membership: Membership,
    actor: User,
    duration_hours: Decimal | str | float,
    work_date=None,
    distance_km: Decimal | str | float | None = None,
    start_time=None,
    end_time=None,
    route_details: str = "",
    notes: str = "",
    record_labor_entry: bool = True,
    request: HttpRequest | None = None,
) -> TravelLog:
    """Log technician travel time and route details.
    
    If record_labor_entry is True and work order is active, authoritatively persists
    a labor entry in M06 WorkOrderLabor so total job hours stay unified.
    """
    assert_can_execute_job(work_order, membership)
    try:
        hours = Decimal(str(duration_hours))
    except Exception as exc:
        raise ValidationFailed("Travel duration must be a valid number of hours.") from exc

    if hours <= 0 or hours > 24:
        raise ValidationFailed("Travel duration must be between 0 and 24 hours.")

    km = None
    if distance_km not in (None, ""):
        try:
            km = Decimal(str(distance_km))
            if km < 0:
                raise ValidationFailed("Distance cannot be negative.")
        except Exception as exc:
            raise ValidationFailed("Distance must be a valid number.") from exc

    work_date = work_date or timezone.localdate()
    if work_date > timezone.localdate():
        raise ValidationFailed("Travel date cannot be in the future.")

    labor_entry = None
    if record_labor_entry and work_order.status in (WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD):
        desc = f"Travel: {notes or route_details or 'Transit to site'}".strip()
        labor_entry = wo_services.record_labor(
            work_order,
            membership=membership,
            actor=actor,
            work_date=work_date,
            hours=hours,
            description=desc,
            request=request,
        )

    travel_log = TravelLog.objects.create(
        organization=work_order.organization,
        work_order=work_order,
        technician=membership,
        start_time=start_time,
        end_time=end_time,
        duration_hours=hours,
        distance_km=km,
        route_details=(route_details or "").strip(),
        notes=(notes or "").strip(),
        labor_entry=labor_entry,
    )

    audit.record(
        action="workspace.travel_logged",
        organization=work_order.organization,
        actor=actor,
        target=work_order,
        metadata={
            "duration_hours": str(hours),
            "distance_km": str(km) if km is not None else None,
            "notes": f"Logged {hours}h travel ({km or 0} km) for {work_order.order_number}",
        },
        request=request,
    )
    return travel_log


def get_drawer_summary(work_order: WorkOrder, *, membership: Membership) -> dict:
    """Assembles all data needed by the technician job execution drawer."""
    assert_can_execute_job(work_order, membership)
    route = selectors.route_context(work_order)
    travel_logs = list(selectors.travel_logs_for(work_order)[:10])
    total_travel_hours = (
        TravelLog.objects.filter(work_order=work_order).aggregate(total=Sum("duration_hours"))["total"]
        or Decimal("0.00")
    )
    labor_entries = list(work_order.labor_entries.select_related("technician__user").order_by("-work_date", "-created_at")[:10])
    total_labor_hours = (
        work_order.labor_entries.aggregate(total=Sum("hours"))["total"]
        or Decimal("0.00")
    )

    allowed = wo_services.allowed_actions(work_order, membership)
    action_ops = {a["operation"]: a for a in allowed}

    can_start = "start" in action_ops
    can_pause = "hold" in action_ops
    can_resume = "resume" in action_ops
    can_complete = "complete" in action_ops

    return {
        "work_order": work_order,
        "status": work_order.status,
        "status_display": work_order.get_status_display(),
        "priority_display": work_order.get_priority_display(),
        "work_type_display": work_order.get_work_type_display(),
        "can_start": can_start,
        "can_pause": can_pause,
        "can_resume": can_resume,
        "can_complete": can_complete,
        "allowed_actions": allowed,
        "route": route,
        "travel_logs": travel_logs,
        "total_travel_hours": total_travel_hours,
        "labor_entries": labor_entries,
        "total_labor_hours": total_labor_hours,
    }


@transaction.atomic
def record_work_note(
    work_order: WorkOrder,
    *,
    note: str,
    membership: Membership,
    actor: User,
    request: HttpRequest | None = None,
) -> WorkNote:
    """Appends an execution note for the technician work order."""
    assert_can_execute_job(work_order, membership)
    note_clean = (note or "").strip()
    if not note_clean:
        raise ValidationFailed("Work note cannot be blank.")
    if len(note_clean) > 2000:
        raise ValidationFailed("Work note cannot exceed 2000 characters.")

    entry = WorkNote.objects.create(
        organization=work_order.organization,
        work_order=work_order,
        author=membership,
        body=note_clean,
    )
    audit.record(
        action="workspace.work_note_added",
        organization=work_order.organization,
        actor=actor,
        target=work_order,
        metadata={"note_id": str(entry.pk), "note_preview": note_clean[:80]},
        request=request,
    )
    return entry


@transaction.atomic
def complete_job(
    work_order: WorkOrder,
    *,
    resolution_notes: str,
    membership: Membership,
    actor: User,
    request: HttpRequest | None = None,
) -> WorkOrder:
    """Technician completes the job (orchestrates authoritative M06 complete_work_order)."""
    assert_can_execute_job(work_order, membership)
    order = wo_services.complete_work_order(
        work_order,
        membership=membership,
        actor=actor,
        resolution_notes=resolution_notes,
        request=request,
    )
    audit.record(
        action="workspace.job_completed",
        organization=order.organization,
        actor=actor,
        target=order,
        metadata={"resolution_notes": (resolution_notes or "")[:120]},
        request=request,
    )
    return order


@transaction.atomic
def record_job_labor(
    work_order: WorkOrder,
    *,
    hours: Decimal | str | float,
    description: str,
    membership: Membership,
    actor: User,
    work_date=None,
    request: HttpRequest | None = None,
):
    """Records on-site technician labor entry in M06."""
    assert_can_execute_job(work_order, membership)
    labor = wo_services.record_labor(
        work_order,
        membership=membership,
        actor=actor,
        work_date=work_date or timezone.localdate(),
        hours=hours,
        description=description,
        request=request,
    )
    audit.record(
        action="workspace.labor_recorded",
        organization=work_order.organization,
        actor=actor,
        target=work_order,
        metadata={"hours": str(hours), "description": description[:80]},
        request=request,
    )
    return labor


@transaction.atomic
def record_job_material(
    work_order: WorkOrder,
    *,
    item_name: str,
    quantity: Decimal | str | float,
    membership: Membership,
    actor: User,
    unit: str = "unit",
    notes: str = "",
    request: HttpRequest | None = None,
):
    """Records material used during job execution in M06."""
    assert_can_execute_job(work_order, membership)
    material = wo_services.record_material(
        work_order,
        membership=membership,
        actor=actor,
        item_name=item_name,
        quantity=quantity,
        unit=unit,
        notes=notes,
        request=request,
    )
    audit.record(
        action="workspace.material_recorded",
        organization=work_order.organization,
        actor=actor,
        target=work_order,
        metadata={"item_name": item_name, "quantity": str(quantity)},
        request=request,
    )
    return material


@transaction.atomic
def upload_job_evidence(
    work_order: WorkOrder,
    *,
    file,
    description: str = "",
    membership: Membership,
    actor: User,
    request: HttpRequest | None = None,
):
    """Uploads photo or document completion evidence and links it to the work order in M06."""
    assert_can_execute_job(work_order, membership)
    from apps.files import services as files_services

    if not file:
        raise ValidationFailed("A photo or file must be selected for upload.")

    att = files_services.attach(
        file,
        target=work_order,
        organization=work_order.organization,
        user=actor,
        description=(description or f"Completion evidence for {work_order.order_number}")[:200],
        read_permission="work_order.view_assigned",
        request=request,
    )
    wo_services.add_evidence(
        work_order,
        membership=membership,
        actor=actor,
        attachment_id=att.pk,
        request=request,
    )
    audit.record(
        action="workspace.evidence_uploaded",
        organization=work_order.organization,
        actor=actor,
        target=work_order,
        metadata={"attachment_id": str(att.pk), "filename": att.original_name, "size": att.size},
        request=request,
    )
    return att

