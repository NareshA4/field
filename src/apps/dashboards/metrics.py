"""Operational Metrics and Aggregation Engine (Module M14).

Implements HPE Section 7.2 requirements and architectural decision D-052.
All KPIs are calculated on-the-fly as PostgreSQL aggregates over real transactional data.
No hardcoded values, no fake metrics, zero duplicate tables.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Avg, Count, F, Q, Sum
from django.utils import timezone

from apps.assets.models import Asset
from apps.incidents.models import Downtime, ServiceRequest
from apps.inventory.models import Part, StockBalance, StockMovement
from apps.rbac import services as rbac
from apps.rbac.models import MembershipRole, Role
from apps.sites.models import Site
from apps.sla.models import SLABreach, SLATracking
from apps.workorders.models import WorkOrder, WorkOrderLabor

if TYPE_CHECKING:
    from apps.tenancy.models import Membership, Organization

# Default window: 30 days. Max allowed window: 400 days.
DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 400

KPI_DEFINITIONS: dict[str, dict[str, Any]] = {
    "mttr": {
        "name": "Mean Time to Repair (MTTR)",
        "section": "operations",
        "unit": "hours",
        "formula": "mean(ended_at - started_at) of incident downtimes that ended in the window",
        "description": "Average clock hours elapsed between equipment failure and full operational restoration.",
        "source": "M05 Incidents (Downtime)",
        "is_snapshot": False,
    },
    "mtbf": {
        "name": "Mean Time Between Failures (MTBF)",
        "section": "assets",
        "unit": "hours",
        "formula": "(assets in scope × window hours - downtime hours) / failures started in the window",
        "description": "Average operating hours of machinery between breakdown incidents.",
        "source": "M02 Assets & M05 Incidents",
        "is_snapshot": False,
    },
    "downtime_hours": {
        "name": "Equipment Downtime",
        "section": "assets",
        "unit": "hours",
        "formula": "Overlap of active and closed incident downtimes within the selected date window",
        "description": "Cumulative outage hours suffered across all monitored machines.",
        "source": "M05 Incidents (Downtime)",
        "is_snapshot": False,
    },
    "open_work_orders": {
        "name": "Open Work Orders",
        "section": "operations",
        "unit": "count",
        "formula": "Count of work orders in non-terminal status (Draft, Planned, Assigned, Dispatched, In Progress, On Hold)",
        "description": "Current active workload queued or currently being executed by field crews.",
        "source": "M06 Work Orders",
        "is_snapshot": True,
    },
    "overdue_work_orders": {
        "name": "Overdue Work Orders",
        "section": "operations",
        "unit": "count",
        "formula": "Count of open work orders where planned_end is in the past",
        "description": "Work orders that have exceeded their target delivery completion window.",
        "source": "M06 Work Orders",
        "is_snapshot": True,
    },
    "technician_utilization": {
        "name": "Technician Utilization",
        "section": "operations",
        "unit": "%",
        "formula": "(logged labor hours in window) / (active technicians × work days × 8 hours) × 100",
        "description": "Percentage of standard 8-hour workday capacity committed to productive job labor.",
        "source": "M06 Work Orders (WorkOrderLabor) & M07 Workspace",
        "is_snapshot": False,
    },
    "pm_compliance": {
        "name": "Preventive Maintenance Compliance",
        "section": "maintenance",
        "unit": "%",
        "formula": "on_time_pm / (on_time_pm + late_pm + missed_pm) × 100",
        "description": "Percentage of scheduled preventive maintenance jobs completed on or before due date.",
        "source": "M04 & M06 Preventive Work Orders",
        "is_snapshot": False,
    },
    "parts_consumption": {
        "name": "Parts Consumption",
        "section": "inventory",
        "unit": "units",
        "formula": "SUM(ISSUE) - SUM(RETURN) stock ledger movements issued to work orders",
        "description": "Net physical units of spare parts and components consumed by field operations.",
        "source": "M09 Inventory (StockMovement)",
        "is_snapshot": False,
    },
    "sla_breaches": {
        "name": "SLA Breaches",
        "section": "service",
        "unit": "count",
        "formula": "Count of recorded response and resolution SLA target breaches in the window",
        "description": "Total missed SLA commitments on emergency incidents and work orders.",
        "source": "M11 SLA Engine (SLABreach)",
        "is_snapshot": False,
    },
}


def parse_date_window(
    from_date_str: str | None = None,
    to_date_str: str | None = None,
    default_days: int = DEFAULT_WINDOW_DAYS,
) -> tuple[datetime.datetime, datetime.datetime]:
    """Parses and bounds date window in UTC. Defaults to last 30 days. Max 400 days."""
    now = timezone.now()
    today = now.date()
    if to_date_str:
        try:
            to_date = datetime.date.fromisoformat(str(to_date_str).strip())
            # End of that day in UTC
            end_dt = datetime.datetime.combine(to_date, datetime.time.max, tzinfo=datetime.timezone.utc)
        except ValueError:
            end_dt = datetime.datetime.combine(today, datetime.time.max, tzinfo=datetime.timezone.utc)
    else:
        end_dt = datetime.datetime.combine(today, datetime.time.max, tzinfo=datetime.timezone.utc)

    if from_date_str:
        try:
            from_date = datetime.date.fromisoformat(str(from_date_str).strip())
            start_dt = datetime.datetime.combine(from_date, datetime.time.min, tzinfo=datetime.timezone.utc)
        except ValueError:
            start_dt = datetime.datetime.combine(today - datetime.timedelta(days=default_days), datetime.time.min, tzinfo=datetime.timezone.utc)
    else:
        start_dt = datetime.datetime.combine(today - datetime.timedelta(days=default_days), datetime.time.min, tzinfo=datetime.timezone.utc)

    if start_dt > end_dt:
        start_dt, end_dt = end_dt - datetime.timedelta(days=default_days), end_dt

    # Guard against excessively large date ranges (> 400 days)
    delta_days = (end_dt - start_dt).days
    if delta_days > MAX_WINDOW_DAYS:
        start_dt = end_dt - datetime.timedelta(days=MAX_WINDOW_DAYS)

    return start_dt, end_dt


def resolve_permitted_sites(
    membership: Membership,
    organization: Organization,
    section: str = "operations",
    requested_site_id: str | None = None,
) -> list[Site]:
    """Resolves sites accessible to the membership for the dashboard section.
    
    Enforces intersection of `report.view` and section data permissions (D-052).
    """
    if not membership or not membership.is_active:
        raise PermissionDenied("Active membership required.")

    # 1. Base permission: report.view
    report_scope = rbac.site_scope(membership, "report.view")

    # 2. Section specific permission
    section_perm_map = {
        "operations": "work_order.view",
        "assets": "asset.view",
        "maintenance": "work_order.view",
        "service": "incident.view" if rbac.catalog.is_registered("incident.view") else "work_order.view",
        "inventory": "inventory.part.view" if rbac.catalog.is_registered("inventory.part.view") else "work_order.view",
        "my_work": None,
    }
    sec_perm = section_perm_map.get(section)

    sites_qs = Site.objects.filter(organization=organization, status=Site.Status.ACTIVE)

    if not report_scope.all_sites:
        sites_qs = sites_qs.filter(pk__in=report_scope.site_ids)

    if sec_perm:
        sec_scope = rbac.site_scope(membership, sec_perm)
        if not sec_scope.all_sites:
            sites_qs = sites_qs.filter(pk__in=sec_scope.site_ids)

    sites = list(sites_qs.order_by("code"))

    if requested_site_id:
        req_val = str(requested_site_id).strip()
        filtered = [s for s in sites if str(s.pk) == req_val or s.code.lower() == req_val.lower()]
        if not filtered:
            raise PermissionDenied("Requested site is outside permitted scope.")
        return filtered

    return sites


# --- METRIC CALCULATION ENGINES -------------------------------------------------------------


def compute_mttr(organization: Organization, sites: list[Site], start_dt: datetime.datetime, end_dt: datetime.datetime) -> dict[str, Any]:
    """Calculates Mean Time to Repair (MTTR) in hours."""
    site_ids = [s.pk for s in sites]
    downtimes = Downtime.objects.filter(
        organization=organization,
        ended_at__isnull=False,
        ended_at__gte=start_dt,
        ended_at__lte=end_dt,
        asset__site_id__in=site_ids,
    ).select_related("asset")

    total_seconds = 0.0
    count = 0
    for dt in downtimes:
        if dt.ended_at and dt.started_at:
            dur = (dt.ended_at - dt.started_at).total_seconds()
            if dur >= 0:
                total_seconds += dur
                count += 1

    if count == 0:
        return {"value": "n/a", "numeric": None, "count": 0, "unit": "hours"}

    hours = round(total_seconds / (count * 3600.0), 2)
    return {"value": f"{hours:.2f} h", "numeric": hours, "count": count, "unit": "hours"}


def compute_downtime_hours(organization: Organization, sites: list[Site], start_dt: datetime.datetime, end_dt: datetime.datetime) -> dict[str, Any]:
    """Calculates total overlapping equipment downtime hours in window."""
    site_ids = [s.pk for s in sites]
    now = timezone.now()
    downtimes = Downtime.objects.filter(
        organization=organization,
        asset__site_id__in=site_ids,
        started_at__lte=end_dt,
    ).filter(Q(ended_at__isnull=True) | Q(ended_at__gte=start_dt))

    total_seconds = 0.0
    active_count = 0
    closed_count = 0

    for dt in downtimes:
        window_start = max(dt.started_at, start_dt)
        actual_end = dt.ended_at or min(now, end_dt)
        window_end = min(actual_end, end_dt)
        if window_end > window_start:
            total_seconds += (window_end - window_start).total_seconds()
        if dt.ended_at is None:
            active_count += 1
        else:
            closed_count += 1

    hours = round(total_seconds / 3600.0, 1)
    return {
        "value": f"{hours:.1f} h",
        "numeric": hours,
        "active_outages": active_count,
        "closed_outages": closed_count,
        "unit": "hours",
    }


def compute_mtbf(organization: Organization, sites: list[Site], start_dt: datetime.datetime, end_dt: datetime.datetime, downtime_hours: float | None = None) -> dict[str, Any]:
    """Calculates Mean Time Between Failures (MTBF) in hours."""
    site_ids = [s.pk for s in sites]
    assets_count = Asset.objects.filter(
        organization=organization,
        site_id__in=site_ids,
    ).exclude(status__in=[Asset.Status.RETIRED, Asset.Status.DISPOSED]).count()

    failures_started = Downtime.objects.filter(
        organization=organization,
        asset__site_id__in=site_ids,
        started_at__gte=start_dt,
        started_at__lte=end_dt,
    ).count()

    if failures_started == 0:
        return {"value": "n/a", "numeric": None, "failures": 0, "assets": assets_count, "unit": "hours"}

    window_hours = (end_dt - start_dt).total_seconds() / 3600.0
    if downtime_hours is None:
        dt_res = compute_downtime_hours(organization, sites, start_dt, end_dt)
        downtime_hours = dt_res["numeric"] or 0.0

    total_operating_hours = max(0.0, (assets_count * window_hours) - downtime_hours)
    mtbf_val = round(total_operating_hours / failures_started, 1)

    return {
        "value": f"{mtbf_val:.1f} h",
        "numeric": mtbf_val,
        "failures": failures_started,
        "assets": assets_count,
        "unit": "hours",
    }


def compute_technician_utilization(organization: Organization, sites: list[Site], start_dt: datetime.datetime, end_dt: datetime.datetime) -> dict[str, Any]:
    """Calculates technician utilization based on logged labor hours vs 8h capacity."""
    site_ids = [s.pk for s in sites]
    labor_entries = WorkOrderLabor.objects.filter(
        organization=organization,
        work_order__site_id__in=site_ids,
        work_date__gte=start_dt.date(),
        work_date__lte=end_dt.date(),
    )

    agg = labor_entries.aggregate(total_hours=Sum("hours"), tech_count=Count("technician", distinct=True))
    total_hours = float(agg["total_hours"] or Decimal("0.0"))
    distinct_techs = agg["tech_count"] or 0

    # Fallback to organization active technician members count if labor was 0
    if distinct_techs == 0:
        tech_roles = Role.objects.filter(organization=organization, system_key="technician")
        distinct_techs = MembershipRole.objects.filter(
            membership__organization=organization,
            membership__status="ACTIVE",
            role__in=tech_roles,
        ).values("membership").distinct().count()

    days = max(1, (end_dt.date() - start_dt.date()).days + 1)
    effective_techs = max(1, distinct_techs)
    available_capacity = effective_techs * days * 8.0

    pct = round((total_hours / available_capacity) * 100.0, 1) if available_capacity > 0 else 0.0
    pct = min(100.0, pct)  # Clamp display to 100%

    return {
        "value": f"{pct:.1f}%",
        "numeric": pct,
        "total_hours": total_hours,
        "technicians": distinct_techs,
        "capacity_hours": available_capacity,
        "unit": "%",
    }


def compute_pm_compliance(organization: Organization, sites: list[Site], start_dt: datetime.datetime, end_dt: datetime.datetime) -> dict[str, Any]:
    """Calculates Preventive Maintenance Compliance percentage."""
    site_ids = [s.pk for s in sites]
    now = timezone.now()
    cutoff_end = min(end_dt, now)

    pm_orders = WorkOrder.objects.filter(
        organization=organization,
        site_id__in=site_ids,
        work_type=WorkOrder.WorkType.PREVENTIVE,
        due_at__isnull=False,
        due_at__gte=start_dt,
        due_at__lte=cutoff_end,
    ).exclude(status=WorkOrder.Status.CANCELLED)

    on_time = 0
    late = 0
    missed = 0

    for wo in pm_orders:
        if wo.status in (WorkOrder.Status.COMPLETED, WorkOrder.Status.CLOSED, WorkOrder.Status.SUPERVISOR_REVIEW):
            effective_end = wo.completed_at or wo.closed_at or wo.updated_at
            if effective_end and wo.due_at and effective_end <= wo.due_at:
                on_time += 1
            else:
                late += 1
        else:
            if wo.due_at and now > wo.due_at:
                missed += 1

    total_due = on_time + late + missed
    if total_due == 0:
        return {"value": "100.0%", "numeric": 100.0, "total_due": 0, "on_time": 0, "late": 0, "missed": 0, "unit": "%"}

    rate = round((on_time / total_due) * 100.0, 1)
    return {
        "value": f"{rate:.1f}%",
        "numeric": rate,
        "total_due": total_due,
        "on_time": on_time,
        "late": late,
        "missed": missed,
        "unit": "%",
    }


def compute_parts_consumption(organization: Organization, sites: list[Site], start_dt: datetime.datetime, end_dt: datetime.datetime) -> dict[str, Any]:
    """Calculates net spare parts consumed (ISSUE - RETURN)."""
    site_ids = [s.pk for s in sites]
    movements = StockMovement.objects.filter(
        organization=organization,
        warehouse__site_id__in=site_ids,
        created_at__gte=start_dt,
        created_at__lte=end_dt,
    )

    issued = float(movements.filter(movement_type=StockMovement.Type.ISSUE).aggregate(s=Sum("quantity"))["s"] or Decimal("0"))
    returned = float(movements.filter(movement_type=StockMovement.Type.RETURN).aggregate(s=Sum("quantity"))["s"] or Decimal("0"))
    net = max(0.0, issued - returned)

    # Top consumed parts
    top_parts = (
        movements.filter(movement_type=StockMovement.Type.ISSUE)
        .values("part__part_number", "part__name", "part__unit")
        .annotate(total_qty=Sum("quantity"))
        .order_by("-total_qty")[:5]
    )

    return {
        "value": f"{int(net) if net.is_integer() else net:.1f} units",
        "numeric": net,
        "issued": issued,
        "returned": returned,
        "top_parts": list(top_parts),
        "unit": "units",
    }


def compute_work_order_snapshots(organization: Organization, sites: list[Site]) -> dict[str, Any]:
    """Snapshot KPIs for work orders (open, overdue, status distribution)."""
    site_ids = [s.pk for s in sites]
    now = timezone.now()

    all_orders = WorkOrder.objects.filter(organization=organization, site_id__in=site_ids)

    open_orders = all_orders.filter(
        status__in=[
            WorkOrder.Status.DRAFT,
            WorkOrder.Status.PLANNED,
            WorkOrder.Status.ASSIGNED,
            WorkOrder.Status.DISPATCHED,
            WorkOrder.Status.IN_PROGRESS,
            WorkOrder.Status.ON_HOLD,
        ]
    )
    open_count = open_orders.count()

    overdue_count = open_orders.filter(Q(planned_end__lt=now) | (Q(planned_end__isnull=True) & Q(due_at__lt=now))).count()

    status_dist = dict(all_orders.values_list("status").annotate(c=Count("id")).order_by("-c"))
    priority_dist = dict(open_orders.values_list("priority").annotate(c=Count("id")).order_by("-c"))

    return {
        "open_count": open_count,
        "overdue_count": overdue_count,
        "status_distribution": status_dist,
        "priority_distribution": priority_dist,
    }


def compute_sla_breaches(organization: Organization, sites: list[Site], start_dt: datetime.datetime, end_dt: datetime.datetime) -> dict[str, Any]:
    """Calculates SLA breaches recorded within the window."""
    site_ids = [s.pk for s in sites]
    breaches = SLABreach.objects.filter(
        organization=organization,
        site_id__in=site_ids,
        breached_at__gte=start_dt,
        breached_at__lte=end_dt,
    )
    total_breaches = breaches.count()
    open_breaches = breaches.filter(status=SLABreach.Status.OPEN).count()
    response_breaches = breaches.filter(target_kind="RESPONSE").count()
    resolution_breaches = breaches.filter(target_kind="RESOLUTION").count()

    return {
        "value": str(total_breaches),
        "numeric": total_breaches,
        "open_breaches": open_breaches,
        "response_breaches": response_breaches,
        "resolution_breaches": resolution_breaches,
        "unit": "count",
    }


# --- HIGH LEVEL SECTION AGGREGATORS ---------------------------------------------------------


def get_operations_dashboard(organization: Organization, sites: list[Site], start_dt: datetime.datetime, end_dt: datetime.datetime) -> dict[str, Any]:
    """Consolidates Operations Section metrics."""
    wo_snap = compute_work_order_snapshots(organization, sites)
    mttr_res = compute_mttr(organization, sites, start_dt, end_dt)
    dt_res = compute_downtime_hours(organization, sites, start_dt, end_dt)
    util_res = compute_technician_utilization(organization, sites, start_dt, end_dt)
    pm_res = compute_pm_compliance(organization, sites, start_dt, end_dt)
    sla_res = compute_sla_breaches(organization, sites, start_dt, end_dt)

    return {
        "section": "operations",
        "date_range": {"from": start_dt.date().isoformat(), "to": end_dt.date().isoformat()},
        "sites_count": len(sites),
        "kpis": {
            "open_work_orders": wo_snap["open_count"],
            "overdue_work_orders": wo_snap["overdue_count"],
            "mttr": mttr_res["value"],
            "downtime_hours": dt_res["value"],
            "technician_utilization": util_res["value"],
            "pm_compliance": pm_res["value"],
            "sla_breaches": sla_res["value"],
        },
        "details": {
            "mttr_data": mttr_res,
            "downtime_data": dt_res,
            "utilization_data": util_res,
            "pm_data": pm_res,
            "sla_data": sla_res,
            "status_distribution": wo_snap["status_distribution"],
            "priority_distribution": wo_snap["priority_distribution"],
        },
    }


def get_assets_dashboard(organization: Organization, sites: list[Site], start_dt: datetime.datetime, end_dt: datetime.datetime) -> dict[str, Any]:
    """Consolidates Assets Section metrics (Downtime, MTBF, Top Failing Equipment)."""
    site_ids = [s.pk for s in sites]
    assets_qs = Asset.objects.filter(organization=organization, site_id__in=site_ids)
    total_assets = assets_qs.count()
    active_assets = assets_qs.filter(status=Asset.Status.ACTIVE).count()
    maintenance_assets = assets_qs.filter(status=Asset.Status.UNDER_MAINTENANCE).count()

    dt_res = compute_downtime_hours(organization, sites, start_dt, end_dt)
    mtbf_res = compute_mtbf(organization, sites, start_dt, end_dt, dt_res["numeric"])

    # Top failing assets
    top_failing = list(
        Downtime.objects.filter(
            organization=organization,
            asset__site_id__in=site_ids,
            started_at__gte=start_dt,
            started_at__lte=end_dt,
        )
        .values("asset__asset_tag", "asset__name", "asset__site__name")
        .annotate(outage_count=Count("id"))
        .order_by("-outage_count")[:5]
    )

    return {
        "section": "assets",
        "date_range": {"from": start_dt.date().isoformat(), "to": end_dt.date().isoformat()},
        "kpis": {
            "total_assets": total_assets,
            "active_assets": active_assets,
            "maintenance_assets": maintenance_assets,
            "downtime_hours": dt_res["value"],
            "mtbf": mtbf_res["value"],
        },
        "details": {
            "downtime_data": dt_res,
            "mtbf_data": mtbf_res,
            "top_failing_assets": top_failing,
        },
    }


def get_maintenance_dashboard(organization: Organization, sites: list[Site], start_dt: datetime.datetime, end_dt: datetime.datetime) -> dict[str, Any]:
    """Consolidates Maintenance Section metrics (PM Compliance, PM vs Corrective ratio)."""
    site_ids = [s.pk for s in sites]
    pm_res = compute_pm_compliance(organization, sites, start_dt, end_dt)

    orders = WorkOrder.objects.filter(
        organization=organization,
        site_id__in=site_ids,
        created_at__gte=start_dt,
        created_at__lte=end_dt,
    )
    corrective_count = orders.filter(work_type=WorkOrder.WorkType.CORRECTIVE).count()
    preventive_count = orders.filter(work_type=WorkOrder.WorkType.PREVENTIVE).count()
    emergency_count = orders.filter(work_type=WorkOrder.WorkType.EMERGENCY).count()

    return {
        "section": "maintenance",
        "date_range": {"from": start_dt.date().isoformat(), "to": end_dt.date().isoformat()},
        "kpis": {
            "pm_compliance": pm_res["value"],
            "scheduled_pms": pm_res["total_due"],
            "on_time_pms": pm_res["on_time"],
            "missed_pms": pm_res["missed"],
        },
        "details": {
            "pm_data": pm_res,
            "work_type_mix": {
                "Preventive": preventive_count,
                "Corrective": corrective_count,
                "Emergency": emergency_count,
            },
        },
    }


def get_service_dashboard(organization: Organization, sites: list[Site], start_dt: datetime.datetime, end_dt: datetime.datetime) -> dict[str, Any]:
    """Consolidates Service & Requests Section metrics."""
    site_ids = [s.pk for s in sites]
    requests_qs = ServiceRequest.objects.filter(
        organization=organization,
        site_id__in=site_ids,
    )
    open_requests = requests_qs.exclude(status__in=["RESOLVED", "CLOSED", "CANCELLED"]).count()
    total_new = requests_qs.filter(created_at__gte=start_dt, created_at__lte=end_dt).count()
    sla_res = compute_sla_breaches(organization, sites, start_dt, end_dt)

    return {
        "section": "service",
        "date_range": {"from": start_dt.date().isoformat(), "to": end_dt.date().isoformat()},
        "kpis": {
            "open_requests": open_requests,
            "new_requests_in_window": total_new,
            "sla_breaches": sla_res["value"],
        },
        "details": {
            "sla_data": sla_res,
        },
    }


def get_inventory_dashboard(organization: Organization, sites: list[Site], start_dt: datetime.datetime, end_dt: datetime.datetime) -> dict[str, Any]:
    """Consolidates Inventory Section metrics (Parts consumption, low stock count)."""
    site_ids = [s.pk for s in sites]
    balances = StockBalance.objects.filter(organization=organization, warehouse__site_id__in=site_ids)
    total_lines = balances.count()
    low_stock_count = 0
    for b in balances:
        if b.is_low:
            low_stock_count += 1

    parts_res = compute_parts_consumption(organization, sites, start_dt, end_dt)

    return {
        "section": "inventory",
        "date_range": {"from": start_dt.date().isoformat(), "to": end_dt.date().isoformat()},
        "kpis": {
            "total_stock_lines": total_lines,
            "low_stock_alerts": low_stock_count,
            "net_parts_consumed": parts_res["value"],
        },
        "details": {
            "parts_data": parts_res,
        },
    }


def get_my_work_dashboard(membership: Membership, start_dt: datetime.datetime, end_dt: datetime.datetime) -> dict[str, Any]:
    """Dedicated metrics for authenticated field technician (own assigned jobs and labor)."""
    org = membership.organization
    my_orders = WorkOrder.objects.filter(organization=org, assigned_to=membership)

    open_jobs = my_orders.filter(
        status__in=[WorkOrder.Status.ASSIGNED, WorkOrder.Status.DISPATCHED, WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD]
    ).count()

    in_progress = my_orders.filter(status=WorkOrder.Status.IN_PROGRESS).count()
    completed_in_window = my_orders.filter(
        status=WorkOrder.Status.COMPLETED,
        completed_at__gte=start_dt,
        completed_at__lte=end_dt,
    ).count()

    my_labor = WorkOrderLabor.objects.filter(
        organization=org,
        technician=membership,
        work_date__gte=start_dt.date(),
        work_date__lte=end_dt.date(),
    )
    total_hours = float(my_labor.aggregate(s=Sum("hours"))["s"] or Decimal("0.0"))

    days = max(1, (end_dt.date() - start_dt.date()).days + 1)
    capacity = days * 8.0
    util_pct = round((total_hours / capacity) * 100.0, 1) if capacity > 0 else 0.0

    return {
        "section": "my_work",
        "technician_name": membership.user.display_name if hasattr(membership.user, "display_name") else membership.user.email,
        "date_range": {"from": start_dt.date().isoformat(), "to": end_dt.date().isoformat()},
        "kpis": {
            "open_jobs": open_jobs,
            "active_in_progress": in_progress,
            "completed_in_window": completed_in_window,
            "logged_hours": f"{total_hours:.1f} h",
            "utilization": f"{util_pct:.1f}%",
        },
        "details": {
            "total_labor_hours": total_hours,
            "available_capacity_hours": capacity,
        },
    }
