"""M09 read side. Everything starts from the caller's organization; warehouse-bound records (balances, movements,
reservations) are further restricted to the sites where the caller holds ``inventory.view``; the catalogue is
organization-wide. Part lines of a work order follow the caller's visibility of that work order (M06 selectors)."""
from __future__ import annotations

import uuid

from django.db.models import F, Q, Sum

from apps.rbac import services as rbac
from apps.sites.selectors import scoped_get

from .models import Part, PartReservation, StockBalance, StockMovement, Warehouse, WorkOrderPart


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _uuid_filter(qs, params, param, field):
    value = (params.get(param) or "").strip()
    if value:
        parsed = _uuid_or_none(value)
        return qs.filter(**{field: parsed}) if parsed else qs.none()
    return qs


# --- catalogue ---------------------------------------------------------------------------------------------------


def parts_for(org):
    return Part.objects.for_organization(org)


def get_part(org, pk) -> Part:
    return scoped_get(parts_for(org), pk, "Part")


def filter_parts(qs, params):
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(part_number__icontains=q) | Q(name__icontains=q))
    active = (params.get("active") or "").strip()
    if active in ("1", "true"):
        qs = qs.filter(is_active=True)
    elif active in ("0", "false"):
        qs = qs.filter(is_active=False)
    return qs.order_by("part_number")


def part_totals(membership, org, part: Part) -> dict:
    """Stock of one part over the warehouses the caller may see."""
    agg = balances_for(membership, org).filter(part=part).aggregate(on_hand=Sum("on_hand"), reserved=Sum("reserved"))
    on_hand, reserved = agg["on_hand"] or 0, agg["reserved"] or 0
    return {"on_hand": on_hand, "reserved": reserved, "available": on_hand - reserved}


# --- warehouses ---------------------------------------------------------------------------------------------------


def warehouses_for(membership, org, code: str = "inventory.view"):
    scope = rbac.site_scope(membership, code)
    return scope.filter(Warehouse.objects.for_organization(org).select_related("site"), "site_id")


def get_warehouse(membership, org, pk, code: str = "inventory.view") -> Warehouse:
    return scoped_get(warehouses_for(membership, org, code), pk, "Warehouse")


def filter_warehouses(qs, params):
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))
    qs = _uuid_filter(qs, params, "site", "site_id")
    active = (params.get("active") or "").strip()
    if active in ("1", "true"):
        qs = qs.filter(is_active=True)
    elif active in ("0", "false"):
        qs = qs.filter(is_active=False)
    return qs.order_by("code")


# --- balances / movements / reservations ---------------------------------------------------------------------------


def balances_for(membership, org):
    scope = rbac.site_scope(membership, "inventory.view")
    qs = StockBalance.objects.for_organization(org).select_related("warehouse__site", "part")
    return scope.filter(qs, "warehouse__site_id")


def get_balance(membership, org, pk) -> StockBalance:
    return scoped_get(balances_for(membership, org), pk, "Stock balance")


def filter_balances(qs, params):
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(part__part_number__icontains=q) | Q(part__name__icontains=q))
    qs = _uuid_filter(qs, params, "warehouse", "warehouse_id")
    qs = _uuid_filter(qs, params, "part", "part_id")
    qs = _uuid_filter(qs, params, "site", "warehouse__site_id")
    if (params.get("low") or "") in ("1", "true"):
        qs = qs.filter(
            Q(min_level__isnull=False, on_hand__lte=F("min_level") + F("reserved"))
            | Q(min_level__isnull=True, part__min_stock__isnull=False,
                on_hand__lte=F("part__min_stock") + F("reserved")))
    if (params.get("in_stock") or "") in ("1", "true"):
        qs = qs.filter(on_hand__gt=0)
    return qs.order_by("warehouse__code", "part__part_number")


def movements_for(membership, org):
    scope = rbac.site_scope(membership, "inventory.view")
    qs = StockMovement.objects.for_organization(org).select_related("warehouse", "part", "work_order", "actor")
    return scope.filter(qs, "warehouse__site_id")


def filter_movements(qs, params):
    from .models import StockMovement as M

    for param, field in (("warehouse", "warehouse_id"), ("part", "part_id"), ("work_order", "work_order_id"),
                         ("balance", "balance_id")):
        qs = _uuid_filter(qs, params, param, field)
    kind = (params.get("type") or "").strip()
    if kind:
        qs = qs.filter(movement_type=kind) if kind in M.Type.values else qs.none()
    return qs.order_by("-created_at", "-id")


def reservations_for(membership, org):
    scope = rbac.site_scope(membership, "inventory.view")
    qs = PartReservation.objects.for_organization(org).select_related("warehouse", "part", "work_order")
    return scope.filter(qs, "warehouse__site_id")


def filter_reservations(qs, params):
    qs = _uuid_filter(qs, params, "work_order", "work_order_id")
    qs = _uuid_filter(qs, params, "warehouse", "warehouse_id")
    status = (params.get("status") or "").strip()
    if status:
        qs = qs.filter(status=status) if status in PartReservation.Status.values else qs.none()
    return qs.order_by("-updated_at")


# --- work-order part lines ----------------------------------------------------------------------------------------


def lines_for_work_order(org, wo):
    return WorkOrderPart.objects.for_organization(org).filter(work_order=wo).select_related(
        "part", "warehouse", "reservation")


def get_line(membership, org, pk) -> WorkOrderPart:
    """A part line is visible when its work order is (M06 visibility: site scope or assignment)."""
    from apps.workorders.selectors import work_orders_for

    visible = work_orders_for(membership, org).values("pk")
    qs = WorkOrderPart.objects.for_organization(org).filter(work_order__in=visible).select_related(
        "part", "warehouse", "work_order__site")
    return scoped_get(qs, pk, "Part line")


def lines_visible(membership, org):
    from apps.workorders.selectors import work_orders_for

    visible = work_orders_for(membership, org).values("pk")
    return WorkOrderPart.objects.for_organization(org).filter(work_order__in=visible).select_related(
        "part", "warehouse", "work_order")


def usable_warehouses(membership, org, site_id, code: str):
    """Active warehouses at the work order's site where the caller holds ``code`` (for the issue / reserve forms)."""
    return warehouses_for(membership, org, code).filter(site_id=site_id, is_active=True).order_by("code")
