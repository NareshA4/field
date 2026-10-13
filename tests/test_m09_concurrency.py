"""M09 concurrency: real threads on real PostgreSQL connections (committed data, row locks, no mocks).
The balance must never go negative, never lose an update and never over-reserve."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.db import connection
from django.db.models import Sum
from django.utils import timezone

from apps.core.exceptions import DomainError
from apps.inventory import services as inv
from apps.inventory.models import PartReservation, StockBalance, StockMovement
from tests.phase3_support import new_wo, step

pytestmark = pytest.mark.django_db(transaction=True)


def started_work_orders(p, n):
    """n live work orders (distinct planned windows so one technician can hold them all)."""
    out = []
    t0 = timezone.now() + timedelta(days=1)
    for i in range(n):
        wo = new_wo(p, title=f"Concurrent job {i}")
        start = t0 + timedelta(hours=3 * i)
        wo = step(wo, "plan", p, "planner", planned_start=start, planned_end=start + timedelta(hours=2))
        wo = step(wo, "assign", p, "planner", technician=p["tech"])
        wo = step(wo, "dispatch", p, "planner")
        out.append(step(wo, "start", p, "tech"))
    return out


def run_parallel(calls):
    """Runs every callable in its own thread (own DB connection), released together; returns 'ok' / error code."""
    barrier = Barrier(len(calls))

    def worker(fn):
        try:
            barrier.wait(timeout=30)
            fn()
            return "ok"
        except DomainError as exc:
            return exc.code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(worker, calls))


def assert_consistent(wh, part):
    b = StockBalance.objects.get(warehouse=wh, part=part)
    agg = StockMovement.objects.filter(balance=b).aggregate(on=Sum("on_hand_delta"), res=Sum("reserved_delta"))
    held = PartReservation.objects.filter(warehouse=wh, part=part, status="ACTIVE").aggregate(q=Sum("quantity"))["q"]
    assert b.on_hand >= 0 and 0 <= b.reserved <= b.on_hand
    assert (agg["on"] or 0) == b.on_hand and (agg["res"] or 0) == b.reserved == (held or 0)
    return b


def test_two_concurrent_issues_of_the_last_unit_exactly_one_succeeds(inv_):
    p = inv_
    inv.receive(p["wh"], p["part"], 1, actor=p["stores"].user)
    lines = [inv.request_part(wo, p["part"], 1, actor=p["tech"].user, membership=p["tech"])
             for wo in started_work_orders(p, 2)]
    results = run_parallel([lambda ln=ln: inv.issue(ln, p["wh"], 1, actor=p["stores"].user) for ln in lines])
    assert sorted(results) == ["insufficient_available", "ok"]
    b = assert_consistent(p["wh"], p["part"])
    assert b.on_hand == 0  # never -1
    assert StockMovement.objects.filter(movement_type="ISSUE").count() == 1


def test_many_concurrent_issues_never_oversell(inv_):
    p = inv_
    inv.receive(p["wh"], p["part"], 3, actor=p["stores"].user)
    lines = [inv.request_part(wo, p["part"], 1, actor=p["tech"].user, membership=p["tech"])
             for wo in started_work_orders(p, 6)]
    results = run_parallel([lambda ln=ln: inv.issue(ln, p["wh"], 1, actor=p["stores"].user) for ln in lines])
    assert results.count("ok") == 3 and results.count("insufficient_available") == 3
    assert assert_consistent(p["wh"], p["part"]).on_hand == 0


def test_concurrent_reservations_never_exceed_available(inv_):
    p = inv_
    inv.receive(p["wh"], p["part"], 5, actor=p["stores"].user)
    lines = [inv.request_part(wo, p["part"], 2, actor=p["tech"].user, membership=p["tech"])
             for wo in started_work_orders(p, 4)]
    results = run_parallel([lambda ln=ln: inv.reserve(ln, p["wh"], 2, actor=p["stores"].user) for ln in lines])
    assert results.count("ok") == 2 and results.count("insufficient_available") == 2
    b = assert_consistent(p["wh"], p["part"])
    assert (b.on_hand, b.reserved, b.available) == (5, 4, 1)


def test_concurrent_issues_on_the_same_line_respect_the_requirement(inv_):
    p = inv_
    inv.receive(p["wh"], p["part"], 10, actor=p["stores"].user)
    line = inv.request_part(started_work_orders(p, 1)[0], p["part"], 1, actor=p["tech"].user, membership=p["tech"])
    results = run_parallel([lambda: inv.issue(line, p["wh"], 1, actor=p["stores"].user) for _ in range(4)])
    assert results.count("ok") == 1 and results.count("exceeds_requirement") == 3
    assert assert_consistent(p["wh"], p["part"]).on_hand == 9


def test_concurrent_first_receipts_create_one_balance_and_lose_nothing(inv_):
    p = inv_
    results = run_parallel([lambda: inv.receive(p["wh"], p["part"], 1, actor=p["stores"].user) for _ in range(6)])
    assert results == ["ok"] * 6
    assert StockBalance.objects.filter(warehouse=p["wh"], part=p["part"]).count() == 1
    assert assert_consistent(p["wh"], p["part"]).on_hand == 6
    assert StockMovement.objects.filter(movement_type="RECEIPT").count() == 6


def test_opposite_transfers_do_not_deadlock_and_conserve_stock(inv_):
    p = inv_
    inv.receive(p["wh"], p["part"], 10, actor=p["stores"].user)
    inv.receive(p["wh2"], p["part"], 10, actor=p["stores"].user)
    calls = []
    for _ in range(4):
        calls.append(lambda: inv.transfer(p["wh"], p["wh2"], p["part"], 2, actor=p["stores"].user))
        calls.append(lambda: inv.transfer(p["wh2"], p["wh"], p["part"], 3, actor=p["stores"].user))
    results = run_parallel(calls)
    assert set(results) == {"ok"}
    a, b = assert_consistent(p["wh"], p["part"]), assert_consistent(p["wh2"], p["part"])
    assert a.on_hand + b.on_hand == 20
    assert (a.on_hand, b.on_hand) == (10 - 8 + 12, 10 + 8 - 12)


def test_concurrent_issue_and_release_on_one_line_stay_consistent(inv_):
    p = inv_
    inv.receive(p["wh"], p["part"], 5, actor=p["stores"].user)
    wo = started_work_orders(p, 1)[0]
    line = inv.request_part(wo, p["part"], 5, actor=p["tech"].user, membership=p["tech"])
    inv.reserve(line, p["wh"], 3, actor=p["stores"].user)
    results = run_parallel([lambda: inv.issue(line, p["wh"], 2, actor=p["stores"].user),
                            lambda: inv.release(line, 3, actor=p["stores"].user)])
    assert results.count("ok") >= 1  # whichever order the locks were taken in, the books balance
    b = assert_consistent(p["wh"], p["part"])
    assert b.on_hand == 3  # the issue of 2 always happens; the release only succeeds if it ran first or fits
