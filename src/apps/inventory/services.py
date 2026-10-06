"""M09 service layer: the ONLY place stock changes.

Callers (API / UI) resolve objects through organization- and site-scoped selectors and check the permission for the
warehouse's / work order's site first. This layer enforces the rest: tenant ownership of every reference, warehouse
site == work-order site, active part / warehouse, quantity validity, no negative stock, reserved <= on-hand,
row locks (``select_for_update``: work order -> part line -> balance, always in that order; two balances in
primary-key order), exactly one append-only ``StockMovement`` per balance change in the same transaction, and one
audit record per operation.

Boundary: M06 owns the work-order lifecycle (this module only reads the state); consumption is written to the M06
material list through ``workorders.services.record_material``; M06 calls ``part_blockers`` /
``on_work_order_closed`` / ``on_work_order_cancelled`` from its own transitions.
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction

from apps.audit import services as audit
from apps.core.exceptions import Conflict, PermissionDenied, ValidationFailed
from apps.rbac import services as rbac

from .models import Part, PartReservation, StockBalance, StockMovement, Warehouse, WorkOrderPart
from .workflow import (
    CONSUME_STATES,
    CONSUMED,
    ISSUED,
    PART_LINE,
    RECONCILED,
    REQUEST_STATES,
    REQUESTED,
    RESERVED,
    RETURN_STATES,
    RETURNED,
    STOCK_OUT_STATES,
    TERMINAL,
)

ZERO = Decimal("0")
MAX_QTY = Decimal("9999999")
Type = StockMovement.Type


# --- validation helpers ------------------------------------------------------------------------------------------


def quantity_of(value, what: str = "Quantity", *, allow_negative: bool = False) -> Decimal:
    """Strictly validated quantity: finite number, at most 3 decimals, > 0 (or non-zero when negatives allowed)."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationFailed(f"{what} must be a number.", code="invalid_quantity") from exc
    if not d.is_finite():
        raise ValidationFailed(f"{what} must be a number.", code="invalid_quantity")
    if d.as_tuple().exponent < -3:
        raise ValidationFailed(f"{what} can have at most 3 decimal places.", code="invalid_quantity")
    if abs(d) > MAX_QTY:
        raise ValidationFailed(f"{what} is too large.", code="invalid_quantity")
    if d == 0 or (d < 0 and not allow_negative):
        raise ValidationFailed(f"{what} must be greater than zero.", code="invalid_quantity")
    return d


def _optional_level(value, what: str):
    if value in (None, ""):
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationFailed(f"{what} must be a number.", code="invalid_quantity") from exc
    if not d.is_finite() or d < 0 or d > MAX_QTY or d.as_tuple().exponent < -3:
        raise ValidationFailed(f"{what} must be zero or more (max 3 decimals).", code="invalid_quantity")
    return d


def _check_levels(low, high):
    if low is not None and high is not None and high < low:
        raise ValidationFailed("The maximum level cannot be below the minimum level.", code="levels_invalid")


def _reason(reason: str, *, required: bool = False) -> str:
    reason = (reason or "").strip()
    if required and len(reason) < 3:
        raise ValidationFailed("A reason is required.", code="reason_required")
    return reason[:300]


def _same_org(org, *objs):
    for obj in objs:
        if obj is not None and obj.organization_id != org.pk:
            raise ValidationFailed("Referenced record belongs to a different organization.",
                                   code=f"cross_tenant_{obj._meta.model_name}")


def _active_part(part: Part):
    """Reads the flag from the database (callers may hold a stale instance; deactivation may race with a request)."""
    if not Part.objects.filter(pk=part.pk, is_active=True).exists():
        raise Conflict("The part is inactive.", code="part_inactive")


def _active_warehouse(wh: Warehouse):
    if not Warehouse.objects.filter(pk=wh.pk, is_active=True).exists():
        raise Conflict("The warehouse is inactive.", code="warehouse_inactive")


# --- parts -------------------------------------------------------------------------------------------------------

PART_FIELDS = ["part_number", "name", "description", "unit", "min_stock", "max_stock", "reorder_quantity",
               "is_active"]


@transaction.atomic
def create_part(org, *, part_number: str, name: str, unit: str = "pcs", description: str = "", min_stock=None,
                max_stock=None, reorder_quantity=None, actor, request=None) -> Part:
    part_number, name = (part_number or "").strip(), (name or "").strip()
    if not part_number or not name:
        raise ValidationFailed("Part number and name are required.", code="part_required")
    if Part.objects.for_organization(org).filter(part_number__iexact=part_number).exists():
        raise Conflict("A part with this part number already exists.", code="part_number_taken")
    low, high = _optional_level(min_stock, "Minimum stock"), _optional_level(max_stock, "Maximum stock")
    _check_levels(low, high)
    part = Part(organization=org, part_number=part_number[:60], name=name[:200],
                description=(description or "").strip(), unit=(unit or "pcs").strip()[:20] or "pcs",
                min_stock=low, max_stock=high, reorder_quantity=_optional_level(reorder_quantity, "Reorder quantity"))
    part.save()
    audit.record("part.created", actor=actor, organization=org, target=part, after=audit.snapshot(part, PART_FIELDS),
                 request=request)
    return part


@transaction.atomic
def update_part(part: Part, *, actor, request=None, **changes) -> Part:
    part = Part.objects.select_for_update().get(pk=part.pk)
    allowed = {"part_number", "name", "description", "unit", "min_stock", "max_stock", "reorder_quantity"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValidationFailed(f"Fields cannot be edited: {', '.join(sorted(unknown))}.", code="field_not_editable")
    before = audit.snapshot(part, PART_FIELDS)
    if "part_number" in changes:
        pn = (changes["part_number"] or "").strip()
        if not pn:
            raise ValidationFailed("Part number is required.", code="part_required")
        if Part.objects.for_organization(part.organization).filter(part_number__iexact=pn).exclude(
                pk=part.pk).exists():
            raise Conflict("A part with this part number already exists.", code="part_number_taken")
        part.part_number = pn[:60]
    if "name" in changes:
        if not (changes["name"] or "").strip():
            raise ValidationFailed("Name is required.", code="part_required")
        part.name = changes["name"].strip()[:200]
    if "description" in changes:
        part.description = (changes["description"] or "").strip()
    if "unit" in changes:
        part.unit = (changes["unit"] or "pcs").strip()[:20] or "pcs"
    if "min_stock" in changes:
        part.min_stock = _optional_level(changes["min_stock"], "Minimum stock")
    if "max_stock" in changes:
        part.max_stock = _optional_level(changes["max_stock"], "Maximum stock")
    if "reorder_quantity" in changes:
        part.reorder_quantity = _optional_level(changes["reorder_quantity"], "Reorder quantity")
    _check_levels(part.min_stock, part.max_stock)
    part.save()
    after = audit.snapshot(part, PART_FIELDS)
    if after != before:
        audit.record("part.updated", actor=actor, organization=part.organization, target=part, before=before,
                     after=after, request=request)
    return part


@transaction.atomic
def set_part_active(part: Part, active: bool, *, actor, request=None) -> Part:
    part = Part.objects.select_for_update().get(pk=part.pk)
    if part.is_active == active:
        return part
    part.is_active = active
    part.save(update_fields=["is_active", "updated_at"])
    audit.record("part.activated" if active else "part.deactivated", actor=actor, organization=part.organization,
                 target=part, before={"is_active": not active}, after={"is_active": active}, request=request)
    return part


# --- warehouses --------------------------------------------------------------------------------------------------

WAREHOUSE_FIELDS = ["code", "name", "description", "site", "is_active"]


@transaction.atomic
def create_warehouse(org, *, site, code: str, name: str, description: str = "", actor, request=None) -> Warehouse:
    _same_org(org, site)
    if site.status != "ACTIVE":
        raise Conflict("Warehouses can only be created at an active site.", code="site_inactive")
    code, name = (code or "").strip(), (name or "").strip()
    if not code or not name:
        raise ValidationFailed("Code and name are required.", code="warehouse_required")
    if Warehouse.objects.for_organization(org).filter(code__iexact=code).exists():
        raise Conflict("A warehouse with this code already exists.", code="warehouse_code_taken")
    wh = Warehouse(organization=org, site=site, code=code[:20], name=name[:120],
                   description=(description or "").strip()[:300])
    wh.save()
    audit.record("warehouse.created", actor=actor, organization=org, target=wh,
                 after=audit.snapshot(wh, WAREHOUSE_FIELDS), request=request)
    return wh


@transaction.atomic
def update_warehouse(wh: Warehouse, *, actor, request=None, **changes) -> Warehouse:
    wh = Warehouse.objects.select_for_update().get(pk=wh.pk)
    unknown = set(changes) - {"name", "description", "code"}
    if unknown:
        raise ValidationFailed(f"Fields cannot be edited: {', '.join(sorted(unknown))}.", code="field_not_editable")
    before = audit.snapshot(wh, WAREHOUSE_FIELDS)
    if "code" in changes:
        code = (changes["code"] or "").strip()
        if not code:
            raise ValidationFailed("Code is required.", code="warehouse_required")
        if Warehouse.objects.for_organization(wh.organization).filter(code__iexact=code).exclude(pk=wh.pk).exists():
            raise Conflict("A warehouse with this code already exists.", code="warehouse_code_taken")
        wh.code = code[:20]
    if "name" in changes:
        if not (changes["name"] or "").strip():
            raise ValidationFailed("Name is required.", code="warehouse_required")
        wh.name = changes["name"].strip()[:120]
    if "description" in changes:
        wh.description = (changes["description"] or "").strip()[:300]
    wh.save()
    after = audit.snapshot(wh, WAREHOUSE_FIELDS)
    if after != before:
        audit.record("warehouse.updated", actor=actor, organization=wh.organization, target=wh, before=before,
                     after=after, request=request)
    return wh


@transaction.atomic
def set_warehouse_active(wh: Warehouse, active: bool, *, actor, request=None) -> Warehouse:
    wh = Warehouse.objects.select_for_update().get(pk=wh.pk)
    if wh.is_active == active:
        return wh
    if not active:
        holds = StockBalance.objects.for_organization(wh.organization).filter(warehouse=wh).exclude(
            on_hand=0, reserved=0).exists()
        if holds:
            raise Conflict("Move or write off the stock before deactivating this warehouse.",
                           code="warehouse_has_stock")
    elif wh.site.status != "ACTIVE":
        raise Conflict("The warehouse's site is not active.", code="site_inactive")
    wh.is_active = active
    wh.save(update_fields=["is_active", "updated_at"])
    audit.record("warehouse.activated" if active else "warehouse.deactivated", actor=actor,
                 organization=wh.organization, target=wh, before={"is_active": not active},
                 after={"is_active": active}, request=request)
    return wh


# --- balance + ledger primitives ---------------------------------------------------------------------------------


def _lock_balance(org, warehouse: Warehouse, part: Part, *, create: bool) -> StockBalance | None:
    qs = StockBalance.objects.for_organization(org).select_for_update(of=("self",))
    try:
        return qs.get(warehouse=warehouse, part=part)
    except StockBalance.DoesNotExist:
        if not create:
            return None
    try:
        with transaction.atomic():  # savepoint: a concurrent creator makes this a harmless unique violation
            StockBalance(organization=org, warehouse=warehouse, part=part).save()
    except IntegrityError:
        pass
    return qs.get(warehouse=warehouse, part=part)


def _ensure_balance(org, warehouse: Warehouse, part: Part) -> None:
    """Makes sure the (warehouse, part) row exists WITHOUT locking it (callers then lock in a fixed order)."""
    if StockBalance.objects.for_organization(org).filter(warehouse=warehouse, part=part).exists():
        return
    try:
        with transaction.atomic():
            StockBalance(organization=org, warehouse=warehouse, part=part).save()
    except IntegrityError:
        pass


def _apply(balance: StockBalance, *, movement_type, quantity: Decimal, on_hand_delta=ZERO, reserved_delta=ZERO,
           actor, work_order=None, line=None, transfer_ref=None, reference="", reason="") -> StockMovement:
    """The single place a balance changes: re-checks the invariants, saves the balance and writes the movement."""
    new_on, new_res = balance.on_hand + on_hand_delta, balance.reserved + reserved_delta
    if new_on < 0:
        raise Conflict("Insufficient stock.", code="insufficient_stock")
    if new_res < 0 or new_res > new_on:
        raise Conflict("Insufficient available stock.", code="insufficient_available")
    if on_hand_delta == 0 and reserved_delta == 0:
        raise ValidationFailed("Nothing to change.", code="no_change")
    balance.on_hand, balance.reserved = new_on, new_res
    balance.save(update_fields=["on_hand", "reserved", "updated_at"])
    movement = StockMovement(
        organization=balance.organization, balance=balance, warehouse_id=balance.warehouse_id,
        part_id=balance.part_id, movement_type=movement_type, quantity=quantity, on_hand_delta=on_hand_delta,
        reserved_delta=reserved_delta, on_hand_after=new_on, reserved_after=new_res, work_order=work_order,
        part_line=line, transfer_ref=transfer_ref, reference=(reference or "")[:100], reason=reason, actor=actor)
    movement.save()
    return movement


def _audit_movement(action: str, movement: StockMovement, *, actor, request, **extra):
    audit.record(action, actor=actor, organization=movement.organization, target=movement.balance,
                 after={"on_hand": movement.on_hand_after, "reserved": movement.reserved_after},
                 metadata={"movement": str(movement.pk), "type": movement.movement_type,
                           "quantity": str(movement.quantity), "part": movement.part.part_number,
                           "warehouse": movement.warehouse.code, **extra}, request=request)


# --- receive / adjust / levels / transfer ------------------------------------------------------------------------


@transaction.atomic
def receive(warehouse: Warehouse, part: Part, quantity, *, actor, reference: str = "", reason: str = "",
            request=None) -> StockMovement:
    org = warehouse.organization
    _same_org(org, part)
    q = quantity_of(quantity)
    _active_part(part)
    _active_warehouse(warehouse)
    bal = _lock_balance(org, warehouse, part, create=True)
    mv = _apply(bal, movement_type=Type.RECEIPT, quantity=q, on_hand_delta=q, actor=actor, reference=reference,
                reason=_reason(reason))
    _audit_movement("stock.received", mv, actor=actor, request=request)
    return mv


@transaction.atomic
def adjust(warehouse: Warehouse, part: Part, delta, *, reason: str, actor, request=None) -> StockMovement:
    """Count correction (signed, non-zero). It can never cut into stock that is reserved."""
    org = warehouse.organization
    _same_org(org, part)
    d = quantity_of(delta, "Adjustment", allow_negative=True)
    reason = _reason(reason, required=True)
    _active_part(part)
    _active_warehouse(warehouse)
    bal = _lock_balance(org, warehouse, part, create=d > 0)
    if bal is None:
        raise Conflict("There is no stock of this part in the warehouse.", code="insufficient_stock")
    if d < 0 and bal.available < -d:
        raise Conflict("The adjustment would cut into reserved stock or go below zero.", code="insufficient_available",
                       details={"available": str(bal.available)})
    mv = _apply(bal, movement_type=Type.ADJUSTMENT, quantity=abs(d), on_hand_delta=d, actor=actor, reason=reason)
    _audit_movement("stock.adjusted", mv, actor=actor, request=request, reason=reason)
    return mv


@transaction.atomic
def set_levels(warehouse: Warehouse, part: Part, *, min_level, max_level, reorder_quantity, actor,
               request=None) -> StockBalance:
    """Per-warehouse min / max / reorder override (creates an empty balance row when none exists; no movement
    because no quantity changes)."""
    org = warehouse.organization
    _same_org(org, part)
    low, high = _optional_level(min_level, "Minimum level"), _optional_level(max_level, "Maximum level")
    _check_levels(low, high)
    bal = _lock_balance(org, warehouse, part, create=True)
    before = audit.snapshot(bal, ["min_level", "max_level", "reorder_quantity"])
    bal.min_level, bal.max_level = low, high
    bal.reorder_quantity = _optional_level(reorder_quantity, "Reorder quantity")
    bal.save(update_fields=["min_level", "max_level", "reorder_quantity", "updated_at"])
    audit.record("stock.levels_changed", actor=actor, organization=org, target=bal, before=before,
                 after=audit.snapshot(bal, ["min_level", "max_level", "reorder_quantity"]), request=request)
    return bal


@transaction.atomic
def transfer(source: Warehouse, target: Warehouse, part: Part, quantity, *, actor, reason: str = "",
             request=None) -> tuple[StockMovement, StockMovement]:
    """Moves available (not reserved) stock between two warehouses of one organization: one TRANSFER_OUT and one
    TRANSFER_IN movement sharing a transfer reference, balances locked in primary-key order."""
    org = source.organization
    _same_org(org, target, part)
    if source.pk == target.pk:
        raise ValidationFailed("Source and destination must be different warehouses.", code="same_warehouse")
    q = quantity_of(quantity)
    _active_part(part)
    _active_warehouse(source)
    _active_warehouse(target)
    src = StockBalance.objects.for_organization(org).filter(warehouse=source, part=part).first()
    if src is None:
        raise Conflict("There is no stock of this part in the source warehouse.", code="insufficient_stock")
    _ensure_balance(org, target, part)  # no lock yet: both rows are locked together, in primary-key order
    locked = {b.warehouse_id: b for b in StockBalance.objects.for_organization(org).select_for_update(of=("self",))
              .filter(part=part, warehouse__in=[source, target]).order_by("pk")}
    src, dst = locked[source.pk], locked[target.pk]
    if src.available < q:
        raise Conflict("Insufficient available stock in the source warehouse.", code="insufficient_available",
                       details={"available": str(src.available)})
    ref, reason = uuid.uuid4(), _reason(reason)
    out = _apply(src, movement_type=Type.TRANSFER_OUT, quantity=q, on_hand_delta=-q, actor=actor, transfer_ref=ref,
                 reason=reason)
    inn = _apply(dst, movement_type=Type.TRANSFER_IN, quantity=q, on_hand_delta=q, actor=actor, transfer_ref=ref,
                 reason=reason)
    audit.record("stock.transferred", actor=actor, organization=org, target=src,
                 metadata={"transfer": str(ref), "part": part.part_number, "quantity": str(q),
                           "from": source.code, "to": target.code, "movements": [str(out.pk), str(inn.pk)]},
                 request=request)
    return out, inn


# --- work-order part lines (M06 integration) -----------------------------------------------------------------------


def _lock_wo(wo):
    from apps.workorders.models import WorkOrder

    return WorkOrder.objects.select_for_update(of=("self",)).select_related("site").get(pk=wo.pk)


def _lock_line(line: WorkOrderPart) -> WorkOrderPart:
    return WorkOrderPart.objects.select_for_update(of=("self",)).select_related("part", "warehouse").get(pk=line.pk)


def _reservation(line: WorkOrderPart) -> PartReservation | None:
    return PartReservation.objects.select_for_update().filter(part_line=line).first()


def _refresh_status(line: WorkOrderPart, reservation: PartReservation | None):
    if line.status in TERMINAL:
        return
    held = reservation.quantity if reservation else ZERO
    if line.quantity_issued == 0:
        line.status = RESERVED if held > 0 else REQUESTED
    elif line.outstanding > 0 or held > 0:
        line.status = ISSUED
    elif line.quantity_consumed > 0:
        line.status = CONSUMED
    else:
        line.status = RETURNED


def _save_line(line, reservation=None):
    _refresh_status(line, reservation)
    if line.warehouse_id and line.quantity_issued - line.quantity_returned == 0 and (
            reservation is None or reservation.quantity == 0) and line.quantity_consumed == 0:
        line.warehouse = None  # nothing is committed to a warehouse any more: free to choose another one
    line.save()


def held_quantity(line: WorkOrderPart) -> Decimal:
    res = PartReservation.objects.for_organization(line.organization).filter(part_line=line).first()
    return res.quantity if res else ZERO


def _line_warehouse(line: WorkOrderPart, wo, warehouse: Warehouse, *, for_stock_out: bool) -> Warehouse:
    _same_org(wo.organization, warehouse)
    if warehouse.site_id != wo.site_id:
        raise ValidationFailed("The warehouse belongs to a different site than the work order.", code="wrong_site")
    if line.warehouse_id and line.warehouse_id != warehouse.pk:
        raise Conflict("This line is already committed to another warehouse; return or release that stock first.",
                       code="wrong_warehouse", details={"warehouse": line.warehouse.code})
    if for_stock_out:
        _active_warehouse(warehouse)
    return warehouse


def _gate(wo, states: tuple, what: str):
    if wo.status not in states:
        raise Conflict(f"{what} is not possible while the work order is {wo.status.replace('_', ' ').lower()}.",
                       code="work_order_state", details={"state": wo.status})


def can_request(wo, membership) -> bool:
    """Who may put a part requirement on a work order: its assignee, dispatchers / reviewers, and stores staff."""
    return (wo.assigned_to_id == membership.pk
            or any(rbac.has_permission(membership, c, wo.site_id)
                   for c in ("work_order.dispatch", "work_order.review", "inventory.reserve", "inventory.issue")))


@transaction.atomic
def request_part(wo, part: Part, quantity, *, actor, membership, notes: str = "", request=None) -> WorkOrderPart:
    """Creates (or tops up) the part requirement of a work order. Moves no stock."""
    wo = _lock_wo(wo)
    _same_org(wo.organization, part)
    q = quantity_of(quantity)
    _active_part(part)
    _gate(wo, REQUEST_STATES, "Requesting parts")
    if not can_request(wo, membership):
        raise PermissionDenied("Only the assigned technician, a dispatcher or stores staff can request parts.",
                               code="not_assignee")
    line = WorkOrderPart.objects.for_organization(wo.organization).select_for_update(of=("self",)).filter(
        work_order=wo, part=part).first()
    if line is None:
        line = WorkOrderPart(organization=wo.organization, work_order=wo, part=part, quantity_requested=q,
                             notes=(notes or "").strip()[:300], requested_by=actor)
        line.save()
        action = "part_line.requested"
    else:
        if line.status == RECONCILED:
            raise Conflict("This part line is reconciled and closed.", code="part_line_locked")
        if line.quantity_requested + q > MAX_QTY:
            raise ValidationFailed("Quantity is too large.", code="invalid_quantity")
        if line.status == "CANCELLED":
            line.status, line.quantity_requested = REQUESTED, q
        else:
            line.quantity_requested += q
        if notes:
            line.notes = notes.strip()[:300]
        _save_line(line, _reservation(line))
        action = "part_line.topped_up"
    audit.record(action, actor=actor, organization=wo.organization, target=line,
                 after={"work_order": wo.number, "part": part.part_number,
                        "quantity_requested": line.quantity_requested}, request=request)
    return line


@transaction.atomic
def reserve(line: WorkOrderPart, warehouse: Warehouse, quantity=None, *, actor, request=None) -> WorkOrderPart:
    """Holds available stock for the line (default: everything still uncovered). Physical stock is untouched; the
    balance's ``reserved`` rises and a RESERVE movement is written."""
    wo = _lock_wo(line.work_order)
    line = _lock_line(line)
    _gate(wo, STOCK_OUT_STATES, "Reserving stock")
    if line.status in TERMINAL:
        raise Conflict("This part line is closed.", code="part_line_locked")
    warehouse = _line_warehouse(line, wo, warehouse, for_stock_out=True)
    _active_part(line.part)
    res = _reservation(line)
    held = res.quantity if res else ZERO
    uncovered = line.remaining_to_issue - held
    if uncovered <= 0:
        raise Conflict("The requirement is already fully covered.", code="already_covered")
    q = quantity_of(quantity) if quantity not in (None, "") else uncovered
    if q > uncovered:
        raise Conflict("Cannot reserve more than the uncovered requirement.", code="exceeds_requirement",
                       details={"uncovered": str(uncovered)})
    bal = _lock_balance(wo.organization, warehouse, line.part, create=False)
    if bal is None or bal.available < q:
        raise Conflict("Insufficient available stock.", code="insufficient_available",
                       details={"available": str(bal.available if bal else ZERO)})
    mv = _apply(bal, movement_type=Type.RESERVE, quantity=q, reserved_delta=q, actor=actor, work_order=wo, line=line)
    if res is None:
        res = PartReservation(organization=wo.organization, part_line=line, work_order=wo, warehouse=warehouse,
                              part=line.part, quantity=q)
    else:
        res.quantity, res.status, res.warehouse = res.quantity + q, "ACTIVE", warehouse
    res.save()
    line.warehouse = warehouse
    _save_line(line, res)
    _audit_movement("stock.reserved", mv, actor=actor, request=request, work_order=wo.number)
    return line


def _release_held(line, wo, res, quantity, *, actor, request, reason="", final_status="RELEASED"):
    bal = _lock_balance(wo.organization, res.warehouse, line.part, create=False)
    mv = _apply(bal, movement_type=Type.RELEASE, quantity=quantity, reserved_delta=-quantity, actor=actor,
                work_order=wo, line=line, reason=reason)
    res.quantity -= quantity
    if res.quantity == 0:
        res.status = final_status
    res.save()
    _audit_movement("stock.released", mv, actor=actor, request=request, work_order=wo.number)


@transaction.atomic
def release(line: WorkOrderPart, quantity=None, *, actor, request=None) -> WorkOrderPart:
    wo = _lock_wo(line.work_order)
    line = _lock_line(line)
    res = _reservation(line)
    if res is None or res.quantity <= 0:
        raise Conflict("Nothing is reserved on this line.", code="nothing_reserved")
    q = quantity_of(quantity) if quantity not in (None, "") else res.quantity
    if q > res.quantity:
        raise Conflict("Cannot release more than is reserved.", code="exceeds_reserved",
                       details={"reserved": str(res.quantity)})
    _release_held(line, wo, res, q, actor=actor, request=request)
    _save_line(line, res)
    return line


@transaction.atomic
def issue(line: WorkOrderPart, warehouse: Warehouse, quantity, *, actor, request=None) -> WorkOrderPart:
    """Physically issues stock to the work order: on-hand falls (the reserved hold is used first), one ISSUE
    movement is written and the line's issued quantity rises."""
    wo = _lock_wo(line.work_order)
    line = _lock_line(line)
    _gate(wo, STOCK_OUT_STATES, "Issuing stock")
    if line.status in TERMINAL:
        raise Conflict("This part line is closed.", code="part_line_locked")
    warehouse = _line_warehouse(line, wo, warehouse, for_stock_out=True)
    _active_part(line.part)
    q = quantity_of(quantity)
    if q > line.remaining_to_issue:
        raise Conflict("Cannot issue more than the requirement.", code="exceeds_requirement",
                       details={"remaining": str(line.remaining_to_issue)})
    res = _reservation(line)
    held = res.quantity if res else ZERO
    from_hold = min(held, q)
    bal = _lock_balance(wo.organization, warehouse, line.part, create=False)
    if bal is None or bal.available < q - from_hold:
        raise Conflict("Insufficient available stock.", code="insufficient_available",
                       details={"available": str(bal.available if bal else ZERO)})
    mv = _apply(bal, movement_type=Type.ISSUE, quantity=q, on_hand_delta=-q, reserved_delta=-from_hold, actor=actor,
                work_order=wo, line=line)
    if res is not None and from_hold:
        res.quantity -= from_hold
        if res.quantity == 0:
            res.status = "FULFILLED"
        res.save()
    line.quantity_issued += q
    line.warehouse = warehouse
    _save_line(line, res)
    _audit_movement("stock.issued", mv, actor=actor, request=request, work_order=wo.number)
    return line


@transaction.atomic
def return_stock(line: WorkOrderPart, quantity, *, actor, reason: str = "", request=None) -> WorkOrderPart:
    """Returns issued-but-unused stock to the warehouse it came from (one RETURN movement)."""
    wo = _lock_wo(line.work_order)
    line = _lock_line(line)
    _gate(wo, RETURN_STATES, "Returning stock")
    if line.status in TERMINAL:
        raise Conflict("This part line is closed.", code="part_line_locked")
    q = quantity_of(quantity)
    if q > line.outstanding:
        raise Conflict("Cannot return more than is issued and not yet consumed.", code="exceeds_outstanding",
                       details={"outstanding": str(line.outstanding)})
    _active_warehouse(line.warehouse)
    bal = _lock_balance(wo.organization, line.warehouse, line.part, create=True)
    mv = _apply(bal, movement_type=Type.RETURN, quantity=q, on_hand_delta=q, actor=actor, work_order=wo, line=line,
                reason=_reason(reason))
    line.quantity_returned += q
    _save_line(line, _reservation(line))
    _audit_movement("stock.returned", mv, actor=actor, request=request, work_order=wo.number)
    return line


@transaction.atomic
def consume(line: WorkOrderPart, quantity, *, actor, membership, request=None) -> WorkOrderPart:
    """Records that issued parts were used on the job. Stock already left the warehouse at issue time, so no
    balance changes here; the usage is written to the M06 material list (through the M06 service) and audited."""
    from apps.workorders import services as wos

    wo = _lock_wo(line.work_order)
    line = _lock_line(line)
    _gate(wo, CONSUME_STATES, "Recording consumption")
    wos.assert_recordable(wo, membership)  # M06 rule: assignee / dispatcher / reviewer, work in progress or review
    if line.status in TERMINAL:
        raise Conflict("This part line is closed.", code="part_line_locked")
    q = quantity_of(quantity)
    if q > line.outstanding:
        raise Conflict("Cannot consume more than is issued and not yet consumed or returned.",
                       code="exceeds_outstanding", details={"outstanding": str(line.outstanding)})
    wos.record_material(wo, description=line.part.name, quantity=q, unit=line.part.unit,
                        part_number=line.part.part_number, actor=actor, membership=membership, request=request,
                        part_line=line)
    line.quantity_consumed += q
    _save_line(line, _reservation(line))
    audit.record("part_line.consumed", actor=actor, organization=wo.organization, target=line,
                 after={"work_order": wo.number, "part": line.part.part_number, "consumed": q,
                        "total_consumed": line.quantity_consumed}, request=request)
    return line


@transaction.atomic
def reconcile(line: WorkOrderPart, *, actor, request=None) -> WorkOrderPart:
    wo = _lock_wo(line.work_order)
    line = _lock_line(line)
    res = _reservation(line)
    if line.outstanding > 0:
        raise Conflict("Consume or return the issued parts first.", code="outstanding_parts")
    if res is not None and res.quantity > 0:
        raise Conflict("Release the remaining reservation first.", code="reservation_open")
    PART_LINE.apply(line, "reconcile")
    line.save()
    audit.record("part_line.reconciled", actor=actor, organization=wo.organization, target=line,
                 after={"work_order": wo.number, "part": line.part.part_number}, request=request)
    return line


@transaction.atomic
def cancel_line(line: WorkOrderPart, *, actor, membership, request=None) -> WorkOrderPart:
    """Cancels a requirement that has nothing out on the job (a held reservation is released first). Stores staff
    (``inventory.reconcile``) may cancel any open line; the requester only a line nothing has happened to yet."""
    wo = _lock_wo(line.work_order)
    line = _lock_line(line)
    if not rbac.has_permission(membership, "inventory.reconcile", wo.site_id) and not (
            line.status == REQUESTED and rbac.has_permission(membership, "inventory.request", wo.site_id)
            and can_request(wo, membership)):
        raise PermissionDenied("You cannot cancel this part line.", code="not_allowed")
    res = _reservation(line)
    if res is not None and res.quantity > 0 and line.outstanding == 0 and line.quantity_consumed == 0:
        _release_held(line, wo, res, res.quantity, actor=actor, request=request, reason="line cancelled")
    _refresh_status(line, res)
    PART_LINE.apply(line, "cancel")  # ISSUED / CONSUMED / terminal -> InvalidTransition (409)
    line.save()
    audit.record("part_line.cancelled", actor=actor, organization=wo.organization, target=line,
                 after={"work_order": wo.number, "part": line.part.part_number}, request=request)
    return line


# --- M06 hooks ----------------------------------------------------------------------------------------------------


def part_blockers(wo) -> list[str]:
    """M06 closure contract: parts physically issued to the order must be consumed or returned first."""
    lines = WorkOrderPart.objects.for_organization(wo.organization).filter(work_order=wo).exclude(
        status__in=TERMINAL).select_related("part")
    return [f"{ln.outstanding:g} x {ln.part.part_number} issued but not consumed or returned."
            for ln in lines if ln.outstanding > 0]


def _wind_down(wo, *, actor, request, reconcile_done: bool):
    lines = list(WorkOrderPart.objects.for_organization(wo.organization).select_for_update(of=("self",)).filter(
        work_order=wo).exclude(status__in=TERMINAL).select_related("part", "warehouse"))
    for line in lines:
        res = _reservation(line)
        if res is not None and res.quantity > 0:
            _release_held(line, wo, res, res.quantity, actor=actor, request=request, reason="work order ended")
        _save_line(line, res)
        if line.quantity_issued == 0 or (line.outstanding == 0 and line.quantity_consumed == 0):
            line.status = "CANCELLED"
        elif reconcile_done and line.outstanding == 0:
            line.status = RECONCILED
        line.save()
        audit.record("part_line.cancelled" if line.status == "CANCELLED" else "part_line.reconciled", actor=actor,
                     organization=wo.organization, target=line,
                     after={"work_order": wo.number, "part": line.part.part_number}, metadata={"automatic": True},
                     request=request)


@transaction.atomic
def on_work_order_closed(wo, *, actor, request=None):
    """Closure reconciles the part lines: reservations are released, never-issued lines cancelled, finished lines
    reconciled. (Outstanding issued parts already blocked the closure.)"""
    _wind_down(wo, actor=actor, request=request, reconcile_done=True)


@transaction.atomic
def on_work_order_cancelled(wo, *, actor, request=None):
    """A cancelled order gives its reservations back; stock that was already issued must be returned first."""
    blockers = part_blockers(wo)
    if blockers:
        raise Conflict("Return the issued parts before cancelling: " + " ".join(blockers), code="outstanding_parts",
                       details={"blockers": blockers})
    _wind_down(wo, actor=actor, request=request, reconcile_done=False)
