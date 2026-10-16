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


def get_job_detail(membership: Membership, pk: str) -> dict:
    """Retrieve full job detail including route, checklists, logs, notes, and actions."""
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
    from apps.inventory.models import WorkOrderPart
    part_lines = list(WorkOrderPart.objects.for_organization(membership.organization).filter(work_order=wo).select_related("part", "warehouse"))

    all_parts = part_lines_for(wo)
    consumable_parts = [ln for ln in all_parts if ln.quantity_issued > 0 and ln.outstanding > 0]
    consumed_parts = [ln for ln in all_parts if ln.quantity_consumed > 0]

    return {
        "work_order": wo,
        "route": route,
        "notes": notes,
        "travel_logs": travel_logs,
        "drawer": drawer_summary,
        "labor_entries": list(wo.labor_entries.select_related("technician__user").order_by("-work_date", "-created_at")),
        "materials": list(wo.material_entries.order_by("item_name")),
        "part_lines": part_lines,
        "evidence": list(wo.completion_evidence.order_by("-created_at")),
        "allowed_actions": wo_services.allowed_actions(wo, membership),
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

