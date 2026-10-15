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
        "part_lines": all_parts,
        "consumable_parts": consumable_parts,
        "has_consumable_parts": bool(consumable_parts),
        "consumed_parts": consumed_parts,
        "has_consumed_parts": bool(consumed_parts),
        "evidence": list(wo.completion_evidence.order_by("-created_at")),
        "allowed_actions": wo_services.allowed_actions(wo, membership),
    }

