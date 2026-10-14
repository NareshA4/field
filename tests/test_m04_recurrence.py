"""M04 recurrence arithmetic (pure functions): calendar cadence without drift, collapse maths, meter thresholds."""
from datetime import date
from decimal import Decimal as D

import pytest

from apps.maintenance import recurrence as r


def test_add_months_clamps_to_month_end_and_never_drifts():
    start = date(2026, 1, 31)
    assert r.add_months(start, 1) == date(2026, 2, 28)
    assert r.add_months(start, 2) == date(2026, 3, 31)  # computed from the start, not from February
    assert r.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert r.add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert r.add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)


@pytest.mark.parametrize("freq,interval,k,expected", [
    ("DAILY", 1, 3, date(2026, 3, 13)), ("DAILY", 10, 2, date(2026, 3, 30)), ("WEEKLY", 1, 1, date(2026, 3, 17)),
    ("WEEKLY", 2, 2, date(2026, 4, 7)), ("MONTHLY", 1, 1, date(2026, 4, 10)), ("MONTHLY", 3, 1, date(2026, 6, 10)),
    ("QUARTERLY", 1, 2, date(2026, 9, 10)), ("YEARLY", 1, 1, date(2027, 3, 10)), ("YEARLY", 2, 1, date(2028, 3, 10)),
    ("MONTHLY", 1, 0, date(2026, 3, 10)),
])
def test_time_due_date(freq, interval, k, expected):
    assert r.time_due_date(date(2026, 3, 10), freq, interval, k) == expected


@pytest.mark.parametrize("freq,interval", [("DAILY", 1), ("DAILY", 3), ("WEEKLY", 1), ("MONTHLY", 1), ("MONTHLY", 2),
                                           ("QUARTERLY", 1), ("YEARLY", 1)])
def test_latest_sequence_is_consistent_with_due_dates(freq, interval):
    start = date(2026, 1, 31)
    for offset in range(0, 800, 7):
        horizon = date.fromordinal(start.toordinal() + offset)
        k = r.latest_time_sequence(start, freq, interval, horizon)
        assert r.time_due_date(start, freq, interval, k) <= horizon
        assert r.time_due_date(start, freq, interval, k + 1) > horizon


def test_latest_sequence_before_start_is_minus_one():
    assert r.latest_time_sequence(date(2026, 3, 10), "MONTHLY", 1, date(2026, 3, 9)) == -1
    assert r.latest_time_sequence(date(2026, 3, 10), "MONTHLY", 1, date(2026, 3, 10)) == 0


def test_first_sequence_from_a_day():
    start = date(2026, 3, 10)
    assert r.first_time_sequence_from(start, "MONTHLY", 1, date(2026, 3, 1)) == 0
    assert r.first_time_sequence_from(start, "MONTHLY", 1, date(2026, 3, 10)) == 0
    assert r.first_time_sequence_from(start, "MONTHLY", 1, date(2026, 3, 11)) == 1
    assert r.first_time_sequence_from(start, "MONTHLY", 1, date(2026, 4, 10)) == 1
    assert r.first_time_sequence_from(start, "DAILY", 7, date(2026, 3, 18)) == 2


def test_meter_thresholds_follow_the_hpe_example():
    # every 500 running hours, current 1200 -> thresholds 500, 1000 passed, next 1500
    assert r.latest_meter_sequence(D(0), D(500), D(1200)) == 2
    assert r.first_meter_sequence_after(D(0), D(500), D(1200)) == 3
    assert r.meter_threshold(D(0), D(500), 3) == 1500
    assert r.latest_meter_sequence(D(0), D(500), D(499)) == 0
    assert r.latest_meter_sequence(D(0), D(500), D(500)) == 1
    assert r.first_meter_sequence_after(D(0), D(500), D(500)) == 2
    assert r.latest_meter_sequence(D(100), D(50), D("149.999")) == 0
    assert r.latest_meter_sequence(D(100), D(50), D(150)) == 1
    assert r.first_meter_sequence_after(D(100), D(50), D(20)) == 1
