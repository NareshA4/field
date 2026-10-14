"""BX-M04-01 / F-H01 regression: editing a schedule's recurrence must never point it at an occurrence that already has a
work order, must never reuse a sequence number, must keep generating, and every resolution is audited
(frozen clock, real services)."""
from datetime import UTC, date, datetime, timedelta

import pytest
from django.test import Client

from apps.audit.models import AuditLog
from apps.maintenance import services as pm
from apps.maintenance import tasks
from apps.maintenance.models import MaintenanceCycle, MaintenanceSchedule
from apps.workorders.models import WorkOrder
from tests.pm_support import T0, freeze, make_plan, time_schedule

pytestmark = pytest.mark.django_db


def at(y, m, d, h=6):
    return datetime(y, m, d, h, 0, tzinfo=UTC)


def orders(org):
    return WorkOrder.objects.filter(organization=org, source_type="PREVENTIVE_MAINTENANCE")


def seqs(sch):
    return list(MaintenanceCycle.objects.filter(schedule=sch).order_by("sequence").values_list("sequence", flat=True))


def fresh(sch):
    return MaintenanceSchedule.objects.get(pk=sch.pk)


def test_edit_after_generation_does_not_offer_the_generated_occurrence_again(pm_, monkeypatch):
    """The browser scenario: monthly from today, cycle 0 generated, interval 1 -> 2."""
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p))                      # start 2026-03-10 = today
    assert pm.generate_cycle(sch).sequence == 0
    sch = pm.update_schedule(sch, actor=p["planner"].user, interval_count=2)
    assert sch.next_due_date == date(2026, 5, 10)          # not today: 2026-03-10 already has its work order
    assert sch.next_sequence == 1 and pm.evaluate(sch) is None
    assert pm.blocked_reason(sch) == ""
    assert pm.generate_cycle(sch) is None and orders(p["org"]).count() == 1   # nothing is DUE, nothing duplicated
    assert not MaintenanceSchedule.objects.get(pk=sch.pk).last_error


def test_manual_generate_after_edit_creates_the_next_new_occurrence_not_a_duplicate(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p))
    first = pm.generate_cycle(sch)
    sch = pm.update_schedule(sch, actor=p["planner"].user, interval_count=2)
    wo = first.work_order
    WorkOrder.objects.filter(pk=wo.pk).update(status="CLOSED")
    second = pm.generate_cycle(fresh(sch), actor=p["planner"].user, manual=True)
    assert second is not None and second.sequence == 1 and second.due_date == date(2026, 5, 10)
    assert orders(p["org"]).count() == 2 and seqs(sch) == [0, 1]


def test_edit_after_many_cycles_reindexes_above_existing_and_keeps_generating(pm_, monkeypatch):
    """The scenario of the original audit: weekly, 8 cycles, then every 2 weeks: generation must continue."""
    p = pm_
    start = date(2026, 3, 10)
    freeze(monkeypatch, at(2026, 3, 10))
    sch = time_schedule(make_plan(p), frequency="WEEKLY", interval_count=1, start_date=start)
    for week in range(8):
        freeze(monkeypatch, at(2026, 3, 10) + timedelta(weeks=week))
        cycle = pm.generate_cycle(fresh(sch))
        assert cycle is not None
        WorkOrder.objects.filter(pk=cycle.work_order_id).update(status="CLOSED")
    assert seqs(sch) == list(range(8))
    now = at(2026, 3, 10) + timedelta(weeks=7)
    freeze(monkeypatch, now)
    sch = pm.update_schedule(fresh(sch), actor=p["planner"].user, interval_count=2)
    assert sch.next_sequence > 7 and sch.next_sequence == sch.sequence_offset + (sch.next_sequence - sch.sequence_offset)
    assert sch.next_due_date > date(2026, 4, 28)           # strictly after the last generated occurrence
    generated = len(seqs(sch))
    for week in range(1, 11):                              # ten more weeks: every 2 weeks => about 5 new orders
        freeze(monkeypatch, now + timedelta(weeks=week))
        cycle = pm.generate_cycle(fresh(sch))
        if cycle is not None:
            WorkOrder.objects.filter(pk=cycle.work_order_id).update(status="CLOSED")
    new = seqs(sch)[generated:]
    assert 4 <= len(new) <= 6, new                         # was exactly 1 before the fix
    assert seqs(sch) == sorted(set(seqs(sch))) and orders(p["org"]).count() == len(seqs(sch))
    due_dates = list(MaintenanceCycle.objects.filter(schedule=sch).order_by("sequence").values_list("due_date", flat=True))
    assert due_dates == sorted(set(due_dates))             # no date generated twice, strictly increasing


def test_recurrence_edit_is_audited_with_what_was_resolved(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p))
    pm.generate_cycle(sch)
    sch = pm.update_schedule(sch, actor=p["planner"].user, interval_count=2)
    log = AuditLog.objects.filter(action="maintenance.schedule_updated", target_id=str(sch.pk)).latest("occurred_at")
    resync = log.metadata["resync"]
    assert resync["already_generated_occurrences_skipped"] == 1 and resync["next_sequence"] == 1
    assert resync["highest_existing_sequence"] == 0 and log.after["next_sequence"] == 1


def test_a_noop_resync_is_still_recorded_when_the_recurrence_changes(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p), start_date=date(2026, 6, 1))          # nothing generated yet
    sch = pm.update_schedule(sch, actor=p["planner"].user, interval_count=3)
    log = AuditLog.objects.filter(action="maintenance.schedule_updated").latest("occurred_at")
    assert log.metadata["resync"]["reindexed"] is False and sch.next_due_date == date(2026, 6, 1)


def test_disable_enable_does_not_offer_the_generated_occurrence_again(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p))
    pm.generate_cycle(sch)
    pm.set_schedule_active(fresh(sch), False, actor=p["planner"].user)
    sch = pm.set_schedule_active(fresh(sch), True, actor=p["planner"].user)
    assert sch.next_sequence == 1 and sch.next_due_date == date(2026, 4, 10)
    assert pm.generate_cycle(sch) is None and orders(p["org"]).count() == 1
    log = AuditLog.objects.filter(action="maintenance.schedule_enabled").latest("occurred_at")
    assert log.metadata["resync"]["already_generated_occurrences_skipped"] == 1


def test_duplicate_generation_stays_impossible_after_an_edit(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p), frequency="DAILY", interval_count=1)
    pm.generate_cycle(sch)
    sch = pm.update_schedule(sch, actor=p["planner"].user, interval_count=2)
    freeze(monkeypatch, T0 + timedelta(days=2))
    first = pm.generate_cycle(fresh(sch))
    again = pm.generate_cycle(fresh(sch))
    assert first is not None and again is None and orders(p["org"]).count() == 2
    assert tasks.generate_due_maintenance()["generated"] == 0
    assert seqs(sch) == [0, 1]


def test_missed_occurrences_after_an_edit_are_still_collapsed_deterministically(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p), frequency="DAILY", interval_count=1)
    pm.generate_cycle(sch)                                                # sequence 0 (day 0)
    sch = pm.update_schedule(sch, actor=p["planner"].user, interval_count=2)   # every 2 days from day 0
    freeze(monkeypatch, T0 + timedelta(days=9))                           # occurrences on days 2,4,6,8 were missed
    cycle = pm.generate_cycle(fresh(sch))
    assert cycle is not None and cycle.due_date == date(2026, 3, 18)      # the latest one (day 8)
    assert cycle.skipped == 3 and cycle.sequence == 4                     # 1,2,3 collapsed into 4
    assert fresh(sch).next_due_date == date(2026, 3, 20)


def test_meter_interval_edit_never_regenerates_a_reached_threshold(pm_, monkeypatch):
    from tests.test_m04_maintenance import reading
    p = pm_
    freeze(monkeypatch, T0)
    sch = pm.create_schedule(make_plan(p), actor=None, trigger_type="METER", meter=p["meter"], interval_value=500,
                             start_value=0)
    reading(monkeypatch, p["meter"], 520)
    first = pm.generate_cycle(fresh(sch))
    assert first.due_value == 500
    sch = pm.update_schedule(fresh(sch), actor=p["planner"].user, interval_value=250)
    assert sch.next_due_value == 750 and sch.next_sequence > first.sequence
    assert pm.generate_cycle(sch) is None and orders(p["org"]).count() == 1
    reading(monkeypatch, p["meter"], 760)
    second = pm.generate_cycle(fresh(sch))
    assert second.due_value == 750 and second.sequence > first.sequence and orders(p["org"]).count() == 2


def test_integrity_collision_is_never_silent(pm_, monkeypatch):
    """Defence in depth: if a cycle for the occurrence exists anyway (corrupt data / concurrent writer) nothing is created
    twice, but the schedule records the error and an audit entry is written."""
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p))
    first = pm.generate_cycle(sch)
    MaintenanceSchedule.objects.filter(pk=sch.pk).update(next_sequence=0, next_due_date=date(2026, 3, 10))   # corrupt it
    assert pm.generate_cycle(fresh(sch)) is None
    s = fresh(sch)
    assert "already had a cycle" in s.last_error and s.next_sequence == 1
    assert AuditLog.objects.filter(action="maintenance.cycle_collision", target_id=str(sch.pk)).count() == 1
    assert orders(p["org"]).count() == 1 and first.work_order_id


def test_ui_after_edit_the_schedule_is_not_due_and_generate_says_nothing_due(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p))
    pm.generate_cycle(sch)
    c = Client()
    c.force_login(p["planner"].user)
    r = c.post(f"/app/maintenance/schedules/{sch.pk}/edit/", {"frequency": "MONTHLY", "interval_count": "2",
               "start_date": "2026-03-10", "lead_days": "0", "window_start_time": "08:00", "window_hours": "8",
               "reminder_days": "0"})
    assert r.status_code == 302
    page = c.get(f"/app/maintenance/schedules/{sch.pk}/")
    html = page.content.decode()
    assert page.context["state"] == "SCHEDULED" and "10 May 2026" in html
    assert orders(p["org"]).count() == 1
