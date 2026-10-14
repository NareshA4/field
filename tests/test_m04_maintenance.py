"""M04 service rules: plans, schedules, time / meter due, generation through M06, duplicate prevention, missed
cycles, disabled / re-enabled behaviour, checklist integration, reminders, Celery tasks (frozen clock, real services)."""
from datetime import UTC, date, datetime, time, timedelta

import pytest
from django.db import IntegrityError, transaction

from apps.assets import services as asset_services
from apps.audit.models import AuditLog
from apps.checklists import services as cl
from apps.core.exceptions import Conflict, ValidationFailed
from apps.maintenance import services as pm
from apps.maintenance import tasks
from apps.maintenance.models import MaintenanceCycle, MaintenanceSchedule
from apps.notifications.models import Notification
from apps.sites import services as site_services
from apps.workorders import services as wos
from apps.workorders.models import WorkOrder
from tests.phase3_support import good_answers, make_template, step
from tests.pm_support import T0, freeze, make_plan, meter_schedule, time_schedule

pytestmark = pytest.mark.django_db


def at(y, m, d, h=6):
    return datetime(y, m, d, h, 0, tzinfo=UTC)


def pm_orders(org):
    return WorkOrder.objects.filter(organization=org, source_type="PREVENTIVE_MAINTENANCE")


# --- plans / schedules: validation ---------------------------------------------------------------------------------------


def test_plan_creation_validation_and_audit(pm_, org_b, make_site, make_asset, monkeypatch):
    freeze(monkeypatch, T0)
    p = pm_
    plan = make_plan(p, estimated_hours="2.5", priority="HIGH")
    assert (plan.site_id, plan.asset_id, plan.priority, plan.is_active) == (p["site"].pk, p["asset"].pk, "HIGH", True)
    assert AuditLog.objects.filter(organization=p["org"], action="maintenance.plan_created",
                                   target_id=str(plan.pk)).exists()
    with pytest.raises(Conflict) as exc:
        make_plan(p, name="pump SERVICE")
    assert exc.value.code == "plan_name_taken"
    with pytest.raises(ValidationFailed):
        make_plan(p, name="ab")
    with pytest.raises(ValidationFailed):
        make_plan(p, name="Another", priority="SUPER")
    with pytest.raises(ValidationFailed):
        make_plan(p, name="Another", estimated_hours="-1")
    b_asset = make_asset(org_b, make_site(org_b, "B1"), "B-PUMP")
    with pytest.raises(ValidationFailed) as exc:
        make_plan(p, name="Cross tenant", asset=b_asset)
    assert exc.value.code == "cross_tenant_asset"
    asset_row = p["asset2"]
    type(asset_row).objects.filter(pk=asset_row.pk).update(status="RETIRED")
    asset_row.refresh_from_db()
    with pytest.raises(Conflict) as exc:
        make_plan(p, name="Retired asset plan", asset=asset_row)
    assert exc.value.code == "asset_terminal"
    assert type(plan).objects.filter(organization=p["org"]).count() == 1


def test_checklist_association_is_validated_per_organization(pm_, org_b, make_member, monkeypatch):
    freeze(monkeypatch, T0)
    p = pm_
    t = make_template(p, required=False)
    plan = make_plan(p, checklist_key=str(t.key))
    assert plan.checklist_key == str(t.key)
    for bad in ("not-a-uuid", "00000000-0000-0000-0000-000000000000"):
        with pytest.raises(ValidationFailed) as exc:
            make_plan(p, name=f"Bad {bad[:4]}", checklist_key=bad)
        assert exc.value.code == "unknown_checklist"
    b_owner = make_member(org_b, "ops@beta.test", "operations_manager")
    bt = cl.create_template(org_b, actor=b_owner.user, name="Beta checklist")
    with pytest.raises(ValidationFailed):
        make_plan(p, name="Uses beta key", checklist_key=str(bt.key))


@pytest.mark.parametrize("kw,code", [
    ({"frequency": "HOURLY"}, "invalid_frequency"), ({"interval_count": 0}, "invalid_interval"),
    ({"interval_count": "x"}, "invalid_interval"), ({"interval_count": 1001}, "invalid_interval"),
    ({"start_date": None}, "start_date_required"), ({"lead_days": 61}, "invalid_window"),
    ({"window_hours": 0}, "invalid_window"), ({"window_hours": 73}, "invalid_window"),
    ({"reminder_days": -1}, "invalid_window"),
])
def test_invalid_time_schedules_are_rejected(pm_, monkeypatch, kw, code):
    freeze(monkeypatch, T0)
    plan = make_plan(pm_)
    with pytest.raises(ValidationFailed) as exc:
        time_schedule(plan, **kw)
    assert exc.value.code == code
    assert not MaintenanceSchedule.objects.exists()


def test_invalid_meter_schedules_are_rejected(pm_, org_b, make_site, make_asset, monkeypatch):
    freeze(monkeypatch, T0)
    p = pm_
    plan = make_plan(p)
    for bad in (0, -5, "abc"):
        with pytest.raises(ValidationFailed):
            meter_schedule(plan, p["meter"], interval_value=bad)
    other_meter = asset_services.create_meter(p["asset2"], name="Hours", unit="h", actor=None)
    with pytest.raises(ValidationFailed) as exc:
        meter_schedule(plan, other_meter)  # another asset's meter
    assert exc.value.code == "meter_asset_mismatch"
    b_meter = asset_services.create_meter(make_asset(org_b, make_site(org_b, "B1"), "B-PUMP"), name="Hours", unit="h",
                                          actor=None)
    with pytest.raises(ValidationFailed) as exc:
        meter_schedule(plan, b_meter)
    assert exc.value.code == "cross_tenant_meter"
    with pytest.raises(ValidationFailed):
        pm.create_schedule(plan, actor=None, trigger_type="METER", meter=None, interval_value=100)
    with pytest.raises(ValidationFailed):
        pm.create_schedule(plan, actor=None, trigger_type="BOGUS")
    asset_services.set_meter_active(p["meter"], active=False, actor=None)
    with pytest.raises(Conflict) as exc:
        meter_schedule(plan, p["meter"])
    assert exc.value.code == "meter_inactive"


def test_duplicate_schedules_are_prevented(pm_, monkeypatch):
    freeze(monkeypatch, T0)
    p = pm_
    plan = make_plan(p)
    time_schedule(plan)
    with pytest.raises(Conflict) as exc:
        time_schedule(plan, start_date=date(2026, 5, 1))  # same frequency + interval on the same plan
    assert exc.value.code == "duplicate_schedule"
    time_schedule(plan, interval_count=3)  # a different recurrence is fine
    meter_schedule(plan, p["meter"])
    with pytest.raises(Conflict):
        meter_schedule(plan, p["meter"])
    meter_schedule(plan, p["meter"], interval_value=1000)
    assert MaintenanceSchedule.objects.count() == 4


# --- time-based due + generation ---------------------------------------------------------------------------------------


def test_time_based_generation_creates_a_real_planned_work_order(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    plan = make_plan(p, priority="HIGH", estimated_hours="3", description="Check seals.")
    sch = time_schedule(plan, window_hours=6)
    assert (sch.next_sequence, sch.next_due_date) == (0, date(2026, 3, 10))
    cycle = pm.generate_cycle(sch)
    wo = cycle.work_order
    assert (wo.work_type, wo.status, wo.priority, wo.asset_id, wo.site_id) == (
        "PREVENTIVE", "PLANNED", "HIGH", p["asset"].pk, p["site"].pk)
    assert (wo.source_type, wo.source_id) == ("PREVENTIVE_MAINTENANCE", cycle.pk)
    assert wo.planned_start == at(2026, 3, 10, 8) and wo.planned_end == at(2026, 3, 10, 14)
    assert wo.estimated_hours == 3 and "Check seals." in wo.description and wo.title == "PM: Pump service (cycle 0)"
    assert (cycle.sequence, cycle.skipped, cycle.trigger, cycle.organization_id) == (0, 0, "scheduler", p["org"].pk)
    sch.refresh_from_db()
    assert (sch.next_sequence, sch.next_due_date) == (1, date(2026, 4, 10))
    assert AuditLog.objects.filter(organization=p["org"], action="maintenance.cycle_generated",
                                   target_id=str(cycle.pk)).exists()
    assert AuditLog.objects.filter(organization=p["org"], action="work_order.created", target_id=str(wo.pk)).exists()
    assert Notification.objects.filter(recipient=p["planner"].user, title__startswith=wo.number).exists()
    assert wo.maintenance_cycle == cycle


def test_nothing_is_generated_before_the_due_date_and_lead_days_pull_it_forward(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, at(2026, 3, 8))
    sch = time_schedule(make_plan(p), start_date=date(2026, 3, 10))
    assert pm.evaluate(sch) is None and pm.generate_cycle(sch) is None
    assert not pm_orders(p["org"]).exists()
    freeze(monkeypatch, at(2026, 3, 9))
    assert pm.generate_cycle(sch) is None
    freeze(monkeypatch, at(2026, 3, 10, 0))
    assert pm.evaluate(sch).sequence == 0
    # lead days: the order is created early, its window still sits on the due date
    sch2 = time_schedule(make_plan(p, name="Early plan"), start_date=date(2026, 3, 20), lead_days=7)
    freeze(monkeypatch, at(2026, 3, 12))
    assert pm.generate_cycle(sch2) is None
    freeze(monkeypatch, at(2026, 3, 13))
    cycle = pm.generate_cycle(sch2)
    assert cycle.work_order.planned_start == at(2026, 3, 20, 8) and cycle.due_date == date(2026, 3, 20)


def test_due_date_uses_the_site_timezone(pm_, make_site, make_asset, monkeypatch):
    p = pm_
    site = make_site(p["org"], "KOL", timezone="Asia/Kolkata")
    asset = make_asset(p["org"], site, "KOL-PUMP")
    freeze(monkeypatch, at(2026, 3, 8))
    sch = time_schedule(make_plan(p, asset=asset, name="Kolkata plan"), start_date=date(2026, 3, 10),
                        window_start_time=time(9, 0))
    freeze(monkeypatch, at(2026, 3, 9, 17))  # 22:30 on the 9th in Kolkata, still the 9th
    assert pm.evaluate(sch) is None
    freeze(monkeypatch, at(2026, 3, 9, 20))  # 01:30 on the 10th in Kolkata (still the 9th in UTC)
    cycle = pm.generate_cycle(sch)
    assert cycle is not None and cycle.due_date == date(2026, 3, 10)
    # the window is 09:00 SITE time = 03:30 UTC
    assert cycle.work_order.planned_start == datetime(2026, 3, 10, 3, 30, tzinfo=UTC)


def test_planned_window_moves_to_the_next_working_day_of_the_site_calendar(pm_, monkeypatch):
    p = pm_
    site_services.create_calendar(p["site"], actor=None, name="Weekdays", working_days=[1, 2, 3, 4, 5],
                                  is_24x7=False, start_time=time(8), end_time=time(17), is_default=True)
    freeze(monkeypatch, at(2026, 3, 13))
    sch = time_schedule(make_plan(p), start_date=date(2026, 3, 14))  # a Saturday
    freeze(monkeypatch, at(2026, 3, 14))
    cycle = pm.generate_cycle(sch)
    assert cycle.due_date == date(2026, 3, 14)  # the DUE date is not shifted ...
    assert cycle.work_order.planned_start == at(2026, 3, 16, 8)  # ... the window moves to Monday


def test_running_generation_twice_gives_one_cycle_and_one_work_order(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p))
    first = pm.generate_cycle(sch)
    second = pm.generate_cycle(sch)
    third = pm.generate_cycle(MaintenanceSchedule.objects.get(pk=sch.pk))
    assert first is not None and second is None and third is None
    assert MaintenanceCycle.objects.count() == 1 and pm_orders(p["org"]).count() == 1
    assert WorkOrder.objects.filter(organization=p["org"]).count() == 1


def test_database_enforces_one_work_order_per_cycle(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p))
    cycle = pm.generate_cycle(sch)
    with pytest.raises(IntegrityError), transaction.atomic():  # same (schedule, sequence) again
        MaintenanceCycle(organization=p["org"], schedule=sch, sequence=cycle.sequence).save()
    with pytest.raises(IntegrityError), transaction.atomic():  # a second work order for the same PM source
        wos.create_work_order(p["org"], asset=p["asset"], actor=None, title="Duplicate PM order",
                              work_type="PREVENTIVE", source_type="PREVENTIVE_MAINTENANCE", source_id=cycle.pk)
    with pytest.raises(ValidationFailed):  # service refuses a half-specified source
        wos.create_work_order(p["org"], asset=p["asset"], actor=None, title="Half source", source_type="PREVENTIVE_MAINTENANCE")
    with pytest.raises(IntegrityError), transaction.atomic():  # and so does the database
        WorkOrder.objects.filter(pk=cycle.work_order_id).update(source_id=None)
    assert pm_orders(p["org"]).count() == 1


def test_missed_cycles_are_collapsed_into_the_latest_one(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p), start_date=date(2026, 3, 10))  # monthly: 3/10, 4/10, 5/10, 6/10 ...
    freeze(monkeypatch, at(2026, 6, 20))  # the scheduler was down for three months
    cycle = pm.generate_cycle(sch)
    assert (cycle.sequence, cycle.skipped, cycle.due_date) == (3, 3, date(2026, 6, 10))
    assert "3 earlier occurrence(s)" in cycle.work_order.description
    sch.refresh_from_db()
    assert (sch.next_sequence, sch.next_due_date) == (4, date(2026, 7, 10))
    assert pm.generate_cycle(sch) is None and pm_orders(p["org"]).count() == 1  # nothing else is replayed
    freeze(monkeypatch, at(2026, 7, 10))
    nxt = pm.generate_cycle(sch)
    assert (nxt.sequence, nxt.skipped) == (4, 0) and pm_orders(p["org"]).count() == 2


@pytest.mark.parametrize("frequency,interval,start,now,expected_seq,expected_due", [
    ("DAILY", 1, date(2026, 3, 10), at(2026, 3, 13), 3, date(2026, 3, 13)),
    ("WEEKLY", 2, date(2026, 3, 10), at(2026, 4, 8), 2, date(2026, 4, 7)),
    ("QUARTERLY", 1, date(2026, 3, 10), at(2026, 9, 11), 2, date(2026, 9, 10)),
    ("YEARLY", 1, date(2026, 3, 10), at(2028, 3, 10), 2, date(2028, 3, 10)),
    ("MONTHLY", 1, date(2026, 1, 31), at(2026, 3, 1), 1, date(2026, 2, 28)),
])
def test_every_frequency_generates_on_the_right_day(pm_, monkeypatch, frequency, interval, start, now, expected_seq,
                                                    expected_due):
    freeze(monkeypatch, datetime.combine(start, time(1), tzinfo=UTC))
    sch = time_schedule(make_plan(pm_), frequency=frequency, interval_count=interval, start_date=start)
    freeze(monkeypatch, now)
    cycle = pm.generate_cycle(sch)
    assert (cycle.sequence, cycle.due_date) == (expected_seq, expected_due)


# --- enable / disable ----------------------------------------------------------------------------------------------------


def test_disabled_plan_and_schedule_never_generate_and_reenabling_resumes_without_replay(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    plan = make_plan(p)
    sch = time_schedule(plan)
    pm.set_plan_active(plan, False, actor=p["planner"].user)
    freeze(monkeypatch, at(2026, 5, 20))
    assert pm.generate_cycle(sch) is None
    with pytest.raises(Conflict) as exc:
        pm.generate_cycle(sch, manual=True)
    assert exc.value.code == "plan_disabled"
    assert not pm_orders(p["org"]).exists()
    pm.set_plan_active(plan, True, actor=p["planner"].user)  # two months later
    sch.refresh_from_db()
    assert (sch.next_sequence, sch.next_due_date) == (3, date(2026, 6, 10))  # not 0: nothing is replayed
    assert pm.generate_cycle(sch) is None and not pm_orders(p["org"]).exists()
    freeze(monkeypatch, at(2026, 6, 10))
    assert pm.generate_cycle(sch).sequence == 3
    # schedule-level switch
    pm.set_schedule_active(sch, False, actor=p["planner"].user)
    freeze(monkeypatch, at(2026, 7, 10))
    assert pm.generate_cycle(sch) is None
    pm.set_schedule_active(sch, True, actor=p["planner"].user)
    sch.refresh_from_db()
    assert sch.next_sequence == 4 and pm_orders(p["org"]).count() == 1
    assert pm.generate_cycle(sch).sequence == 4
    actions = set(AuditLog.objects.filter(organization=p["org"]).values_list("action", flat=True))
    assert {"maintenance.plan_disabled", "maintenance.plan_enabled", "maintenance.schedule_disabled",
            "maintenance.schedule_enabled"} <= actions


def test_suspended_organization_and_retired_asset_do_not_generate(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p))
    p["org"].status = "SUSPENDED"
    p["org"].save(update_fields=["status"])
    assert pm.generate_cycle(MaintenanceSchedule.objects.get(pk=sch.pk)) is None
    p["org"].status = "ACTIVE"
    p["org"].save(update_fields=["status"])
    type(p["asset"]).objects.filter(pk=p["asset"].pk).update(status="RETIRED")
    assert pm.generate_cycle(MaintenanceSchedule.objects.get(pk=sch.pk)) is None
    assert not pm_orders(p["org"]).exists()


def test_editing_the_recurrence_restarts_it_at_the_next_future_occurrence(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p))
    pm.generate_cycle(sch)
    sch = pm.update_schedule(sch, actor=p["planner"].user, interval_count=2)
    assert sch.interval_count == 2 and sch.next_due_date >= date(2026, 3, 10)
    sch = pm.update_schedule(sch, actor=p["planner"].user, lead_days=3, window_hours=4)
    assert (sch.lead_days, sch.window_hours) == (3, 4)
    with pytest.raises(ValidationFailed):
        pm.update_schedule(sch, actor=p["planner"].user, window_hours=100)
    assert AuditLog.objects.filter(action="maintenance.schedule_updated").count() == 2


# --- meter-based -----------------------------------------------------------------------------------------------------------


_tick = [0]


def reading(monkeypatch, meter, value):
    """Records a reading one minute after the previous one (readings must move forward in time as well)."""
    _tick[0] += 1
    freeze(monkeypatch, T0 + timedelta(minutes=_tick[0]))
    return asset_services.record_reading(meter, value=value, actor=None)


def test_meter_based_due_follows_the_hpe_example(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    reading(monkeypatch, p["meter"], 1200)
    sch = meter_schedule(make_plan(p), p["meter"], interval_value=500)
    assert (sch.next_sequence, sch.next_due_value) == (3, 1500)  # 500 and 1000 are in the past: no backlog
    assert pm.evaluate(sch) is None and pm.generate_cycle(sch) is None
    reading(monkeypatch, p["meter"], 1499)
    assert pm.generate_cycle(sch) is None
    with pytest.raises(ValidationFailed) as exc:  # M02 monotonicity rule is the only meter rule (no second system)
        reading(monkeypatch, p["meter"], 1400)
    assert exc.value.code == "meter_not_monotonic"
    reading(monkeypatch, p["meter"], 1500)
    cycle = pm.generate_cycle(sch)
    assert (cycle.sequence, cycle.due_value, cycle.skipped, cycle.due_date) == (3, 1500, 0, None)
    assert cycle.work_order.source_type == "PREVENTIVE_MAINTENANCE" and "1500" in cycle.work_order.description
    sch.refresh_from_db()
    assert (sch.next_sequence, sch.next_due_value) == (4, 2000)
    assert pm.generate_cycle(sch) is None
    reading(monkeypatch, p["meter"], 2600)  # jumped over 2000 and 2500
    cycle = pm.generate_cycle(sch)
    assert (cycle.sequence, cycle.skipped, cycle.due_value) == (5, 1, 2500)
    sch.refresh_from_db()
    assert sch.next_due_value == 3000 and pm_orders(p["org"]).count() == 2


def test_meter_schedule_without_readings_waits_and_inactive_meter_blocks(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = meter_schedule(make_plan(p), p["meter"], interval_value=100, start_value=50)
    assert (sch.next_sequence, sch.next_due_value) == (1, 150)
    assert pm.generate_cycle(sch) is None
    reading(monkeypatch, p["meter"], 149.999)
    assert pm.generate_cycle(sch) is None
    reading(monkeypatch, p["meter"], 150)
    asset_services.set_meter_active(p["meter"], active=False, actor=None)
    assert pm.generate_cycle(MaintenanceSchedule.objects.get(pk=sch.pk)) is None  # inactive meter: no generation
    asset_services.set_meter_active(p["meter"], active=True, actor=None)
    assert pm.generate_cycle(MaintenanceSchedule.objects.get(pk=sch.pk)).due_value == 150


# --- manual generation -------------------------------------------------------------------------------------------------------


def test_manual_generation_is_early_once_and_blocked_while_the_previous_order_is_open(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p), start_date=date(2026, 4, 1))
    cycle = pm.generate_cycle(sch, actor=p["planner"].user, manual=True)
    assert (cycle.sequence, cycle.trigger, cycle.generated_by_id) == (0, "manual", p["planner"].user.pk)
    assert cycle.work_order.planned_start == at(2026, 4, 1, 8)
    with pytest.raises(Conflict) as exc:  # a double click must not create the next occurrence as well
        pm.generate_cycle(sch, actor=p["planner"].user, manual=True)
    assert exc.value.code == "previous_cycle_open"
    step(cycle.work_order, "cancel", p, "planner", reason="Not needed this time")
    nxt = pm.generate_cycle(sch, actor=p["planner"].user, manual=True)
    assert nxt.sequence == 1 and pm_orders(p["org"]).count() == 2


# --- checklist integration (M08 stays authoritative) ---------------------------------------------------------------------------


def test_plan_checklist_becomes_a_required_checklist_of_the_generated_work_order(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    template = make_template(p, required=False, name="PM pump inspection")  # NOT required globally
    sch = time_schedule(make_plan(p, checklist_key=str(template.key)))
    wo = pm.generate_cycle(sch).work_order
    other = wos.create_work_order(p["org"], asset=p["asset"], actor=None, title="Plain job", work_type="PREVENTIVE")
    assert template in list(cl.required_templates(wo)) and template not in list(cl.required_templates(other))
    assert any("PM pump inspection" in b and "has not been started" in b for b in wos.closure_blockers(wo))
    assert not any("PM pump inspection" in b for b in wos.closure_blockers(other))
    wo = step(wo, "assign", p, "planner", technician=p["tech"])
    wo = step(wo, "dispatch", p, "planner")
    wo = step(wo, "start", p, "tech")
    with pytest.raises(Conflict) as exc:  # M08 completion guard applies to the PM order
        step(wo, "complete", p, "tech", resolution_notes="Done without the checklist.")
    assert exc.value.code == "checklist_incomplete"
    insp = cl.start_inspection(p["org"], template=template, membership=p["tech"], actor=p["tech"].user, work_order=wo)
    cl.save_responses(insp, good_answers(template), membership=p["tech"], actor=p["tech"].user)
    cl.complete_inspection(insp, membership=p["tech"], actor=p["tech"].user)
    assert not [b for b in wos.closure_blockers(wo) if "PM pump inspection" in b]
    from apps.checklists.selectors import pending_required_counts

    assert pending_required_counts(p["org"], [wo, other]) == {wo.pk: 0, other.pk: 0}


def test_pending_counts_include_the_plan_checklist_until_it_is_done(pm_, monkeypatch):
    from apps.checklists.selectors import pending_required_counts

    p = pm_
    freeze(monkeypatch, T0)
    template = make_template(p, required=False, name="PM seal check")
    wo = pm.generate_cycle(time_schedule(make_plan(p, checklist_key=str(template.key)))).work_order
    assert pending_required_counts(p["org"], [wo]) == {wo.pk: 1}


# --- reminders -------------------------------------------------------------------------------------------------------------------


def test_reminder_is_sent_once_per_occurrence(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, at(2026, 3, 10))
    sch = time_schedule(make_plan(p), start_date=date(2026, 3, 12), reminder_days=3)
    freeze(monkeypatch, at(2026, 3, 8))
    assert pm.remind(sch) is False  # 4 days out: too early
    freeze(monkeypatch, at(2026, 3, 9))
    assert pm.remind(sch) is True
    assert pm.remind(sch) is False
    assert Notification.objects.filter(recipient=p["planner"].user, title__startswith="Maintenance reminder").count() == 1
    freeze(monkeypatch, at(2026, 3, 12))
    cycle = pm.generate_cycle(sch)  # due: the order is generated, the next occurrence will remind again later
    assert cycle is not None
    assert pm.remind(sch) is False  # next due is a month away


# --- Celery tasks ----------------------------------------------------------------------------------------------------------------


def test_scheduler_task_is_idempotent_and_tenant_safe(pm_, org_b, make_member, make_site, make_asset, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    time_schedule(make_plan(p))
    b_site = make_site(org_b, "B1")
    b_asset = make_asset(org_b, b_site, "B-PUMP")
    b_planner = make_member(org_b, "planner@beta.test", "maintenance_planner")
    time_schedule(pm.create_plan(org_b, asset=b_asset, name="Beta pump service", actor=b_planner.user))
    suspended = type(org_b).objects.create(name="Suspended Co", slug="suspended-co", status="SUSPENDED")
    assert suspended.status == "SUSPENDED"
    first = tasks.generate_due_maintenance()
    assert first["generated"] == 2 and first["errors"] == 0
    second = tasks.generate_due_maintenance()  # a retry / a second beat tick
    assert second["generated"] == 0
    assert pm_orders(p["org"]).count() == 1 and pm_orders(org_b).count() == 1
    assert {w.organization_id for w in pm_orders(p["org"])} == {p["org"].pk}
    assert pm_orders(p["org"]).get().asset.organization_id == p["org"].pk
    assert pm_orders(org_b).get().asset_id == b_asset.pk


def test_failed_generation_rolls_back_completely_and_the_retry_creates_exactly_one(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p))

    def boom(*a, **k):
        raise RuntimeError("work order service unavailable")

    original = wos.create_work_order
    monkeypatch.setattr(wos, "create_work_order", boom)
    result = tasks.generate_due_maintenance()
    assert result["errors"] == 1 and result["generated"] == 0
    sch.refresh_from_db()
    assert (sch.next_sequence, sch.next_due_date) == (0, date(2026, 3, 10))  # not advanced
    assert MaintenanceCycle.objects.count() == 0 and not pm_orders(p["org"]).exists()  # no orphan cycle
    assert "unavailable" in sch.last_error and sch.last_run_at is not None
    monkeypatch.setattr(wos, "create_work_order", original)
    assert tasks.generate_due_maintenance()["generated"] == 1
    assert tasks.generate_due_maintenance()["generated"] == 0
    sch.refresh_from_db()
    assert sch.last_error == "" and MaintenanceCycle.objects.count() == 1 and pm_orders(p["org"]).count() == 1


def test_single_schedule_task_can_be_retried_safely(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    sch = time_schedule(make_plan(p))
    first = tasks.generate_schedule.apply(args=[str(p["org"].pk), str(sch.pk)]).get()
    again = tasks.generate_schedule.apply(args=[str(p["org"].pk), str(sch.pk)]).get()
    assert first is not None and again is None
    assert pm_orders(p["org"]).count() == 1


def test_beat_schedule_registers_the_task():
    from django.conf import settings

    assert settings.CELERY_BEAT_SCHEDULE["generate-due-maintenance"]["task"] == (
        "apps.maintenance.tasks.fan_out_maintenance")  # beat -> per-organization fan-out (D-056)


def test_disabled_work_is_not_picked_up_by_the_task(pm_, monkeypatch):
    p = pm_
    freeze(monkeypatch, T0)
    plan = make_plan(p)
    time_schedule(plan)
    pm.set_plan_active(plan, False, actor=None)
    assert tasks.generate_due_maintenance()["generated"] == 0
    pm.set_plan_active(plan, True, actor=None)
    assert tasks.generate_due_maintenance()["generated"] == 1
    assert (T0 + timedelta(seconds=1)) > T0
