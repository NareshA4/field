"""M09 Inventory service rules: the stock ledger invariant, reservations, issue / return / consume, transfers,
adjustments, tenancy of references, M06 integration (closure blocker, close / cancel hooks) and audit."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core.exceptions import Conflict, InvalidTransition, PermissionDenied, ValidationFailed
from apps.inventory import services as inv
from apps.inventory.models import PartReservation, StockBalance, StockMovement, WorkOrderPart
from apps.workorders import services as wos
from apps.workorders.models import WorkOrderMaterial
from tests.phase3_support import finish_work, new_wo, step, wo_in_progress

pytestmark = pytest.mark.django_db
D = Decimal


def stock(wh, part, qty, who):
    return inv.receive(wh, part, qty, actor=who.user)


def bal(wh, part):
    return StockBalance.objects.get(warehouse=wh, part=part)


def line_for(p, wo, part=None, qty="5", who="tech"):
    return inv.request_part(wo, part or p["part"], qty, actor=p[who].user, membership=p[who])


def assert_ledger_consistent(org):
    """The invariant: balances equal the sum of their movements, reserved equals the active reservations."""
    for b in StockBalance.objects.for_organization(org):
        agg = StockMovement.objects.filter(balance=b).aggregate(on=Sum("on_hand_delta"), res=Sum("reserved_delta"))
        assert (agg["on"] or 0) == b.on_hand, f"on_hand drift on {b}"
        assert (agg["res"] or 0) == b.reserved, f"reserved drift on {b}"
        held = PartReservation.objects.filter(warehouse=b.warehouse, part=b.part, status="ACTIVE").aggregate(
            q=Sum("quantity"))["q"] or 0
        assert held == b.reserved, f"reservation drift on {b}"
        assert 0 <= b.reserved <= b.on_hand


# --- the required adversarial scenario ------------------------------------------------------------------------------


def test_stock_10_issue_2_return_1_then_issue_10_is_rejected(inv_):
    p = inv_
    wo = wo_in_progress(p)
    stock(p["wh"], p["part"], 10, p["stores"])
    line = line_for(p, wo, qty="12")
    inv.issue(line, p["wh"], 2, actor=p["stores"].user)
    assert bal(p["wh"], p["part"]).on_hand == 8
    inv.return_stock(line, 1, actor=p["stores"].user)
    assert bal(p["wh"], p["part"]).on_hand == 9
    movements_before = StockMovement.objects.count()
    with pytest.raises(Conflict) as exc:
        inv.issue(line, p["wh"], 10, actor=p["stores"].user)
    assert exc.value.code == "insufficient_available"
    assert bal(p["wh"], p["part"]).on_hand == 9  # stock stays 9
    assert StockMovement.objects.count() == movements_before  # and no phantom movement
    types = list(StockMovement.objects.filter(part=p["part"]).order_by("created_at").values_list(
        "movement_type", "on_hand_delta"))
    assert types == [("RECEIPT", 10), ("ISSUE", -2), ("RETURN", 1)]
    assert_ledger_consistent(p["org"])


# --- receive / quantity validation -------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, "0", -1, "-0.5", "abc", "1.2345", float("nan"), "Infinity", 10_000_000, None])
def test_bad_quantities_are_rejected_everywhere(inv_, bad):
    p = inv_
    with pytest.raises(ValidationFailed):
        inv.receive(p["wh"], p["part"], bad, actor=p["stores"].user)
    assert not StockMovement.objects.exists() and not StockBalance.objects.exists()


def test_receive_creates_balance_movement_and_audit(inv_):
    p = inv_
    mv = inv.receive(p["wh"], p["part"], "7.5", actor=p["stores"].user, reference="PO-1")
    b = bal(p["wh"], p["part"])
    assert (b.on_hand, b.reserved, b.available) == (D("7.5"), 0, D("7.5"))
    assert (mv.movement_type, mv.on_hand_delta, mv.on_hand_after, mv.reference) == ("RECEIPT", D("7.5"), D("7.5"), "PO-1")
    assert AuditLog.objects.filter(organization=p["org"], action="stock.received",
                                   metadata__movement=str(mv.pk)).count() == 1


def test_inactive_part_and_warehouse_are_rejected(inv_):
    p = inv_
    stock(p["wh"], p["part"], 5, p["stores"])
    inv.set_part_active(p["part"], False, actor=p["stores"].user)
    for call in (lambda: inv.receive(p["wh"], p["part"], 1, actor=p["stores"].user),
                 lambda: inv.adjust(p["wh"], p["part"], 1, reason="count", actor=p["stores"].user),
                 lambda: inv.transfer(p["wh"], p["wh2"], p["part"], 1, actor=p["stores"].user)):
        with pytest.raises(Conflict) as exc:
            call()
        assert exc.value.code == "part_inactive"
    wo = wo_in_progress(p)
    with pytest.raises(Conflict):
        line_for(p, wo)
    inv.set_part_active(p["part"], True, actor=p["stores"].user)
    inv.set_warehouse_active(p["wh2"], False, actor=p["stores"].user)
    with pytest.raises(Conflict) as exc:
        inv.receive(p["wh2"], p["part"], 1, actor=p["stores"].user)
    assert exc.value.code == "warehouse_inactive"


def test_warehouse_with_stock_cannot_be_deactivated(inv_):
    p = inv_
    stock(p["wh"], p["part"], 1, p["stores"])
    with pytest.raises(Conflict) as exc:
        inv.set_warehouse_active(p["wh"], False, actor=p["stores"].user)
    assert exc.value.code == "warehouse_has_stock"


def test_cross_tenant_references_are_rejected(inv_, org_b, make_site, make_member):
    p = inv_
    b_site = make_site(org_b, "B1")
    b_stores = make_member(org_b, "stores@beta.test", "stores_manager")
    b_wh = inv.create_warehouse(org_b, site=b_site, code="BMAIN", name="Beta store", actor=b_stores.user)
    b_part = inv.create_part(org_b, part_number="SEAL-100", name="Beta seal", actor=b_stores.user)  # same PN, other org
    with pytest.raises(ValidationFailed):
        inv.receive(p["wh"], b_part, 1, actor=p["stores"].user)
    with pytest.raises(ValidationFailed):
        inv.receive(b_wh, p["part"], 1, actor=p["stores"].user)
    with pytest.raises(ValidationFailed):
        inv.transfer(p["wh"], b_wh, p["part"], 1, actor=p["stores"].user)
    with pytest.raises(ValidationFailed):
        inv.create_warehouse(p["org"], site=b_site, code="X", name="X", actor=p["stores"].user)
    wo = wo_in_progress(p)
    with pytest.raises(ValidationFailed):
        inv.request_part(wo, b_part, 1, actor=p["tech"].user, membership=p["tech"])
    line = line_for(p, wo)
    stock(p["wh"], p["part"], 5, p["stores"])
    with pytest.raises(ValidationFailed):
        inv.issue(line, b_wh, 1, actor=p["stores"].user)
    assert not StockMovement.objects.filter(warehouse=b_wh).exists()


def test_part_number_unique_per_organization_case_insensitive(inv_):
    p = inv_
    with pytest.raises(Conflict):
        inv.create_part(p["org"], part_number="seal-100", name="dup", actor=p["stores"].user)
    with pytest.raises(Conflict):
        inv.create_warehouse(p["org"], site=p["site"], code="main", name="dup", actor=p["stores"].user)


# --- reservations ------------------------------------------------------------------------------------------------


def test_reservation_on_hand_reserved_available_and_cannot_overreserve(inv_):
    p = inv_
    stock(p["wh"], p["part"], 10, p["stores"])
    wo1, wo2 = wo_in_progress(p), wo_in_progress(p, tech="tech2")
    l1 = line_for(p, wo1, qty="4")
    l2 = line_for(p, wo2, qty="7", who="tech2")
    inv.reserve(l1, p["wh"], actor=p["stores"].user)
    b = bal(p["wh"], p["part"])
    assert (b.on_hand, b.reserved, b.available) == (10, 4, 6)
    with pytest.raises(Conflict) as exc:
        inv.reserve(l2, p["wh"], 7, actor=p["stores"].user)  # cannot reserve 7 of 6 available
    assert exc.value.code == "insufficient_available"
    inv.reserve(l2, p["wh"], 6, actor=p["stores"].user)
    assert bal(p["wh"], p["part"]).available == 0
    l1.refresh_from_db()
    assert l1.status == "RESERVED"
    assert_ledger_consistent(p["org"])


def test_release_returns_availability_and_issue_uses_the_reservation(inv_):
    p = inv_
    stock(p["wh"], p["part"], 10, p["stores"])
    wo = wo_in_progress(p)
    line = line_for(p, wo, qty="5")
    inv.reserve(line, p["wh"], actor=p["stores"].user)
    inv.release(line, 2, actor=p["stores"].user)
    b = bal(p["wh"], p["part"])
    assert (b.reserved, b.available) == (3, 7)
    inv.issue(line, p["wh"], 3, actor=p["stores"].user)  # consumes the 3 held
    b = bal(p["wh"], p["part"])
    assert (b.on_hand, b.reserved, b.available) == (7, 0, 7)
    res = PartReservation.objects.get(part_line=line)
    assert (res.quantity, res.status) == (0, "FULFILLED")
    mv = StockMovement.objects.filter(movement_type="ISSUE").get()
    assert (mv.on_hand_delta, mv.reserved_delta, mv.work_order_id, mv.part_line_id) == (-3, -3, wo.pk, line.pk)
    with pytest.raises(Conflict) as exc:
        inv.release(line, 1, actor=p["stores"].user)
    assert exc.value.code == "nothing_reserved"
    assert_ledger_consistent(p["org"])


def test_reserve_limits_and_release_limits(inv_):
    p = inv_
    stock(p["wh"], p["part"], 10, p["stores"])
    line = line_for(p, wo_in_progress(p), qty="3")
    with pytest.raises(Conflict) as exc:
        inv.reserve(line, p["wh"], 4, actor=p["stores"].user)
    assert exc.value.code == "exceeds_requirement"
    inv.reserve(line, p["wh"], actor=p["stores"].user)
    with pytest.raises(Conflict) as exc:
        inv.reserve(line, p["wh"], 1, actor=p["stores"].user)
    assert exc.value.code == "already_covered"
    with pytest.raises(Conflict) as exc:
        inv.release(line, 4, actor=p["stores"].user)
    assert exc.value.code == "exceeds_reserved"
    for bad in (0, -1):
        with pytest.raises(ValidationFailed):
            inv.release(line, bad, actor=p["stores"].user)


def test_reserved_stock_cannot_be_issued_to_someone_else_or_adjusted_or_transferred(inv_):
    p = inv_
    stock(p["wh"], p["part"], 10, p["stores"])
    l1 = line_for(p, wo_in_progress(p), qty="8")
    l2 = line_for(p, wo_in_progress(p, tech="tech2"), qty="5", who="tech2")
    inv.reserve(l1, p["wh"], actor=p["stores"].user)
    with pytest.raises(Conflict):
        inv.issue(l2, p["wh"], 3, actor=p["stores"].user)  # only 2 available
    with pytest.raises(Conflict):
        inv.adjust(p["wh"], p["part"], -5, reason="shrinkage", actor=p["stores"].user)
    with pytest.raises(Conflict):
        inv.transfer(p["wh"], p["wh2"], p["part"], 3, actor=p["stores"].user)
    inv.issue(l2, p["wh"], 2, actor=p["stores"].user)
    assert_ledger_consistent(p["org"])


# --- issue / return / consume ------------------------------------------------------------------------------------------


def test_issue_without_stock_and_beyond_requirement(inv_):
    p = inv_
    line = line_for(p, wo_in_progress(p), qty="3")
    with pytest.raises(Conflict) as exc:
        inv.issue(line, p["wh"], 1, actor=p["stores"].user)  # no balance row at all
    assert exc.value.code == "insufficient_available"
    stock(p["wh"], p["part"], 10, p["stores"])
    with pytest.raises(Conflict) as exc:
        inv.issue(line, p["wh"], 4, actor=p["stores"].user)
    assert exc.value.code == "exceeds_requirement"
    for bad in (0, -2, "x"):
        with pytest.raises(ValidationFailed):
            inv.issue(line, p["wh"], bad, actor=p["stores"].user)
    assert bal(p["wh"], p["part"]).on_hand == 10


def test_wrong_site_and_wrong_warehouse_are_rejected(inv_):
    p = inv_
    stock(p["wh"], p["part"], 5, p["stores"])
    stock(p["whs2"], p["part"], 5, p["stores"])
    stock(p["wh2"], p["part"], 5, p["stores"])
    line = line_for(p, wo_in_progress(p), qty="4")
    with pytest.raises(ValidationFailed) as exc:
        inv.issue(line, p["whs2"], 1, actor=p["stores"].user)  # warehouse of another site
    assert exc.value.code == "wrong_site"
    inv.issue(line, p["wh"], 1, actor=p["stores"].user)
    with pytest.raises(Conflict) as exc:
        inv.issue(line, p["wh2"], 1, actor=p["stores"].user)  # line is committed to MAIN
    assert exc.value.code == "wrong_warehouse"
    assert bal(p["wh2"], p["part"]).on_hand == 5 and bal(p["whs2"], p["part"]).on_hand == 5


def test_part_request_rules(inv_):
    p = inv_
    draft = new_wo(p)
    inv.request_part(draft, p["part"], 1, actor=p["planner"].user, membership=p["planner"])  # DRAFT allowed
    with pytest.raises(PermissionDenied):  # a technician who is not the assignee
        inv.request_part(draft, p["part"], 1, actor=p["tech2"].user, membership=p["tech2"])
    wo = wo_in_progress(p)
    other = p["tech2"]
    with pytest.raises(PermissionDenied):
        inv.request_part(wo, p["part"], 1, actor=other.user, membership=other)
    line = line_for(p, wo, qty="2")
    line2 = inv.request_part(wo, p["part"], 3, actor=p["tech"].user, membership=p["tech"])  # tops up the same line
    assert line2.pk == line.pk and line2.quantity_requested == 5
    assert WorkOrderPart.objects.filter(work_order=wo).count() == 1
    for bad in (0, -1):
        with pytest.raises(ValidationFailed):
            inv.request_part(wo, p["part"], bad, actor=p["tech"].user, membership=p["tech"])


def test_consume_writes_the_m06_material_row_and_respects_ownership(inv_):
    p = inv_
    stock(p["wh"], p["part"], 10, p["stores"])
    wo = wo_in_progress(p)
    line = line_for(p, wo, qty="5")
    with pytest.raises(Conflict):
        inv.consume(line, 1, actor=p["tech"].user, membership=p["tech"])  # nothing issued yet
    inv.issue(line, p["wh"], 5, actor=p["stores"].user)
    with pytest.raises(PermissionDenied):
        inv.consume(line, 1, actor=p["tech2"].user, membership=p["tech2"])  # not the assignee
    before = bal(p["wh"], p["part"])
    line = inv.consume(line, 3, actor=p["tech"].user, membership=p["tech"])
    assert bal(p["wh"], p["part"]).on_hand == before.on_hand  # consumption moves no stock (it left at issue)
    mat = WorkOrderMaterial.objects.get(part_line=line)
    assert (mat.quantity, mat.part_number, mat.work_order_id) == (3, "SEAL-100", wo.pk)
    assert line.status == "ISSUED" and line.outstanding == 2
    with pytest.raises(Conflict) as exc:
        inv.consume(line, 3, actor=p["tech"].user, membership=p["tech"])
    assert exc.value.code == "exceeds_outstanding"
    with pytest.raises(Conflict):
        inv.return_stock(line, 3, actor=p["stores"].user)
    line = inv.return_stock(line, 2, actor=p["stores"].user)
    assert (line.status, line.outstanding, bal(p["wh"], p["part"]).on_hand) == ("CONSUMED", 0, 7)
    line = inv.reconcile(line, actor=p["stores"].user)
    assert line.status == "RECONCILED"
    with pytest.raises(Conflict):
        inv.issue(line, p["wh"], 1, actor=p["stores"].user)  # reconciled = closed
    assert_ledger_consistent(p["org"])


def test_free_text_material_still_works_and_moves_no_stock(inv_):
    p = inv_
    wo = wo_in_progress(p)
    row = wos.record_material(wo, description="Rags", quantity=2, actor=p["tech"].user, membership=p["tech"])
    assert row.part_line_id is None and not StockMovement.objects.exists()


def test_issue_state_gates(inv_):
    p = inv_
    stock(p["wh"], p["part"], 5, p["stores"])
    draft = new_wo(p)
    line = inv.request_part(draft, p["part"], 1, actor=p["planner"].user, membership=p["planner"])
    with pytest.raises(Conflict) as exc:
        inv.issue(line, p["wh"], 1, actor=p["stores"].user)  # DRAFT: not yet planned
    assert exc.value.code == "work_order_state"
    t0 = timezone.now() + timedelta(days=1)
    step(draft, "plan", p, "planner", planned_start=t0, planned_end=t0 + timedelta(hours=1))
    inv.issue(line, p["wh"], 1, actor=p["stores"].user)  # PLANNED: ok
    with pytest.raises(Conflict):
        inv.consume(line, 1, actor=p["tech"].user, membership=p["tech"])  # not started: nothing to consume yet


# --- transfers / adjustments / levels ----------------------------------------------------------------------------------


def test_transfer_writes_paired_movements(inv_):
    p = inv_
    stock(p["wh"], p["part"], 10, p["stores"])
    out, inn = inv.transfer(p["wh"], p["whs2"], p["part"], 4, actor=p["stores"].user, reason="rebalance")
    assert (bal(p["wh"], p["part"]).on_hand, bal(p["whs2"], p["part"]).on_hand) == (6, 4)
    assert out.transfer_ref == inn.transfer_ref and out.transfer_ref is not None
    assert (out.movement_type, out.on_hand_delta, inn.movement_type, inn.on_hand_delta) == (
        "TRANSFER_OUT", -4, "TRANSFER_IN", 4)
    with pytest.raises(Conflict):
        inv.transfer(p["wh"], p["whs2"], p["part"], 7, actor=p["stores"].user)
    with pytest.raises(ValidationFailed) as exc:
        inv.transfer(p["wh"], p["wh"], p["part"], 1, actor=p["stores"].user)
    assert exc.value.code == "same_warehouse"
    with pytest.raises(Conflict):
        inv.transfer(p["wh2"], p["wh"], p["part"], 1, actor=p["stores"].user)  # source has no stock row
    assert_ledger_consistent(p["org"])


def test_adjustment_rules(inv_):
    p = inv_
    stock(p["wh"], p["part"], 10, p["stores"])
    with pytest.raises(ValidationFailed):
        inv.adjust(p["wh"], p["part"], -3, reason="", actor=p["stores"].user)
    with pytest.raises(ValidationFailed):
        inv.adjust(p["wh"], p["part"], 0, reason="zero", actor=p["stores"].user)
    inv.adjust(p["wh"], p["part"], -3, reason="count correction", actor=p["stores"].user)
    inv.adjust(p["wh"], p["part"], 1, reason="found one", actor=p["stores"].user)
    assert bal(p["wh"], p["part"]).on_hand == 8
    with pytest.raises(Conflict):
        inv.adjust(p["wh"], p["part"], -9, reason="too much", actor=p["stores"].user)
    with pytest.raises(Conflict):
        inv.adjust(p["wh2"], p["part"], -1, reason="no stock here", actor=p["stores"].user)
    assert bal(p["wh"], p["part"]).on_hand == 8
    assert_ledger_consistent(p["org"])


def test_levels_and_low_stock_flag(inv_):
    p = inv_
    stock(p["wh"], p["part"], 2, p["stores"])  # part default min is 2
    b = bal(p["wh"], p["part"])
    assert b.is_low and b.effective_min == 2
    inv.set_levels(p["wh"], p["part"], min_level="1", max_level="5", reorder_quantity="3", actor=p["stores"].user)
    b = bal(p["wh"], p["part"])
    assert (b.effective_min, b.effective_max, b.effective_reorder_quantity, b.is_low) == (1, 5, 3, False)
    with pytest.raises(ValidationFailed):
        inv.set_levels(p["wh"], p["part"], min_level="5", max_level="1", reorder_quantity=None,
                       actor=p["stores"].user)
    assert not StockMovement.objects.filter(movement_type="ADJUSTMENT").exists()  # levels are not quantity changes


# --- database level guards ---------------------------------------------------------------------------------------------


def test_database_refuses_negative_or_over_reserved_balances_and_movement_edits(inv_):
    p = inv_
    stock(p["wh"], p["part"], 5, p["stores"])
    b = bal(p["wh"], p["part"])
    for field_values in ({"on_hand": D("-1")}, {"reserved": D("6")}):
        with pytest.raises(IntegrityError), transaction.atomic():
            StockBalance.objects.filter(pk=b.pk).update(**field_values)
    mv = StockMovement.objects.get()
    with pytest.raises(ValueError):
        mv.quantity = 99
        mv.save()
    with pytest.raises(ValueError):
        mv.delete()
    with pytest.raises(IntegrityError), transaction.atomic():  # a movement that changes nothing
        StockMovement(organization=p["org"], balance=b, warehouse=p["wh"], part=p["part"], movement_type="RECEIPT",
                      quantity=1, on_hand_delta=0, reserved_delta=0, on_hand_after=5, reserved_after=0).save()


# --- M06 integration ---------------------------------------------------------------------------------------------------


def _ready_for_review(p, wo):
    wo = finish_work(wo, p)  # labor + notes, then complete
    return step(wo, "start_review", p, "sup")


def test_closure_is_blocked_by_outstanding_parts_then_reconciles_the_lines(inv_):
    p = inv_
    stock(p["wh"], p["part"], 10, p["stores"])
    wo = wo_in_progress(p)
    line = line_for(p, wo, qty="4")
    inv.issue(line, p["wh"], 4, actor=p["stores"].user)
    inv.consume(line, 3, actor=p["tech"].user, membership=p["tech"])
    wo = _ready_for_review(p, wo)
    blockers = wos.closure_blockers(wo)
    assert any("SEAL-100" in b and "issued but not consumed" in b for b in blockers)
    with pytest.raises(Conflict) as exc:
        step(wo, "close", p, "sup")
    assert exc.value.code == "closure_blocked"
    inv.return_stock(line, 1, actor=p["stores"].user)  # RETURN allowed while in review
    # a second, never-issued requirement and a held reservation do not block: closure cleans them up
    l2 = line_for(p, wo_in_progress(p, tech="tech2"), part=p["part2"], qty="1", who="tech2")
    wo = step(wo, "close", p, "sup")
    line.refresh_from_db()
    assert wo.status == "CLOSED" and line.status == "RECONCILED"
    assert l2.status == "REQUESTED"  # other work order untouched
    assert_ledger_consistent(p["org"])


def test_closing_releases_reservations_and_cancels_never_issued_lines(inv_):
    p = inv_
    stock(p["wh"], p["part"], 10, p["stores"])
    wo = wo_in_progress(p)
    l_issued = line_for(p, wo, qty="2")
    inv.issue(l_issued, p["wh"], 2, actor=p["stores"].user)
    inv.consume(l_issued, 2, actor=p["tech"].user, membership=p["tech"])
    l_held = line_for(p, wo, part=p["part2"], qty="3")
    stock(p["wh"], p["part2"], 3, p["stores"])
    inv.reserve(l_held, p["wh"], actor=p["stores"].user)
    assert bal(p["wh"], p["part2"]).reserved == 3
    wo = _ready_for_review(p, wo)
    step(wo, "close", p, "sup")
    l_held.refresh_from_db()
    assert l_held.status == "CANCELLED"
    b2 = bal(p["wh"], p["part2"])
    assert (b2.on_hand, b2.reserved) == (3, 0)
    assert PartReservation.objects.get(part_line=l_held).status == "RELEASED"
    assert_ledger_consistent(p["org"])


def test_cancelling_a_work_order_releases_reservations_but_not_with_issued_stock(inv_):
    p = inv_
    stock(p["wh"], p["part"], 10, p["stores"])
    wo = new_wo(p)
    t0 = timezone.now() + timedelta(days=1)
    wo = step(wo, "plan", p, "planner", planned_start=t0, planned_end=t0 + timedelta(hours=1))
    line = inv.request_part(wo, p["part"], 4, actor=p["planner"].user, membership=p["planner"])
    inv.reserve(line, p["wh"], actor=p["stores"].user)
    inv.issue(line, p["wh"], 1, actor=p["stores"].user)
    with pytest.raises(Conflict) as exc:
        step(wo, "cancel", p, "planner", reason="not needed anymore")
    assert exc.value.code == "outstanding_parts"
    wo.refresh_from_db()
    assert wo.status == "PLANNED" and bal(p["wh"], p["part"]).on_hand == 9  # rolled back as a whole
    inv.return_stock(line, 1, actor=p["stores"].user)
    step(wo, "cancel", p, "planner", reason="not needed anymore")
    b = bal(p["wh"], p["part"])
    assert (b.on_hand, b.reserved) == (10, 0)
    line.refresh_from_db()
    assert line.status == "CANCELLED"
    assert_ledger_consistent(p["org"])


def test_cancel_line_rules(inv_):
    p = inv_
    stock(p["wh"], p["part"], 5, p["stores"])
    wo = wo_in_progress(p)
    line = line_for(p, wo, qty="2")
    with pytest.raises(PermissionDenied):
        inv.cancel_line(line, actor=p["tech2"].user, membership=p["tech2"])  # not the requester's order
    inv.reserve(line, p["wh"], actor=p["stores"].user)
    with pytest.raises(PermissionDenied):
        inv.cancel_line(line, actor=p["tech"].user, membership=p["tech"])  # reserved: stores staff only
    inv.cancel_line(line, actor=p["stores"].user, membership=p["stores"])
    assert bal(p["wh"], p["part"]).reserved == 0
    # a cancelled line can be requested again
    line = inv.request_part(wo, p["part"], 1, actor=p["tech"].user, membership=p["tech"])
    assert line.status == "REQUESTED" and line.quantity_requested == 1
    inv.issue(line, p["wh"], 1, actor=p["stores"].user)
    with pytest.raises(InvalidTransition):
        inv.cancel_line(line, actor=p["stores"].user, membership=p["stores"])  # issued: return / reconcile instead


def test_every_operation_is_audited_with_the_actor(inv_):
    p = inv_
    stock(p["wh"], p["part"], 10, p["stores"])
    wo = wo_in_progress(p)
    line = line_for(p, wo, qty="4")
    inv.reserve(line, p["wh"], 2, actor=p["stores"].user)
    inv.release(line, 1, actor=p["stores"].user)
    inv.issue(line, p["wh"], 2, actor=p["stores"].user)
    inv.consume(line, 1, actor=p["tech"].user, membership=p["tech"])
    inv.return_stock(line, 1, actor=p["stores"].user)
    inv.reconcile(line, actor=p["stores"].user)
    inv.transfer(p["wh"], p["wh2"], p["part"], 1, actor=p["stores"].user)
    inv.adjust(p["wh"], p["part"], -1, reason="count", actor=p["stores"].user)
    actions = set(AuditLog.objects.filter(organization=p["org"]).values_list("action", flat=True))
    for expected in ("part.created", "warehouse.created", "stock.received", "part_line.requested", "stock.reserved",
                     "stock.released", "stock.issued", "part_line.consumed", "stock.returned",
                     "part_line.reconciled", "stock.transferred", "stock.adjusted", "work_order.material_recorded"):
        assert expected in actions, expected
    issued = AuditLog.objects.get(action="stock.issued")
    assert issued.actor_email == "stores@alpha.test" and issued.metadata["work_order"] == wo.number


def test_movement_count_equals_balance_changes_across_a_mixed_sequence(inv_):
    p = inv_
    stock(p["wh"], p["part"], 20, p["stores"])
    wo = wo_in_progress(p)
    line = line_for(p, wo, qty="10")
    ops = [lambda: inv.reserve(line, p["wh"], 6, actor=p["stores"].user),
           lambda: inv.issue(line, p["wh"], 4, actor=p["stores"].user),
           lambda: inv.consume(line, 2, actor=p["tech"].user, membership=p["tech"]),
           lambda: inv.return_stock(line, 2, actor=p["stores"].user),
           lambda: inv.release(line, 2, actor=p["stores"].user),
           lambda: inv.transfer(p["wh"], p["wh2"], p["part"], 5, actor=p["stores"].user),
           lambda: inv.adjust(p["wh2"], p["part"], -2, reason="breakage", actor=p["stores"].user)]
    for op in ops:
        op()
        assert_ledger_consistent(p["org"])
    # consume is the only operation here that writes no movement (it changes no stock)
    assert StockMovement.objects.count() == 1 + (len(ops) - 1) + 1  # receipt + 6 ops + the transfer's second row
