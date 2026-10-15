"""M04 concurrency: several real workers (threads, own PostgreSQL connections) racing on the same due schedule must
produce exactly one cycle and one work order."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connection

from apps.core.exceptions import DomainError
from apps.maintenance import services as pm
from apps.maintenance import tasks
from apps.maintenance.models import MaintenanceCycle, MaintenanceSchedule
from apps.workorders.models import WorkOrder
from tests.pm_support import T0, freeze, make_plan, time_schedule

pytestmark = pytest.mark.django_db(transaction=True)


def race(calls):
    barrier = Barrier(len(calls))

    def worker(fn):
        try:
            barrier.wait(timeout=30)
            return fn()
        except DomainError as exc:
            return exc.code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(worker, calls))


def test_concurrent_generation_creates_one_cycle_and_one_work_order(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p))
    results = race([lambda: pm.generate_cycle(MaintenanceSchedule.objects.get(pk=sch.pk)) for _ in range(6)])
    created = [r for r in results if isinstance(r, MaintenanceCycle)]
    assert len(created) == 1 and results.count(None) == 5
    assert MaintenanceCycle.objects.count() == 1
    assert WorkOrder.objects.filter(source_type="PREVENTIVE_MAINTENANCE").count() == 1
    assert MaintenanceSchedule.objects.get(pk=sch.pk).next_sequence == 1


def test_concurrent_scheduler_runs_for_two_organizations(pm_, org_b, make_member, make_site, make_asset, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    time_schedule(make_plan(p))
    b_planner = make_member(org_b, "planner@beta.test", "maintenance_planner")
    b_asset = make_asset(org_b, make_site(org_b, "B1"), "B-PUMP")
    time_schedule(pm.create_plan(org_b, asset=b_asset, name="Beta pump service", actor=b_planner.user))
    results = race([tasks.generate_due_maintenance for _ in range(4)])
    assert sum(r["generated"] for r in results) == 2 and sum(r["errors"] for r in results) == 0
    orders = WorkOrder.objects.filter(source_type="PREVENTIVE_MAINTENANCE")
    assert orders.count() == 2 and {o.organization_id for o in orders} == {p["org"].pk, org_b.pk}


def test_concurrent_manual_generation_cannot_create_two_early_orders(pm_, monkeypatch):
    from datetime import date

    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p), start_date=date(2026, 6, 1))
    results = race([lambda: pm.generate_cycle(MaintenanceSchedule.objects.get(pk=sch.pk), actor=p["planner"].user,
                                              manual=True) for _ in range(4)])
    assert len([r for r in results if isinstance(r, MaintenanceCycle)]) == 1
    assert results.count("previous_cycle_open") == 3
    assert WorkOrder.objects.filter(source_type="PREVENTIVE_MAINTENANCE").count() == 1
