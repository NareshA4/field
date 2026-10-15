"""M04 service layer: maintenance plans, schedules and the PM generator.

Callers (API / UI / Celery) resolve objects through organization- and site-scoped selectors and check the
permission for the plan's site first. This layer enforces tenancy of references, schedule validity, state
(enabled / disabled, terminal assets, inactive meters) and the generation rules, all in one transaction with an
audit record.

Duplicate prevention (one cycle = one work order), three layers: (1) the schedule row is locked
(``select_for_update``) so concurrent workers serialise and the loser sees the already-advanced ``next_sequence``;
(2) ``MaintenanceCycle(schedule, sequence)`` is UNIQUE and ``WorkOrder(source_id)`` is UNIQUE for PM sources, so even
a bug cannot create a second order for one occurrence; (3) the task is idempotent: running it again changes nothing
until the next occurrence is due. Work orders are created ONLY through ``workorders.services.create_work_order``;
M04 never edits a work order afterwards (M06 owns the lifecycle).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import Conflict, ValidationFailed
from apps.rbac import services as rbac

from . import recurrence as rec
from .models import MaintenanceCycle, MaintenancePlan, MaintenanceSchedule, num

log = logging.getLogger(__name__)
Trigger = MaintenanceSchedule.Trigger
PLAN_FIELDS = ["name", "description", "priority", "estimated_hours", "checklist_key", "is_active"]
SCHEDULE_FIELDS = ["trigger_type", "frequency", "interval_count", "start_date", "meter", "interval_value",
                   "start_value", "lead_days", "window_start_time", "window_hours", "reminder_days", "is_active",
                   "next_sequence", "sequence_offset", "next_due_date", "next_due_value"]
PRIORITIES = ("LOW", "MEDIUM", "HIGH", "URGENT")
TERMINAL_ASSET = ("RETIRED", "DISPOSED")


# --- time helpers ----------------------------------------------------------------------------------------------------


def site_zone(site) -> ZoneInfo:
    try:
        return ZoneInfo(site.timezone or "UTC")
    except Exception:  # unknown zone name: UTC is the documented fallback
        return ZoneInfo("UTC")


def site_today(site, now: datetime | None = None) -> date:
    return (now or timezone.now()).astimezone(site_zone(site)).date()


def _working_day(site, day: date) -> date:
    """Moves a planned date forward to the next working day of the site's default calendar (M01); no calendar or a
    24x7 calendar = unchanged. The DUE date itself is never shifted, only the planned window."""
    from apps.sites.models import OperatingCalendar

    cal = OperatingCalendar.objects.for_organization(site.organization).filter(site=site, is_default=True).first()
    if cal is None or cal.is_24x7:
        return day
    holidays = set(cal.holidays.values_list("date", flat=True))
    days = set(cal.working_days or [])
    for _ in range(14):
        if (not days or day.isoweekday() in days) and day not in holidays:
            return day
        day += timedelta(days=1)
    return day


def window_for(schedule: MaintenanceSchedule, day: date) -> tuple[datetime, datetime]:
    site = schedule.plan.site
    start_day = _working_day(site, day)
    start = datetime.combine(start_day, schedule.window_start_time, tzinfo=site_zone(site))
    return start, start + timedelta(hours=schedule.window_hours)


# --- validation helpers ----------------------------------------------------------------------------------------------


def _name(value, what="Name"):
    value = (value or "").strip()
    if len(value) < 3:
        raise ValidationFailed(f"{what} must have at least 3 characters.", code="name_required")
    return value[:150]


def _decimal(value, what, *, positive=True, allow_none=False):
    if value in (None, ""):
        if allow_none:
            return None
        raise ValidationFailed(f"{what} is required.", code="invalid_number")
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationFailed(f"{what} must be a number.", code="invalid_number") from exc
    if not d.is_finite() or d < 0 or (positive and d <= 0) or d >= Decimal("1e12") or d.as_tuple().exponent < -3:
        raise ValidationFailed(f"{what} must be a positive number (max 3 decimals).", code="invalid_number")
    return d


def _check_checklist(org, key: str) -> str:
    key = (key or "").strip()
    if not key:
        return ""
    import uuid

    from apps.checklists.models import ChecklistTemplate

    try:
        uuid.UUID(key)
    except ValueError as exc:
        raise ValidationFailed("No checklist with this key exists in your organization.",
                               code="unknown_checklist") from exc
    if not ChecklistTemplate.objects.for_organization(org).filter(key=key).exists():
        raise ValidationFailed("No checklist with this key exists in your organization.", code="unknown_checklist")
    return key


def _check_asset(org, asset):
    if asset.organization_id != org.pk:
        raise ValidationFailed("Asset belongs to a different organization.", code="cross_tenant_asset")
    if asset.status in TERMINAL_ASSET:
        raise Conflict("Retired or disposed assets cannot have maintenance plans.", code="asset_terminal")


# --- plans -----------------------------------------------------------------------------------------------------------


@transaction.atomic
def create_plan(org, *, asset, name, description="", priority="MEDIUM", estimated_hours=None, checklist_key="",
                actor, request=None) -> MaintenancePlan:
    _check_asset(org, asset)
    name = _name(name)
    if priority not in PRIORITIES:
        raise ValidationFailed("Unknown priority.", code="invalid_priority")
    if MaintenancePlan.objects.for_organization(org).filter(asset=asset, name__iexact=name).exists():
        raise Conflict("This asset already has a plan with that name.", code="plan_name_taken")
    plan = MaintenancePlan(
        organization=org, asset=asset, site=asset.site, name=name, description=(description or "").strip(),
        priority=priority, estimated_hours=_decimal(estimated_hours, "Estimated hours", allow_none=True),
        checklist_key=_check_checklist(org, checklist_key), created_by=actor)
    plan.save()
    audit.record("maintenance.plan_created", actor=actor, organization=org, target=plan,
                 after={**audit.snapshot(plan, PLAN_FIELDS), "asset": asset.asset_tag}, request=request)
    return plan


@transaction.atomic
def update_plan(plan: MaintenancePlan, *, actor, request=None, **changes) -> MaintenancePlan:
    plan = MaintenancePlan.objects.select_for_update(of=("self",)).select_related("asset").get(pk=plan.pk)
    unknown = set(changes) - {"name", "description", "priority", "estimated_hours", "checklist_key"}
    if unknown:
        raise ValidationFailed(f"Fields cannot be edited: {', '.join(sorted(unknown))}.", code="field_not_editable")
    before = audit.snapshot(plan, PLAN_FIELDS)
    if "name" in changes:
        name = _name(changes["name"])
        if MaintenancePlan.objects.for_organization(plan.organization).filter(
                asset=plan.asset, name__iexact=name).exclude(pk=plan.pk).exists():
            raise Conflict("This asset already has a plan with that name.", code="plan_name_taken")
        plan.name = name
    if "description" in changes:
        plan.description = (changes["description"] or "").strip()
    if "priority" in changes:
        if changes["priority"] not in PRIORITIES:
            raise ValidationFailed("Unknown priority.", code="invalid_priority")
        plan.priority = changes["priority"]
    if "estimated_hours" in changes:
        plan.estimated_hours = _decimal(changes["estimated_hours"], "Estimated hours", allow_none=True)
    if "checklist_key" in changes:
        plan.checklist_key = _check_checklist(plan.organization, changes["checklist_key"])
    plan.save()
    after = audit.snapshot(plan, PLAN_FIELDS)
    if after != before:
        audit.record("maintenance.plan_updated", actor=actor, organization=plan.organization, target=plan,
                     before=before, after=after, request=request)
    return plan


@transaction.atomic
def set_plan_active(plan: MaintenancePlan, active: bool, *, actor, request=None) -> MaintenancePlan:
    plan = MaintenancePlan.objects.select_for_update(of=("self",)).select_related("asset", "site").get(pk=plan.pk)
    if plan.is_active == active:
        return plan
    if active:
        _check_asset(plan.organization, plan.asset)
    plan.is_active = active
    plan.save(update_fields=["is_active", "updated_at"])
    if active:  # re-enabled: resume at the next valid occurrence, never replay what was missed while disabled
        for sch in MaintenanceSchedule.objects.for_organization(plan.organization).select_for_update().filter(
                plan=plan, is_active=True):
            sch.plan = plan
            _resync(sch, timezone.now())
            sch.save()
    audit.record("maintenance.plan_enabled" if active else "maintenance.plan_disabled", actor=actor,
                 organization=plan.organization, target=plan, before={"is_active": not active},
                 after={"is_active": active}, request=request)
    return plan


# --- schedules -------------------------------------------------------------------------------------------------------


def latest_reading(meter):
    return meter.readings.order_by("-read_at", "-created_at").first()


def _resync(sch: MaintenanceSchedule, now: datetime) -> dict:
    """Points the schedule at its next occurrence that is not already in the past (no backlog is generated) AND that
    has not been generated yet: an occurrence that already has its cycle / work order is never offered again, and its
    sequence number is never reused (a recurrence edit re-indexes the new cadence above every existing cycle, see
    ``sequence_offset``). Returns what the resync resolved, for the audit trail."""
    cycles = []
    if not sch._state.adding:
        cycles = list(MaintenanceCycle.objects.for_organization(sch.organization).filter(schedule=sch).values_list(
            "sequence", "due_date", "due_value"))
    max_seq = max((c[0] for c in cycles), default=-1)
    covered = 0
    if sch.trigger_type == Trigger.TIME:
        k = rec.first_time_sequence_from(sch.start_date, sch.frequency, sch.interval_count,
                                         site_today(sch.plan.site, now))
        last_due = max((c[1] for c in cycles if c[1] is not None), default=None)
        while last_due is not None and rec.time_due_date(sch.start_date, sch.frequency, sch.interval_count,
                                                         k) <= last_due:
            k += 1  # that date already has its work order
            covered += 1
    else:
        reading = latest_reading(sch.meter)
        current = reading.value if reading else sch.start_value
        k = rec.first_meter_sequence_after(sch.start_value, sch.interval_value, current)
        last_due = max((c[2] for c in cycles if c[2] is not None), default=None)
        while last_due is not None and rec.meter_threshold(sch.start_value, sch.interval_value, k) <= last_due:
            k += 1
            covered += 1
    rebased = sch.sequence_offset + k <= max_seq
    if rebased:
        sch.sequence_offset = max_seq + 1 - k
    sch.next_sequence = sch.sequence_offset + k
    _set_next_display(sch)
    return {"next_sequence": sch.next_sequence, "sequence_offset": sch.sequence_offset,
            "already_generated_occurrences_skipped": covered, "reindexed": rebased,
            "highest_existing_sequence": max_seq if max_seq >= 0 else None}


def _set_next_display(sch: MaintenanceSchedule) -> None:
    local = sch.next_sequence - sch.sequence_offset  # index within the current recurrence
    if sch.trigger_type == Trigger.TIME:
        sch.next_due_date = rec.time_due_date(sch.start_date, sch.frequency, sch.interval_count, local)
        sch.next_due_value = None
    else:
        sch.next_due_value = rec.meter_threshold(sch.start_value, sch.interval_value, local)
        sch.next_due_date = None


def _validate_schedule(plan: MaintenancePlan, data: dict) -> dict:
    kind = data.get("trigger_type")
    out = {"trigger_type": kind, "frequency": "", "interval_count": None, "start_date": None, "meter": None,
           "interval_value": None, "start_value": Decimal("0")}
    if kind == Trigger.TIME:
        if data.get("frequency") not in MaintenanceSchedule.Frequency.values:
            raise ValidationFailed("Choose a frequency.", code="invalid_frequency")
        try:
            count = int(data.get("interval_count"))
        except (TypeError, ValueError) as exc:
            raise ValidationFailed("The interval must be a whole number.", code="invalid_interval") from exc
        if not 1 <= count <= 1000:
            raise ValidationFailed("The interval must be between 1 and 1000.", code="invalid_interval")
        start = data.get("start_date")
        if not isinstance(start, date) or isinstance(start, datetime):
            raise ValidationFailed("A start date is required.", code="start_date_required")
        out.update(frequency=data["frequency"], interval_count=count, start_date=start)
    elif kind == Trigger.METER:
        meter = data.get("meter")
        if meter is None or meter.organization_id != plan.organization_id:
            raise ValidationFailed("Meter not found in this organization.", code="cross_tenant_meter")
        if meter.asset_id != plan.asset_id:
            raise ValidationFailed("The meter belongs to a different asset than the plan.", code="meter_asset_mismatch")
        if not meter.is_active:
            raise Conflict("The meter is inactive.", code="meter_inactive")
        out.update(meter=meter, interval_value=_decimal(data.get("interval_value"), "Interval"),
                   start_value=_decimal(data.get("start_value", 0) or 0, "Start value", positive=False))
    else:
        raise ValidationFailed("Choose time-based or meter-based.", code="invalid_trigger")
    for field, low, high in (("lead_days", 0, 60), ("window_hours", 1, 72), ("reminder_days", 0, 60)):
        default = {"lead_days": 0, "window_hours": 8, "reminder_days": 0}[field]
        try:
            value = int(data.get(field, default) if data.get(field) not in (None, "") else default)
        except (TypeError, ValueError) as exc:
            raise ValidationFailed(f"{field.replace('_', ' ').capitalize()} must be a whole number.",
                                   code="invalid_window") from exc
        if not low <= value <= high:
            raise ValidationFailed(f"{field.replace('_', ' ').capitalize()} must be between {low} and {high}.",
                                   code="invalid_window")
        out[field] = value
    start_time = data.get("window_start_time") or time(8, 0)
    if not isinstance(start_time, time):
        raise ValidationFailed("Window start time is invalid.", code="invalid_window")
    out["window_start_time"] = start_time
    return out


@transaction.atomic
def create_schedule(plan: MaintenancePlan, *, actor, request=None, **data) -> MaintenanceSchedule:
    plan = MaintenancePlan.objects.select_for_update(of=("self",)).select_related("asset", "site").get(pk=plan.pk)
    _check_asset(plan.organization, plan.asset)
    fields = _validate_schedule(plan, data)
    sch = MaintenanceSchedule(organization=plan.organization, plan=plan, **fields)
    try:
        with transaction.atomic():
            _resync(sch, timezone.now())
            sch.save()
    except IntegrityError as exc:
        raise Conflict("The plan already has an identical schedule.", code="duplicate_schedule") from exc
    audit.record("maintenance.schedule_created", actor=actor, organization=plan.organization, target=sch,
                 after={**audit.snapshot(sch, SCHEDULE_FIELDS), "plan": plan.name}, request=request)
    return sch


@transaction.atomic
def update_schedule(sch: MaintenanceSchedule, *, actor, request=None, **data) -> MaintenanceSchedule:
    """Edits the recurrence and windows. Changing the recurrence restarts it at the next occurrence that is not in
    the past (cycles already generated are history and stay as they are)."""
    sch = MaintenanceSchedule.objects.select_for_update(of=("self",)).select_related("plan__site", "plan__asset", "meter").get(
        pk=sch.pk)
    before = audit.snapshot(sch, SCHEDULE_FIELDS)
    merged = {f: getattr(sch, f) for f in ("trigger_type", "frequency", "interval_count", "start_date", "meter",
                                           "interval_value", "start_value", "lead_days", "window_start_time",
                                           "window_hours", "reminder_days")}
    merged.update({k: v for k, v in data.items() if k != "trigger_type"})
    fields = _validate_schedule(sch.plan, merged)
    recurrence_changed = any(getattr(sch, f) != fields[f] for f in (
        "frequency", "interval_count", "start_date", "meter", "interval_value", "start_value"))
    for f, v in fields.items():
        setattr(sch, f, v)
    resolution = None
    try:
        with transaction.atomic():
            if recurrence_changed:
                resolution = _resync(sch, timezone.now())
            sch.save()
    except IntegrityError as exc:
        raise Conflict("The plan already has an identical schedule.", code="duplicate_schedule") from exc
    after = audit.snapshot(sch, SCHEDULE_FIELDS)
    if after != before or resolution:
        audit.record("maintenance.schedule_updated", actor=actor, organization=sch.organization, target=sch,
                     before=before, after=after, metadata={"resync": resolution} if resolution else None,
                     request=request)
    return sch


@transaction.atomic
def set_schedule_active(sch: MaintenanceSchedule, active: bool, *, actor, request=None) -> MaintenanceSchedule:
    sch = MaintenanceSchedule.objects.select_for_update(of=("self",)).select_related("plan__site", "plan__asset", "meter").get(
        pk=sch.pk)
    if sch.is_active == active:
        return sch
    resolution = None
    if active:
        _check_asset(sch.organization, sch.plan.asset)
        if sch.trigger_type == Trigger.METER and not sch.meter.is_active:
            raise Conflict("The meter is inactive.", code="meter_inactive")
        resolution = _resync(sch, timezone.now())
    sch.is_active = active
    sch.save()
    audit.record("maintenance.schedule_enabled" if active else "maintenance.schedule_disabled", actor=actor,
                 organization=sch.organization, target=sch, before={"is_active": not active},
                 after={"is_active": active, "next_sequence": sch.next_sequence},
                 metadata={"resync": resolution} if resolution else None, request=request)
    return sch


# --- due evaluation and generation ----------------------------------------------------------------------------------


def blocked_reason(sch: MaintenanceSchedule) -> str:
    """Why a schedule must not generate right now ('' = it may)."""
    plan = sch.plan
    if sch.plan.organization.status != "ACTIVE":
        return "organization_suspended"
    if not plan.is_active:
        return "plan_disabled"
    if not sch.is_active:
        return "schedule_disabled"
    if plan.asset.status in TERMINAL_ASSET:
        return "asset_terminal"
    if sch.trigger_type == Trigger.METER and not sch.meter.is_active:
        return "meter_inactive"
    return ""


def evaluate(sch: MaintenanceSchedule, now: datetime | None = None) -> rec.Due | None:
    """The occurrence to generate now (the latest one that is due), or None. Pure read."""
    now = now or timezone.now()
    if sch.trigger_type == Trigger.TIME:
        horizon = site_today(sch.plan.site, now) + timedelta(days=sch.lead_days)
        k = rec.latest_time_sequence(sch.start_date, sch.frequency, sch.interval_count, horizon)  # index in the recurrence
        seq = sch.sequence_offset + k
        if k < 0 or seq < sch.next_sequence:
            return None
        return rec.Due(seq, seq - sch.next_sequence,
                       due_date=rec.time_due_date(sch.start_date, sch.frequency, sch.interval_count, k))
    reading = latest_reading(sch.meter)
    if reading is None:
        return None
    k = rec.latest_meter_sequence(sch.start_value, sch.interval_value, reading.value)  # index in the recurrence
    seq = sch.sequence_offset + k
    if k < 1 or seq < sch.next_sequence:
        return None
    return rec.Due(seq, seq - sch.next_sequence, due_value=rec.meter_threshold(sch.start_value, sch.interval_value, k))


def required_checklist_key(wo) -> str:
    """M08 contract: the checklist key named by the PM plan that generated ``wo`` ('' when none)."""
    key = MaintenanceCycle.objects.for_organization(wo.organization).filter(work_order=wo).values_list(
        "schedule__plan__checklist_key", flat=True).first()
    return key or ""


def _planners(org, site):
    from apps.tenancy.models import Membership

    members = Membership.objects.for_organization(org).filter(status=Membership.Status.ACTIVE).select_related("user")
    return [m.user for m in members if rbac.has_permission(m, "work_order.assign", site.pk)]


def _title(sch: MaintenanceSchedule, due: rec.Due) -> str:
    return f"PM: {sch.plan.name} (cycle {due.sequence})"[:200]


def _description(sch: MaintenanceSchedule, due: rec.Due) -> str:
    lines = [f"Preventive maintenance generated from plan '{sch.plan.name}' ({sch.describe()})."]
    if due.due_date:
        lines.append(f"Due date: {due.due_date.isoformat()}.")
    if due.due_value is not None:
        lines.append(f"Meter threshold reached: {num(due.due_value)} {sch.meter.unit}.")
    if due.skipped:
        lines.append(f"{due.skipped} earlier occurrence(s) were missed and are covered by this order.")
    if sch.plan.checklist_key:
        from apps.checklists.models import ChecklistTemplate

        template = ChecklistTemplate.objects.for_organization(sch.organization).filter(
            key=sch.plan.checklist_key).order_by("-version").first()
        lines.append(f"Required checklist: {template.name if template else sch.plan.checklist_key}.")
    if sch.plan.description:
        lines.append(sch.plan.description)
    return "\n".join(lines)


@transaction.atomic
def generate_cycle(schedule: MaintenanceSchedule, *, actor=None, now: datetime | None = None, manual: bool = False,
                   request=None) -> MaintenanceCycle | None:
    """Generates the due occurrence of one schedule as a real M06 work order, exactly once.

    Returns the new cycle, or None when nothing is due / the schedule is disabled / another worker already did it.
    ``manual`` (a user pressing "Generate now") may generate the NEXT occurrence ahead of its date."""
    from apps.workorders import services as wos

    now = now or timezone.now()
    sch = MaintenanceSchedule.objects.select_for_update(of=("self",)).select_related(
        "plan__asset__site", "plan__site", "plan__organization", "meter").get(pk=schedule.pk)
    reason = blocked_reason(sch)
    if reason:
        if manual:
            raise Conflict(f"This schedule cannot generate work now ({reason.replace('_', ' ')}).", code=reason)
        return None
    sch.last_run_at = now
    due = evaluate(sch, now)
    if due is None and manual:
        if MaintenanceCycle.objects.for_organization(sch.organization).filter(schedule=sch).exclude(
                work_order__status__in=("CLOSED", "CANCELLED")).exists():
            raise Conflict("The previous cycle's work order is still open: finish it before generating the next "
                           "occurrence early.", code="previous_cycle_open")
        if sch.trigger_type == Trigger.TIME:
            due = rec.Due(sch.next_sequence, 0, due_date=sch.next_due_date)
        else:
            due = rec.Due(sch.next_sequence, 0, due_value=sch.next_due_value)
    if due is None:
        sch.save(update_fields=["last_run_at", "updated_at"])
        return None
    plan = sch.plan
    cycle = MaintenanceCycle(organization=sch.organization, schedule=sch, sequence=due.sequence,
                             due_date=due.due_date, due_value=due.due_value, skipped=due.skipped,
                             trigger="manual" if manual else "scheduler", generated_by=actor)
    try:
        with transaction.atomic():
            cycle.save()
    except IntegrityError:
        # Defence in depth (a concurrent writer / inconsistent data): the database says this occurrence already has
        # its cycle. Nothing is created, but the resolution is never silent: it is recorded on the schedule and audited.
        sch.next_sequence = max(sch.next_sequence, due.sequence + 1)
        _set_next_display(sch)
        sch.last_error = f"Occurrence {due.sequence} already had a cycle; the schedule advanced to {sch.next_sequence}."
        sch.save()
        audit.record("maintenance.cycle_collision", actor=actor, organization=sch.organization, target=sch,
                     metadata={"sequence": due.sequence, "next_sequence": sch.next_sequence,
                               "trigger": "manual" if manual else "scheduler"}, request=request)
        return None
    day = due.due_date or site_today(plan.site, now)
    start, end = window_for(sch, day)
    est_hours = plan.estimated_hours if plan.estimated_hours is not None else Decimal(str(sch.window_hours))
    due_at = end + timedelta(days=1)
    wo = wos.create_work_order(
        sch.organization, asset=plan.asset, actor=actor, title=_title(sch, due), description=_description(sch, due),
        work_type="PREVENTIVE", priority=plan.priority, planned_start=start, planned_end=end, due_at=due_at,
        estimated_hours=est_hours, source_type="PREVENTIVE_MAINTENANCE", source_id=cycle.pk,
        source_reference=f"PM:{sch.pk}:{due.sequence}",
        request=request)
    wo = wos.transition(wo, action="plan", actor=actor, membership=None, request=request)  # window is set: PLANNED
    cycle.work_order = wo
    cycle.save(update_fields=["work_order", "updated_at"])
    sch.next_sequence = due.sequence + 1
    sch.last_error = ""
    _set_next_display(sch)
    sch.save()
    audit.record("maintenance.cycle_generated", actor=actor, organization=sch.organization, target=cycle,
                 after={"plan": plan.name, "schedule": sch.describe(), "sequence": due.sequence,
                        "skipped": due.skipped, "work_order": wo.number, "trigger": cycle.trigger},
                 request=request)
    from apps.notifications import services as notifications

    notifications.notify(sch.organization, _planners(sch.organization, plan.site), title=f"{wo.number} generated: {plan.name}",
                         body=f"Preventive maintenance on {plan.asset.asset_tag} is ready to assign.",
                         link=f"/app/work-orders/{wo.pk}/", source="maintenance")
    return cycle


@transaction.atomic
def remind(schedule: MaintenanceSchedule, *, now: datetime | None = None) -> bool:
    """Reminds the planners once per occurrence when it is within ``reminder_days`` and not generated yet."""
    now = now or timezone.now()
    sch = MaintenanceSchedule.objects.select_for_update(of=("self",)).select_related(
        "plan__asset", "plan__site", "plan__organization", "meter").get(pk=schedule.pk)
    if sch.reminder_days <= 0 or blocked_reason(sch) or sch.last_reminded_sequence == sch.next_sequence:
        return False
    if sch.trigger_type == Trigger.TIME:
        if sch.next_due_date - site_today(sch.plan.site, now) > timedelta(days=sch.reminder_days):
            return False
        text = f"due {sch.next_due_date.isoformat()}"
    else:
        reading = latest_reading(sch.meter)
        if reading is None or sch.next_due_value - reading.value > sch.interval_value * Decimal("0.1"):
            return False  # meter schedules remind within 10% of an interval before the threshold
        text = f"due at {num(sch.next_due_value)} {sch.meter.unit}"
    sch.last_reminded_sequence = sch.next_sequence
    sch.save(update_fields=["last_reminded_sequence", "updated_at"])
    from apps.notifications import services as notifications

    notifications.notify(sch.organization, _planners(sch.organization, sch.plan.site),
                         title=f"Maintenance reminder: {sch.plan.name}",
                         body=f"{sch.plan.asset.asset_tag} {text} ({sch.describe()}).",
                         link=f"/app/maintenance/plans/{sch.plan_id}/", source="maintenance")
    audit.record("maintenance.reminder_sent", organization=sch.organization, target=sch,
                 metadata={"sequence": sch.next_sequence}, request=None)
    return True


def due_schedule_ids(org, now: datetime | None = None) -> list:
    """Active schedules of enabled plans that currently have a due occurrence (a cheap read for the task)."""
    now = now or timezone.now()
    qs = MaintenanceSchedule.objects.for_organization(org).filter(is_active=True, plan__is_active=True).select_related(
        "plan__site", "plan__asset", "plan__organization", "meter")
    return [s.pk for s in qs if not blocked_reason(s) and evaluate(s, now) is not None]


def run_for_organization(org, now: datetime | None = None) -> dict:
    """The scheduler's unit of work: reminders, then every due schedule in its own transaction. A failing schedule
    is recorded (``last_error``) and does not stop the others."""
    from apps.core.tenant import tenant_context

    now = now or timezone.now()
    result = {"generated": 0, "errors": 0, "reminders": 0}
    with tenant_context(org):
        for sch in list(MaintenanceSchedule.objects.for_organization(org).filter(
                is_active=True, plan__is_active=True, reminder_days__gt=0)):
            try:
                result["reminders"] += int(remind(sch, now=now))
            except Exception:  # noqa: BLE001 - reported, never fatal for the batch
                log.exception("PM reminder failed for schedule %s", sch.pk)
        for pk in due_schedule_ids(org, now):
            sch = MaintenanceSchedule.objects.get(pk=pk)
            try:
                if generate_cycle(sch, now=now) is not None:
                    result["generated"] += 1
            except Exception as exc:  # noqa: BLE001
                result["errors"] += 1
                log.exception("PM generation failed for schedule %s", pk)
                MaintenanceSchedule.objects.filter(pk=pk).update(last_error=str(exc)[:300], last_run_at=now)
    return result
