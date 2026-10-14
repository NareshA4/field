"""Pure recurrence arithmetic for M04 (no database access, no clock: callers pass ``today`` / the latest reading).

Time-based occurrences are calendar dates in the SITE's timezone: due(k) = start_date + k x step, k = 0, 1, 2 ...
(step = interval x day / week / month / quarter / year; months are added on the calendar and clamped to the last
day of a shorter month, always from ``start_date`` so the cadence never drifts: Jan 31 + 1 month = Feb 28/29,
+ 2 months = Mar 31). Meter-based occurrences are thresholds: threshold(k) = start_value + k x interval, k = 1, 2, 3.

Missed occurrences are COLLAPSED (D-043): when several are due at once, only the latest one is generated and the
older ones are counted as ``skipped``; the next occurrence is the one after it.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

DAYS = {"DAILY": 1, "WEEKLY": 7}
MONTHS = {"MONTHLY": 1, "QUARTERLY": 3, "YEARLY": 12}


def add_months(d: date, months: int) -> date:
    index = d.year * 12 + (d.month - 1) + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(d.day, calendar.monthrange(year, month + 1)[1]))


def time_due_date(start: date, frequency: str, interval: int, k: int) -> date:
    if frequency in DAYS:
        return start + timedelta(days=DAYS[frequency] * interval * k)
    return add_months(start, MONTHS[frequency] * interval * k)


def latest_time_sequence(start: date, frequency: str, interval: int, horizon: date) -> int:
    """Largest k >= 0 with due(k) <= horizon, or -1 when even due(0) is after the horizon."""
    if horizon < start:
        return -1
    if frequency in DAYS:
        return (horizon - start).days // (DAYS[frequency] * interval)
    step = MONTHS[frequency] * interval
    months = (horizon.year - start.year) * 12 + (horizon.month - start.month)
    k = months // step
    while k >= 0 and add_months(start, step * k) > horizon:
        k -= 1
    return k


def first_time_sequence_from(start: date, frequency: str, interval: int, day: date) -> int:
    """Smallest k >= 0 with due(k) >= day (the next occurrence that is not in the past)."""
    if day <= start:
        return 0
    k = latest_time_sequence(start, frequency, interval, day) + 1
    if time_due_date(start, frequency, interval, k - 1) >= day:
        k -= 1
    return max(k, 0)


def meter_threshold(start_value: Decimal, interval: Decimal, k: int) -> Decimal:
    return start_value + interval * k


def latest_meter_sequence(start_value: Decimal, interval: Decimal, reading: Decimal) -> int:
    """Largest k >= 1 whose threshold the reading has reached, or 0 when none."""
    if reading < start_value + interval:
        return 0
    return int((reading - start_value) // interval)


def first_meter_sequence_after(start_value: Decimal, interval: Decimal, reading: Decimal) -> int:
    """Smallest k >= 1 whose threshold is strictly above the reading."""
    if reading < start_value:
        return 1
    return int((reading - start_value) // interval) + 1


@dataclass(frozen=True)
class Due:
    sequence: int  # the occurrence to generate (the latest one that is due)
    skipped: int  # older occurrences collapsed into it
    due_date: date | None = None
    due_value: Decimal | None = None
