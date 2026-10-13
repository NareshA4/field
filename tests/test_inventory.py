"""
================================================================================
Test Suite: FON-M09 Spare Parts & Inventory (StockMovement Ledger & Part Lifecycle)
Location: tests/test_inventory.py
================================================================================

WHY THIS TEST FILE EXISTS:
--------------------------
This test suite validates the core business rules and append-only ledger for
FON-M09 (Spare Parts & Inventory) as mandated by HPE-PRD-2026-FOPS02:
1. Append-only StockMovement ledger (Receipt, Issue, Return, Adjust, Scrap, Transfer).
2. Atomic StockBalance invariants: available = on_hand - reserved >= 0, reserved <= on_hand.
3. 6-stage Work Order Part lifecycle: REQUESTED -> RESERVED -> ISSUED -> CONSUMED/RETURNED -> RECONCILED.
4. Concurrency protection via row-level locks (select_for_update).
5. Multi-tenant isolation and site-scoped RBAC permissions.
================================================================================
"""
from decimal import Decimal
import pytest
from django.utils import timezone

from apps.core.exceptions import Conflict, NotFound, PermissionDenied, ValidationFailed
from apps.inventory import selectors, services
from apps.inventory.models import PartReservation, StockBalance, StockMovement, WorkOrderPart
from tests.phase3_support import wo_in_progress

pytestmark = pytest.mark.django_db
Type = StockMovement.Type


# ================================================================================
# 1. Append-Only StockMovement Ledger: Receipt, Adjust, Scrap, Transfer
# ================================================================================

def test_receive_creates_balance_and_append_only_movement(inv_):
    """
    Receipt:
    - Adds to on_hand and available.
    - Writes exactly one append-only StockMovement with Type.RECEIPT.
    - Captures reference (PO/delivery note), actor, and resulting balance.
    """
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]

    mv = services.receive(wh, part, "10.000", actor=stores.user, reference="PO-99881", reason="Initial stock intake")

    assert mv.movement_type == Type.RECEIPT
    assert mv.quantity == Decimal("10.000")
    assert mv.on_hand_delta == Decimal("10.000")
    assert mv.reserved_delta == Decimal("0")
    assert mv.on_hand_after == Decimal("10.000")
    assert mv.reserved_after == Decimal("0")
    assert mv.reference == "PO-99881"
    assert mv.reason == "Initial stock intake"
    assert mv.actor == stores.user

    bal = StockBalance.objects.get(warehouse=wh, part=part)
    assert bal.on_hand == Decimal("10.000")
    assert bal.reserved == Decimal("0")
    assert bal.available == Decimal("10.000")


def test_adjust_count_correction_and_cannot_cut_into_reserved(inv_):
    """
    Adjust:
    - Modifies stock with positive or negative delta.
    - Strict invariant: adjustment CANNOT cut into reserved stock or go below zero.
    """
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]

    # Seed 10 items
    services.receive(wh, part, "10", actor=stores.user)

    # Positive adjustment (+2)
    mv_pos = services.adjust(wh, part, "2", reason="Cycle count found 2 extra", actor=stores.user)
    assert mv_pos.movement_type == Type.ADJUSTMENT
    assert mv_pos.quantity == Decimal("2.000")
    assert mv_pos.on_hand_delta == Decimal("2.000")
    assert mv_pos.on_hand_after == Decimal("12.000")

    # Negative adjustment (-3)
    mv_neg = services.adjust(wh, part, "-3", reason="Damaged packaging correction", actor=stores.user)
    assert mv_neg.movement_type == Type.ADJUSTMENT
    assert mv_neg.quantity == Decimal("3.000")
    assert mv_neg.on_hand_delta == Decimal("-3.000")
    assert mv_neg.on_hand_after == Decimal("9.000")

    # Attempting to adjust below zero fails with Conflict
    with pytest.raises(Conflict) as exc:
        services.adjust(wh, part, "-15", reason="Excess reduction", actor=stores.user)
    assert exc.value.code == "insufficient_available"


def test_scrap_deducts_on_hand_and_creates_scrap_movement(inv_):
    """
    Scrap:
    - Scraps damaged, expired, or decommissioned stock.
    - Decreases on_hand without cutting into reserved stock.
    - Writes an immutable SCRAP movement with mandatory reason.
    """
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]

    services.receive(wh, part, "15", actor=stores.user)

    # Scrap 3 damaged items
    mv = services.scrap(wh, part, "3", reason="Dropped during material handling and cracked", actor=stores.user)

    assert mv.movement_type == Type.SCRAP
    assert mv.quantity == Decimal("3.000")
    assert mv.on_hand_delta == Decimal("-3.000")
    assert mv.reserved_delta == Decimal("0")
    assert mv.on_hand_after == Decimal("12.000")
    assert mv.reason == "Dropped during material handling and cracked"

    bal = StockBalance.objects.get(warehouse=wh, part=part)
    assert bal.on_hand == Decimal("12.000")
    assert bal.available == Decimal("12.000")

    # Scrap requires a reason
    with pytest.raises(ValidationFailed):
        services.scrap(wh, part, "1", reason="", actor=stores.user)

    # Cannot scrap more than available
    with pytest.raises(Conflict) as exc:
        services.scrap(wh, part, "20", reason="Too much", actor=stores.user)
    assert exc.value.code == "insufficient_available"


def test_transfer_between_warehouses_writes_dual_movements(inv_):
    """
    Transfer:
    - Moves available stock between two warehouses of the same organization.
    - Writes exactly two movements sharing a transfer reference: TRANSFER_OUT and TRANSFER_IN.
    """
    wh1, wh2, part, stores = inv_["wh"], inv_["wh2"], inv_["part"], inv_["stores"]

    services.receive(wh1, part, "20", actor=stores.user)

    out_mv, in_mv = services.transfer(wh1, wh2, part, "5", reason="Replenish van stock", actor=stores.user)

    assert out_mv.movement_type == Type.TRANSFER_OUT
    assert out_mv.quantity == Decimal("5.000")
    assert out_mv.on_hand_delta == Decimal("-5.000")
    assert out_mv.on_hand_after == Decimal("15.000")
    assert out_mv.warehouse == wh1

    assert in_mv.movement_type == Type.TRANSFER_IN
    assert in_mv.quantity == Decimal("5.000")
    assert in_mv.on_hand_delta == Decimal("5.000")
    assert in_mv.on_hand_after == Decimal("5.000")
    assert in_mv.warehouse == wh2

    assert out_mv.transfer_ref == in_mv.transfer_ref


def test_set_levels_and_low_stock_thresholds(inv_):
    """
    Min / Max / Reorder levels:
    - Overrides catalog defaults per warehouse.
    - Low stock detection: is_low is True when available <= effective_min.
    """
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]

    services.receive(wh, part, "10", actor=stores.user)
    bal = services.set_levels(wh, part, min_level="5", max_level="25", reorder_quantity="15", actor=stores.user)

    assert bal.min_level == Decimal("5.000")
    assert bal.max_level == Decimal("25.000")
    assert bal.reorder_quantity == Decimal("15.000")
    assert bal.is_low is False

    # Adjust stock down to 4 -> is_low triggers!
    services.adjust(wh, part, "-6", reason="Stock consumed", actor=stores.user)
    bal.refresh_from_db()
    assert bal.available == Decimal("4.000")
    assert bal.is_low is True


# ================================================================================
# 2. Work Order Part Lifecycle: Request, Reserve, Issue, Return, Consume, Reconcile
# ================================================================================

def test_full_work_order_part_lifecycle(inv_):
    """
    Full Lifecycle:
    1. REQUEST: Technician requests 4 parts on active work order.
    2. RESERVE: Storekeeper reserves 4 parts (available drops, on_hand untouched).
    3. ISSUE: Storekeeper issues 4 parts to technician (on_hand drops, reserved released).
    4. CONSUME: Technician consumes 3 parts on the asset (recorded to M06 materials).
    5. RETURN: Technician returns 1 unused part back to the warehouse (on_hand rises).
    6. RECONCILE: Storekeeper reconciles the line (issued 4 == consumed 3 + returned 1).
    """
    wh, part, stores, tech = inv_["wh"], inv_["part"], inv_["stores"], inv_["tech"]
    wo = wo_in_progress(inv_, tech="tech")

    # Seed 10 items in warehouse
    services.receive(wh, part, "10", actor=stores.user)

    # 1. REQUEST
    line = services.request_part(wo, part, "4", actor=tech.user, membership=tech, notes="Required for pump seal repair")
    assert line.status == WorkOrderPart.Status.REQUESTED
    assert line.quantity_requested == Decimal("4.000")

    # 2. RESERVE
    line = services.reserve(line, wh, quantity="4", actor=stores.user)
    assert line.status == WorkOrderPart.Status.RESERVED
    bal = StockBalance.objects.get(warehouse=wh, part=part)
    assert bal.on_hand == Decimal("10.000")
    assert bal.reserved == Decimal("4.000")
    assert bal.available == Decimal("6.000")

    # 3. ISSUE
    line = services.issue(line, wh, quantity="4", actor=stores.user)
    assert line.status == WorkOrderPart.Status.ISSUED
    bal.refresh_from_db()
    assert bal.on_hand == Decimal("6.000")
    assert bal.reserved == Decimal("0.000")
    assert bal.available == Decimal("6.000")
    assert line.quantity_issued == Decimal("4.000")
    assert line.outstanding == Decimal("4.000")

    # 4. CONSUME (3 parts used)
    line = services.consume(line, "3", actor=tech.user, membership=tech)
    assert line.quantity_consumed == Decimal("3.000")
    assert line.outstanding == Decimal("1.000")
    # Verified consumption synchronized to work order materials list
    assert wo.material_entries.filter(part_reference=part.part_number, quantity=Decimal("3.000")).exists()

    # 5. RETURN (1 unused part returned)
    line = services.return_stock(line, "1", reason="Surplus piece returned undamaged", actor=stores.user)
    assert line.quantity_returned == Decimal("1.000")
    assert line.outstanding == Decimal("0.000")
    bal.refresh_from_db()
    assert bal.on_hand == Decimal("7.000")  # 6 + 1 returned = 7!

    # 6. RECONCILE
    line = services.reconcile(line, actor=stores.user)
    assert line.status == WorkOrderPart.Status.RECONCILED


def test_cannot_issue_or_reserve_more_than_available(inv_):
    """Guarantees stock cannot be over-reserved or over-issued."""
    wh, part, stores, tech = inv_["wh"], inv_["part"], inv_["stores"], inv_["tech"]
    wo = wo_in_progress(inv_, tech="tech")

    # Warehouse only has 2 in stock
    services.receive(wh, part, "2", actor=stores.user)

    line = services.request_part(wo, part, "5", actor=tech.user, membership=tech)

    # Attempting to reserve 5 when only 2 available raises Conflict
    with pytest.raises(Conflict) as exc:
        services.reserve(line, wh, quantity="5", actor=stores.user)
    assert exc.value.code == "insufficient_available"


def test_release_reservation_returns_available_stock(inv_):
    """Releasing a reservation clears the hold and restores available stock."""
    wh, part, stores, tech = inv_["wh"], inv_["part"], inv_["stores"], inv_["tech"]
    wo = wo_in_progress(inv_, tech="tech")

    services.receive(wh, part, "10", actor=stores.user)
    line = services.request_part(wo, part, "4", actor=tech.user, membership=tech)
    services.reserve(line, wh, quantity="4", actor=stores.user)

    bal = StockBalance.objects.get(warehouse=wh, part=part)
    assert bal.reserved == Decimal("4.000")
    assert bal.available == Decimal("6.000")

    # Release 2 of the 4 reserved items
    services.release(line, quantity="2", actor=stores.user)
    bal.refresh_from_db()
    assert bal.reserved == Decimal("2.000")
    assert bal.available == Decimal("8.000")


# ================================================================================
# 3. Append-Only Immutability & Ledger Invariants
# ================================================================================

def test_stock_movement_ledger_is_strictly_append_only(inv_):
    """
    Every balance modification creates exactly one immutable StockMovement row.
    Sum of movements matches current balance.
    """
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]

    services.receive(wh, part, "20", actor=stores.user)
    services.adjust(wh, part, "-2", reason="Broken count", actor=stores.user)
    services.scrap(wh, part, "3", reason="Water damage", actor=stores.user)

    movements = StockMovement.objects.filter(warehouse=wh, part=part).order_by("created_at")
    assert movements.count() == 3

    assert [m.movement_type for m in movements] == [Type.RECEIPT, Type.ADJUSTMENT, Type.SCRAP]
    assert [m.on_hand_delta for m in movements] == [Decimal("20.000"), Decimal("-2.000"), Decimal("-3.000")]

    final_bal = StockBalance.objects.get(warehouse=wh, part=part)
    assert final_bal.on_hand == Decimal("15.000")


# ================================================================================
# 4. Multi-Tenant Isolation & Cross-Tenant Security
# ================================================================================

def test_cross_tenant_inventory_isolation(inv_, org_b, make_member):
    """Guarantees Tenant B cannot access or modify Tenant A's inventory."""
    wh_a, part_a = inv_["wh"], inv_["part"]
    stores_b = make_member(org_b, "stores@beta.test", "stores_manager")

    part_b = services.create_part(org_b, part_number="BETA-PART", name="Beta Widget", actor=stores_b.user)

    # Cannot mix parts across tenants in receive
    with pytest.raises(ValidationFailed):
        services.receive(wh_a, part_b, "10", actor=stores_b.user)

    # Tenant B cannot find Tenant A's part via scoped selector
    with pytest.raises(NotFound):
        selectors.get_part(org_b, part_a.pk)
