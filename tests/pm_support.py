"""Shared builders for the Phase 5 tests (real services only; a frozen clock instead of waiting)."""
from datetime import UTC, date, datetime

from django.utils import timezone

from apps.maintenance import services as pm

T0 = datetime(2026, 3, 10, 6, 0, tzinfo=UTC)  # 2026-03-10 06:00 UTC


def freeze(monkeypatch, moment: datetime):
    """Controllable clock: production code keeps calling ``timezone.now()``, tests decide what it returns."""
    monkeypatch.setattr(timezone, "now", lambda: moment)


def make_plan(p, *, name="Pump service", asset=None, who="planner", **kw):
    return pm.create_plan(p["org"], asset=asset or p["asset"], name=name, actor=p[who].user, **kw)


def time_schedule(plan, *, who_user=None, frequency="MONTHLY", interval_count=1, start_date=date(2026, 3, 10), **kw):
    return pm.create_schedule(plan, actor=who_user, trigger_type="TIME", frequency=frequency,
                              interval_count=interval_count, start_date=start_date, **kw)


def meter_schedule(plan, meter, *, interval_value=500, start_value=0, **kw):
    return pm.create_schedule(plan, actor=None, trigger_type="METER", meter=meter, interval_value=interval_value,
                              start_value=start_value, **kw)
