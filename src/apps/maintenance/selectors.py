"""M04 read side: everything starts from the caller's organization and is restricted to the sites where the caller
holds ``maintenance.view`` (the plan's site = its asset's site). The PM state of a cycle is DERIVED from the M06
work order, never stored twice."""
from __future__ import annotations

import uuid
from datetime import datetime

from django.db.models import Q

from apps.rbac import services as rbac
from apps.sites.selectors import scoped_get

from . import services
from .models import MaintenanceCycle, MaintenancePlan, MaintenanceSchedule

CYCLE_STATES = {  # work-order status -> PM lifecycle state (HPE: GENERATED -> ASSIGNED -> COMPLETED -> VERIFIED)
    "DRAFT": "GENERATED", "PLANNED": "GENERATED", "ASSIGNED": "ASSIGNED", "DISPATCHED": "ASSIGNED",
    "IN_PROGRESS": "ASSIGNED", "ON_HOLD": "ASSIGNED", "COMPLETED": "COMPLETED", "SUPERVISOR_REVIEW": "COMPLETED",
    "CLOSED": "VERIFIED", "CANCELLED": "CANCELLED",
}


def cycle_state(cycle: MaintenanceCycle) -> str:
    return CYCLE_STATES.get(cycle.work_order.status, "GENERATED") if cycle.work_order_id else "GENERATED"


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def plans_for(membership, org):
    scope = rbac.site_scope(membership, "maintenance.view")
    return scope.filter(MaintenancePlan.objects.for_organization(org).select_related("asset", "site"), "site_id")


def get_plan(membership, org, pk) -> MaintenancePlan:
    return scoped_get(plans_for(membership, org), pk, "Maintenance plan")


def filter_plans(qs, params):
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(asset__asset_tag__icontains=q) | Q(asset__name__icontains=q))
    for param, field in (("site", "site_id"), ("asset", "asset_id")):
        value = (params.get(param) or "").strip()
        if value:
            parsed = _uuid_or_none(value)
            qs = qs.filter(**{field: parsed}) if parsed else qs.none()
    active = (params.get("active") or "").strip()
    if active in ("1", "true"):
        qs = qs.filter(is_active=True)
    elif active in ("0", "false"):
        qs = qs.filter(is_active=False)
    return qs.order_by("name")


def schedules_for(membership, org):
    scope = rbac.site_scope(membership, "maintenance.view")
    qs = MaintenanceSchedule.objects.for_organization(org).select_related("plan__asset", "plan__site", "meter")
    return scope.filter(qs, "plan__site_id")


def get_schedule(membership, org, pk) -> MaintenanceSchedule:
    return scoped_get(schedules_for(membership, org), pk, "Maintenance schedule")


def cycles_for(membership, org):
    scope = rbac.site_scope(membership, "maintenance.view")
    qs = MaintenanceCycle.objects.for_organization(org).select_related(
        "schedule__plan__asset", "schedule__plan__site", "schedule__meter", "work_order")
    return scope.filter(qs, "schedule__plan__site_id")


def filter_cycles(qs, params):
    for param, field in (("plan", "schedule__plan_id"), ("schedule", "schedule_id"), ("asset", "schedule__plan__asset_id")):
        value = (params.get(param) or "").strip()
        if value:
            parsed = _uuid_or_none(value)
            qs = qs.filter(**{field: parsed}) if parsed else qs.none()
    return qs.order_by("-created_at")


def schedule_state(sch: MaintenanceSchedule, now: datetime | None = None) -> str:
    """DISABLED / DUE (a due occurrence exists, incl. overdue) / SCHEDULED (waiting for the next occurrence)."""
    if services.blocked_reason(sch):
        return "DISABLED"
    return "DUE" if services.evaluate(sch, now) is not None else "SCHEDULED"


def describe_schedule_rows(schedules, now: datetime | None = None) -> list[dict]:
    rows = []
    for sch in schedules:
        rows.append({"schedule": sch, "state": schedule_state(sch, now), "blocked": services.blocked_reason(sch)})
    return rows
