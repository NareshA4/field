from __future__ import annotations

from typing import TYPE_CHECKING
from django.db.models import QuerySet

from apps.sites.models import SiteContact
from apps.workorders.models import WorkOrder

from .models import TravelLog, WorkNote

if TYPE_CHECKING:
    from apps.tenancy.models import Membership, Organization


def route_context(work_order: WorkOrder) -> dict:
    """Return navigation and site routing details for the work order."""
    site = work_order.site
    contacts = SiteContact.objects.filter(site=site).order_by("escalation_order")
    asset = work_order.asset
    location_path = []
    if asset and getattr(asset, "zone", None):
        z = asset.zone
        while z:
            location_path.insert(0, z.name)
            z = z.parent

    maps_query = ""
    if site.address:
        maps_query = site.address.replace("\n", ", ").strip()
    elif site.name:
        maps_query = f"{site.name}, {site.code}"

    return {
        "site_name": site.name,
        "site_code": site.code,
        "site_address": site.address,
        "location_path": " › ".join(location_path) if location_path else "",
        "contacts": contacts,
        "maps_query": maps_query,
    }


def travel_logs_for(work_order: WorkOrder) -> QuerySet[TravelLog]:
    """Return all travel logs associated with a work order."""
    return TravelLog.objects.filter(work_order=work_order).select_related("technician__user").order_by("-created_at")


def work_notes_for(work_order: WorkOrder) -> QuerySet[WorkNote]:
    """Return append-only technician work notes for a work order."""
    return WorkNote.objects.filter(work_order=work_order).select_related("author__user").order_by("created_at")


def my_jobs(
    membership: Membership,
    *,
    status_filter: str | None = None,
    search: str | None = None,
    ordering: str | None = None,
) -> QuerySet[WorkOrder]:
    """Return work orders assigned to the technician within their organization."""
    from django.db.models import Q

    qs = WorkOrder.objects.for_organization(membership.organization).filter(
        assigned_to=membership
    ).select_related("site", "asset", "assigned_to__user")

    filter_mode = (status_filter or "active").lower()
    if filter_mode == "active":
        qs = qs.filter(
            status__in=[
                WorkOrder.Status.IN_PROGRESS,
                WorkOrder.Status.ON_HOLD,
                WorkOrder.Status.DISPATCHED,
            ]
        )
    elif filter_mode == "upcoming":
        qs = qs.filter(
            status__in=[
                WorkOrder.Status.ASSIGNED,
                WorkOrder.Status.PLANNED,
            ]
        )
    elif filter_mode == "completed":
        qs = qs.filter(
            status__in=[
                WorkOrder.Status.COMPLETED,
                WorkOrder.Status.SUPERVISOR_REVIEW,
                WorkOrder.Status.CLOSED,
            ]
        )
    # If filter_mode is "all", no status filtering is applied.

    if search:
        s = search.strip()
        qs = qs.filter(
            Q(order_number__icontains=s)
            | Q(title__icontains=s)
            | Q(asset__asset_tag__icontains=s)
            | Q(asset__name__icontains=s)
            | Q(site__name__icontains=s)
        )

    order_by = ordering or "-planned_start"
    return qs.order_by(order_by, "-created_at")


def part_lines_for(work_order: WorkOrder):
    """Return all inventory part requirement lines associated with a work order."""
    from django.apps import apps
    if not apps.is_installed("apps.inventory"):
        return []
    from apps.inventory.models import WorkOrderPart
    return list(
        WorkOrderPart.objects.filter(
            organization=work_order.organization,
            work_order=work_order,
        ).select_related("part", "warehouse").order_by("created_at")
    )


def consumable_parts_for(work_order: WorkOrder):
    """Return issued part lines that have outstanding quantity available to consume."""
    return [ln for ln in part_lines_for(work_order) if ln.quantity_issued > 0 and ln.outstanding > 0]


def meters_for(work_order: WorkOrder) -> list[dict]:
    """Return active asset meters with their latest reading for the work order's target asset."""
    if not work_order.asset_id:
        return []
    from apps.assets.models import AssetMeter

    meters = AssetMeter.objects.filter(
        organization=work_order.organization,
        asset_id=work_order.asset_id,
        is_active=True,
    ).prefetch_related("readings").order_by("name")

    result = []
    for m in meters:
        latest = m.readings.order_by("-read_at", "-created_at").first()
        result.append({
            "id": str(m.pk),
            "name": m.name,
            "unit": m.unit,
            "latest_value": latest.value if latest else None,
            "latest_read_at": latest.read_at if latest else None,
            "latest_notes": latest.notes if latest else "",
            "readings_count": m.readings.count(),
        })
    return result


def get_job_detail(membership: Membership, pk: str) -> dict:
    """Retrieve full job detail including route, checklists, logs, notes, meters, and actions."""
    from apps.core.exceptions import NotFound
    from apps.workorders import services as wo_services
    from .services import assert_can_execute_job, get_drawer_summary

    wo = WorkOrder.objects.for_organization(membership.organization).select_related(
        "site", "asset", "asset__category", "asset__zone", "assigned_to__user"
    ).filter(pk=pk).first()

    if wo is None:
        raise NotFound("Work order not found.")

    assert_can_execute_job(wo, membership)

    route = route_context(wo)
    notes = list(work_notes_for(wo))
    travel_logs = list(travel_logs_for(wo))
    drawer_summary = get_drawer_summary(wo, membership=membership)

    all_parts = part_lines_for(wo)
    consumable_parts = [ln for ln in all_parts if ln.quantity_issued > 0 and ln.outstanding > 0]
    consumed_parts = [ln for ln in all_parts if ln.quantity_consumed > 0]
    returned_parts = [ln for ln in all_parts if ln.quantity_returned > 0]
    meters = meters_for(wo)

    from apps.assets.models import Asset
    from apps.incidents.models import ServiceRequest

    site_assets = []
    if wo.site_id:
        site_assets = list(
            Asset.objects.for_organization(membership.organization)
            .filter(site_id=wo.site_id)
            .exclude(status__in=["RETIRED", "DISPOSED"])
            .select_related("zone")
            .order_by("asset_tag")
        )

    auxiliary_defects = list(
        ServiceRequest.objects.for_organization(membership.organization)
        .filter(description__icontains=wo.order_number)
        .select_related("asset", "reported_by__user")
        .order_by("-created_at")[:10]
    )

    return {
        "work_order": wo,
        "route": route,
        "notes": notes,
        "travel_logs": travel_logs,
        "drawer": drawer_summary,
        "labor_entries": list(wo.labor_entries.select_related("technician__user").order_by("-work_date", "-created_at")),
        "materials": list(wo.material_entries.order_by("item_name")),
        "part_lines": all_parts,
        "consumable_parts": consumable_parts,
        "has_consumable_parts": bool(consumable_parts),
        "consumed_parts": consumed_parts,
        "has_consumed_parts": bool(consumed_parts),
        "returned_parts": returned_parts,
        "has_returned_parts": bool(returned_parts),
        "evidence": list(wo.completion_evidence.order_by("-created_at")),
        "allowed_actions": wo_services.allowed_actions(wo, membership),
        "meters": meters,
        "has_meters": bool(meters),
        "site_assets": site_assets,
        "auxiliary_defects": auxiliary_defects,
        "has_auxiliary_defects": bool(auxiliary_defects),
    }


def get_job_completion_context(membership: Membership, pk: str) -> dict:
    """Retrieve structured summary context for job completion and customer digital sign-off."""
    from decimal import Decimal
    from apps.core.exceptions import NotFound
    from .services import assert_can_execute_job

    wo = (
        WorkOrder.objects.for_organization(membership.organization)
        .select_related("site", "asset", "asset__category", "asset__zone", "assigned_to__user")
        .filter(pk=pk)
        .first()
    )

    if wo is None:
        raise NotFound("Work order not found.")

    assert_can_execute_job(wo, membership)

    labor_entries = list(wo.labor_entries.select_related("technician__user").order_by("-work_date", "-created_at"))
    total_labor_hours = sum((entry.hours for entry in labor_entries), Decimal("0.00"))

    all_parts = part_lines_for(wo)
    consumable_parts = [ln for ln in all_parts if ln.quantity_issued > 0 and ln.outstanding > 0]
    consumed_parts = [ln for ln in all_parts if ln.quantity_consumed > 0]
    returned_parts = [ln for ln in all_parts if ln.quantity_returned > 0]

    evidence_list = list(wo.completion_evidence.select_related("attachment").order_by("-created_at"))
    evidence_required = wo.work_type in (WorkOrder.WorkType.CORRECTIVE, WorkOrder.WorkType.INSTALLATION)
    has_evidence = len(evidence_list) > 0
    evidence_is_ready = (not evidence_required) or has_evidence

    # Checklist readiness evaluation
    checklist_blockers = []
    try:
        from apps.checklists import services as checklist_services

        checklist_blockers = checklist_services.checklist_blockers(wo)
    except Exception:
        checklist_blockers = []

    if not wo.checklist_required:
        checklist_is_ready = True
    else:
        checklist_is_ready = (wo.checklist_status == "COMPLETED") and not checklist_blockers

    # Overall completion blockers
    blockers = []
    if wo.status != WorkOrder.Status.IN_PROGRESS and wo.status != WorkOrder.Status.COMPLETED:
        blockers.append(f"Work order must be In Progress to complete (currently {wo.get_status_display()}).")
    if not checklist_is_ready:
        if checklist_blockers:
            blockers.extend(checklist_blockers)
        else:
            blockers.append("All mandatory checklists must be completed and validated.")
    if evidence_required and not has_evidence:
        blockers.append("At least one photo or file evidence attachment is required before completion.")

    can_complete = (len(blockers) == 0) and (wo.status == WorkOrder.Status.IN_PROGRESS)
    signoff = getattr(wo, "customer_signoff", None)

    return {
        "work_order": wo,
        "asset": wo.asset,
        "site": wo.site,
        "labor_entries": labor_entries,
        "total_labor_hours": total_labor_hours,
        "materials": list(wo.material_entries.order_by("item_name")),
        "part_lines": all_parts,
        "consumable_parts": consumable_parts,
        "consumed_parts": consumed_parts,
        "returned_parts": returned_parts,
        "has_returned_parts": bool(returned_parts),
        "has_unconsumed_parts": bool(consumable_parts),
        "evidence": evidence_list,
        "evidence_required": evidence_required,
        "has_evidence": has_evidence,
        "evidence_is_ready": evidence_is_ready,
        "checklist_required": wo.checklist_required,
        "checklist_status": wo.checklist_status,
        "checklist_blockers": checklist_blockers,
        "checklist_is_ready": checklist_is_ready,
        "can_complete": can_complete,
        "blockers": blockers,
        "signoff": signoff,
        "is_completed": wo.status in (WorkOrder.Status.COMPLETED, WorkOrder.Status.SUPERVISOR_REVIEW, WorkOrder.Status.CLOSED),
    }


def daily_capacity_summary(
    membership: Membership,
    target_date: any = None,
) -> dict:
    """Calculate daily workday capacity and labor utilization metrics for the technician."""
    from decimal import Decimal
    from django.db.models import Q, Sum
    from django.utils import timezone
    from apps.workorders.models import WorkOrderLabor

    if target_date is None:
        target_date = timezone.localdate()

    org = membership.organization
    labor_qs = WorkOrderLabor.objects.filter(
        organization=org,
        technician=membership,
        work_date=target_date,
    )
    total_hours_dec = labor_qs.aggregate(s=Sum("hours"))["s"] or Decimal("0.00")
    total_hours = float(total_hours_dec)

    standard_capacity = 8.0
    util_pct = round((total_hours / standard_capacity) * 100.0, 1) if standard_capacity > 0 else 0.0
    progress_pct = min(100.0, util_pct)
    remaining_hours = max(0.0, round(standard_capacity - total_hours, 2))
    is_overtime = total_hours > standard_capacity

    # Travel time breakdown for target date
    travel_logs = TravelLog.objects.filter(
        organization=org,
        technician=membership,
    ).filter(
        Q(labor_entry__work_date=target_date)
        | Q(created_at__date=target_date, labor_entry__isnull=True)
    )
    travel_hours_dec = travel_logs.aggregate(s=Sum("duration_hours"))["s"] or Decimal("0.00")
    travel_hours = float(travel_hours_dec)
    direct_hours = max(0.0, round(total_hours - travel_hours, 2))

    return {
        "date": target_date.isoformat(),
        "date_display": target_date.strftime("%d %b %Y"),
        "standard_capacity_hours": standard_capacity,
        "total_logged_hours": round(total_hours, 2),
        "direct_wrench_hours": direct_hours,
        "travel_hours": round(travel_hours, 2),
        "remaining_hours": remaining_hours,
        "utilization_pct": util_pct,
        "progress_pct": progress_pct,
        "is_overtime": is_overtime,
    }


