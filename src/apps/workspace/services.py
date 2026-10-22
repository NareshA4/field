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
from apps.workorders.models import WorkOrder, WorkOrderSignoff

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

    all_parts = selectors.part_lines_for(work_order)
    consumable_parts = [ln for ln in all_parts if ln.quantity_issued > 0 and ln.outstanding > 0]
    returned_parts = [ln for ln in all_parts if ln.quantity_returned > 0]
    meters = selectors.meters_for(work_order)

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
        "consumable_parts": consumable_parts,
        "consumable_parts_count": len(consumable_parts),
        "returned_parts": returned_parts,
        "returned_parts_count": len(returned_parts),
        "has_returned_parts": bool(returned_parts),
        "meters": meters,
        "meters_count": len(meters),
        "has_meters": bool(meters),
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
def complete_job_with_signoff(
    work_order: WorkOrder,
    *,
    resolution_notes: str,
    signer_name: str,
    signer_title: str = "",
    signature_data_base64: str | None = None,
    signature_file=None,
    final_photo_file=None,
    customer_notes: str = "",
    technician_notes: str = "",
    membership: Membership,
    actor: User,
    request: HttpRequest | None = None,
) -> tuple[WorkOrder, WorkOrderSignoff]:
    """Complete a work order and persist customer digital signature & final photo evidence."""
    import base64
    from django.core.files.base import ContentFile
    from apps.files import services as files_services

    assert_can_execute_job(work_order, membership)

    signer_name = (signer_name or "").strip()
    if not signer_name:
        raise ValidationFailed("Customer / representative name is required for digital sign-off.")

    signature_attachment = None

    if signature_file:
        signature_attachment = files_services.attach(
            signature_file,
            target=work_order,
            organization=work_order.organization,
            user=actor,
            description=f"Customer Digital Signature ({signer_name})",
            read_permission="work_order.view_assigned",
            request=request,
        )
    elif signature_data_base64:
        raw_b64 = signature_data_base64.strip()
        if "," in raw_b64:
            raw_b64 = raw_b64.split(",", 1)[1]
        try:
            image_data = base64.b64decode(raw_b64)
            if not image_data or len(image_data) < 50:
                raise ValueError("Signature payload is empty or invalid.")
            sig_file = ContentFile(image_data, name=f"signature_{work_order.order_number}.png")
            signature_attachment = files_services.attach(
                sig_file,
                target=work_order,
                organization=work_order.organization,
                user=actor,
                description=f"Customer Digital Signature ({signer_name})",
                read_permission="work_order.view_assigned",
                request=request,
            )
        except Exception as exc:
            raise ValidationFailed(f"Invalid digital signature image data: {exc}", code="invalid_signature") from exc
    else:
        raise ValidationFailed("A digital signature is required to complete this job.", code="signature_required")

    # If final photo evidence was provided, attach it as completion evidence
    if final_photo_file:
        upload_job_evidence(
            work_order,
            file=final_photo_file,
            description=f"Final completion photo for {work_order.order_number}",
            membership=membership,
            actor=actor,
            request=request,
        )

    # Persist the sign-off record
    signoff, _ = WorkOrderSignoff.objects.update_or_create(
        work_order=work_order,
        defaults={
            "organization": work_order.organization,
            "signer_name": signer_name,
            "signer_title": (signer_title or "").strip(),
            "signed_at": timezone.now(),
            "signature_attachment": signature_attachment,
            "customer_notes": (customer_notes or "").strip(),
            "technician_notes": (technician_notes or "").strip(),
        },
    )

    # Perform authoritative work order completion transition
    order = complete_job(
        work_order,
        resolution_notes=resolution_notes,
        membership=membership,
        actor=actor,
        request=request,
    )

    audit.record(
        action="workspace.customer_signoff",
        organization=order.organization,
        actor=actor,
        target=order,
        metadata={
            "signer_name": signoff.signer_name,
            "signer_title": signoff.signer_title,
            "signoff_id": str(signoff.pk),
            "signed_at": signoff.signed_at.isoformat(),
        },
        request=request,
    )

    return order, signoff


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


@transaction.atomic
def consume_job_part(
    work_order: WorkOrder,
    *,
    part_line_id: str,
    quantity: str | Decimal,
    membership: Membership,
    actor: User,
    request: HttpRequest | None = None,
):
    """Technician consumes reserved/issued inventory parts on their assigned job."""
    from apps.inventory import services as inventory_services
    from apps.inventory.models import WorkOrderPart

    assert_can_execute_job(work_order, membership)

    if not (
        rbac.has_permission(membership, "inventory.consume", work_order.site)
        or rbac.has_permission(membership, "work_order.record", work_order.site)
    ):
        raise PermissionDenied("You do not have permission to consume inventory parts for this work order.")

    part_line = WorkOrderPart.objects.filter(
        organization=work_order.organization,
        work_order=work_order,
        pk=part_line_id,
    ).first()
    if part_line is None:
        raise ValidationFailed("Part requirement line not found on this work order.", code="part_line_not_found")

    return inventory_services.consume(
        part_line,
        quantity,
        actor=actor,
        membership=membership,
        request=request,
    )


@transaction.atomic
def return_job_part(
    work_order: WorkOrder,
    *,
    part_line_id: str,
    quantity: str | Decimal,
    reason: str = "",
    membership: Membership,
    actor: User,
    request: HttpRequest | None = None,
):
    """Technician returns unconsumed, warehouse-issued parts directly from the job workspace."""
    from apps.inventory import services as inventory_services
    from apps.inventory.models import WorkOrderPart

    assert_can_execute_job(work_order, membership)

    if not (
        rbac.has_permission(membership, "inventory.return", work_order.site)
        or rbac.has_permission(membership, "inventory.consume", work_order.site)
        or rbac.has_permission(membership, "work_order.record", work_order.site)
    ):
        raise PermissionDenied("You do not have permission to return inventory parts for this work order.")

    part_line = WorkOrderPart.objects.filter(
        organization=work_order.organization,
        work_order=work_order,
        pk=part_line_id,
    ).first()
    if part_line is None:
        raise ValidationFailed("Part requirement line not found on this work order.", code="part_line_not_found")

    returned_line = inventory_services.return_stock(
        part_line,
        quantity,
        actor=actor,
        reason=reason,
        request=request,
    )

    audit.record(
        action="workspace.part_returned",
        organization=work_order.organization,
        actor=actor,
        target=work_order,
        metadata={
            "part_line_id": str(returned_line.pk),
            "part_number": returned_line.part.part_number,
            "quantity_returned": str(quantity),
            "reason": reason,
        },
        request=request,
    )
    return returned_line


@transaction.atomic
def record_job_meter_reading(
    work_order: WorkOrder,
    *,
    meter_id: str,
    value: Decimal | str | float,
    read_at=None,
    notes: str = "",
    membership: Membership,
    actor: User,
    request: HttpRequest | None = None,
):
    """Records an asset meter reading on the work order's target asset (PRD Section 3.3)."""
    assert_can_execute_job(work_order, membership)
    if not work_order.asset_id:
        raise ValidationFailed("Work order has no associated asset.", code="no_asset")

    from apps.assets import services as asset_services
    from apps.assets.models import AssetMeter

    meter = AssetMeter.objects.filter(
        organization=work_order.organization,
        asset_id=work_order.asset_id,
        pk=meter_id,
    ).first()
    if not meter:
        raise ValidationFailed("Meter not found on this asset.", code="meter_not_found")

    reading = asset_services.record_reading(
        meter,
        value=value,
        read_at=read_at,
        notes=notes,
        actor=actor,
        request=request,
        source="technician_workspace",
    )

    audit.record(
        action="workspace.meter_reading_recorded",
        organization=work_order.organization,
        actor=actor,
        target=work_order,
        metadata={
            "meter_id": str(meter.pk),
            "meter_name": meter.name,
            "value": str(value),
            "unit": meter.unit,
        },
        request=request,
    )
    return reading


@transaction.atomic
def report_auxiliary_defect(
    work_order: WorkOrder,
    *,
    title: str,
    membership: Membership,
    actor: User,
    asset=None,
    description: str = "",
    kind: str = "INCIDENT",
    severity: str = "MEDIUM",
    service_impact: str = "NONE",
    impact_notes: str = "",
    failure_category: str = "",
    occurred_at=None,
    override_duplicate: bool = False,
    override_reason: str = "",
    request: HttpRequest | None = None,
):
    """
    Technician reports an auxiliary defect or incident discovered on-site during work order execution.
    Automatically links to the work order site, defaults to or verifies the target asset,
    creates the authoritative M05 ServiceRequest, cross-links a WorkNote on the active work order,
    and logs an audit trail record.
    """
    assert_can_execute_job(work_order, membership)

    from apps.assets.models import Asset
    from apps.incidents import services as incident_services

    target_asset = asset
    if target_asset is None:
        target_asset = work_order.asset

    if target_asset is None:
        raise ValidationFailed("An asset must be selected to report a defect.")

    if target_asset.organization_id != work_order.organization_id:
        raise ValidationFailed("Selected asset does not belong to the same organization.")

    # Contextual note appended to description
    context_note = f"[Discovered during on-site execution of {work_order.order_number}]"
    full_description = f"{description}\n\n{context_note}".strip() if description else context_note

    sr = incident_services.create_request(
        work_order.organization,
        asset=target_asset,
        reporter=membership,
        title=title,
        description=full_description,
        kind=kind,
        severity=severity,
        service_impact=service_impact,
        impact_notes=impact_notes,
        failure_category=failure_category,
        override_duplicate=override_duplicate,
        override_reason=override_reason,
        occurred_at=occurred_at or timezone.now(),
        actor=actor,
        request=request,
    )

    # Cross-link note onto work order execution journal
    record_work_note(
        work_order,
        note=f"Reported auxiliary defect {sr.number} ({sr.get_kind_display()} · {sr.get_severity_display()}): {sr.title} on asset {target_asset.asset_tag}.",
        membership=membership,
        actor=actor,
        request=request,
    )

    audit.record(
        action="workspace.auxiliary_defect_reported",
        organization=work_order.organization,
        actor=actor,
        target=work_order,
        metadata={
            "service_request_id": str(sr.pk),
            "service_request_number": sr.number,
            "asset_id": str(target_asset.pk),
            "asset_tag": target_asset.asset_tag,
            "title": sr.title,
            "severity": sr.severity,
        },
        request=request,
    )
    return sr


