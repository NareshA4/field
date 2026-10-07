"""M06 read side. A caller sees a work order when ``work_order.view`` covers its site, or when it is assigned to
them and ``work_order.view_assigned`` covers its site (technicians). Everything is organization-scoped first."""
from __future__ import annotations

import uuid

from django.db.models import Q, Sum
from django.utils import timezone

from apps.rbac import services as rbac
from apps.sites.selectors import scoped_get

from .models import WorkOrder, WorkOrderEvent, WorkOrderLabor, WorkOrderMaterial

ORDERINGS = {"-created_at": "-created_at", "created_at": "created_at", "number": "number", "-number": "-number",
             "priority": "priority", "status": "status", "planned_start": "planned_start",
             "-planned_start": "-planned_start"}


def work_orders_for(membership, org):
    qs = WorkOrder.objects.for_organization(org).select_related("asset", "site", "assigned_to__user",
                                                                 "source_request")
    full = rbac.site_scope(membership, "work_order.view")
    if full.all_sites:
        return qs
    cond = Q(pk__isnull=True)  # matches nothing
    if full.site_ids:
        cond |= Q(site_id__in=full.site_ids)
    mine = rbac.site_scope(membership, "work_order.view_assigned")
    if mine.all_sites:
        cond |= Q(assigned_to=membership)
    elif mine.site_ids:
        cond |= Q(assigned_to=membership, site_id__in=mine.site_ids)
    return qs.filter(cond)


def get_work_order(membership, org, pk) -> WorkOrder:
    return scoped_get(work_orders_for(membership, org), pk, "Work order")


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def filter_work_orders(qs, params, membership=None):
    for param, field in (("site", "site_id"), ("asset", "asset_id"), ("assigned_to", "assigned_to_id"),
                         ("source_request", "source_request_id")):
        value = (params.get(param) or "").strip()
        if value:
            parsed = _uuid_or_none(value)
            qs = qs.filter(**{field: parsed}) if parsed else qs.none()
    for param, field, choices in (("status", "status", WorkOrder.Status.values),
                                  ("priority", "priority", WorkOrder.Priority.values),
                                  ("work_type", "work_type", WorkOrder.WorkType.values)):
        value = (params.get(param) or "").strip()
        if value:
            qs = qs.filter(**{field: value}) if value in choices else qs.none()
    if (params.get("open") or "") in ("1", "true"):  # dashboard drill-down: everything not closed / cancelled
        qs = qs.exclude(status__in=("CLOSED", "CANCELLED"))
    if (params.get("overdue") or "") in ("1", "true"):  # past the planned end and not yet completed
        qs = qs.filter(status__in=("DRAFT", "PLANNED", "ASSIGNED", "DISPATCHED", "IN_PROGRESS", "ON_HOLD"),
                       planned_end__lt=timezone.now())
    if membership is not None and (params.get("mine") or "") in ("1", "true"):
        qs = qs.filter(assigned_to=membership)
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(title__icontains=q) | Q(asset__asset_tag__icontains=q)
                       | Q(asset__name__icontains=q))
    return qs.order_by(ORDERINGS.get(params.get("ordering") or "", "-created_at"), "-id")


def events_for(org, wo: WorkOrder):
    return WorkOrderEvent.objects.for_organization(org).filter(work_order=wo).select_related("actor", "assigned_to__user")


def labor_for(org, wo: WorkOrder):
    return WorkOrderLabor.objects.for_organization(org).filter(work_order=wo).select_related("technician__user")


def materials_for(org, wo: WorkOrder):
    return WorkOrderMaterial.objects.for_organization(org).filter(work_order=wo)


def total_hours(org, wo: WorkOrder):
    return labor_for(org, wo).aggregate(t=Sum("hours"))["t"] or 0
