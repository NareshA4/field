"""M04 Preventive Maintenance models.

A ``MaintenancePlan`` (what is maintained and how) owns one or more ``MaintenanceSchedule`` rows (when: time-based
or meter-based). Every generated work order is recorded as a ``MaintenanceCycle``; ``(schedule, sequence)`` is
UNIQUE, so "one cycle = one work order" is guaranteed by the database whatever the scheduler does. The work-order
lifecycle itself stays in M06: the PM-side state of a cycle is derived from the work order, never stored twice.
"""
import datetime

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

from apps.core.models import TenantOwnedModel


def num(value) -> str:
    """500.000 -> '500', 12.50 -> '12.5' (plain decimal text, no exponent)."""
    return format(value.normalize(), "f") if value is not None else ""


class MaintenancePlan(TenantOwnedModel):
    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT, related_name="maintenance_plans")
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="maintenance_plans")
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=10, default="MEDIUM")  # a WorkOrder.Priority value
    estimated_hours = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    # M08 association: ``ChecklistTemplate.key`` of the checklist the generated work orders must complete
    checklist_key = models.CharField(max_length=60, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), "asset", name="uniq_plan_name_per_asset")]
        indexes = [models.Index(fields=["organization", "site"]), models.Index(fields=["organization", "is_active"])]

    def __str__(self):
        return self.name


class MaintenanceSchedule(TenantOwnedModel):
    class Trigger(models.TextChoices):
        TIME = "TIME", "Time-based"
        METER = "METER", "Meter-based"

    class Frequency(models.TextChoices):
        DAILY = "DAILY", "Day(s)"
        WEEKLY = "WEEKLY", "Week(s)"
        MONTHLY = "MONTHLY", "Month(s)"
        QUARTERLY = "QUARTERLY", "Quarter(s)"
        YEARLY = "YEARLY", "Year(s)"

    plan = models.ForeignKey(MaintenancePlan, on_delete=models.PROTECT, related_name="schedules")
    trigger_type = models.CharField(max_length=6, choices=Trigger.choices)
    # time-based: due on start_date + k * (interval_count x frequency), k = 0, 1, 2 ...
    frequency = models.CharField(max_length=10, choices=Frequency.choices, blank=True)
    interval_count = models.PositiveIntegerField(null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)  # a calendar date in the site's timezone
    # meter-based: due when the meter reaches start_value + k * interval_value, k = 1, 2, 3 ...
    meter = models.ForeignKey("assets.AssetMeter", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="maintenance_schedules")
    interval_value = models.DecimalField(max_digits=16, decimal_places=3, null=True, blank=True)
    start_value = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    # generation window / maintenance window / reminders
    lead_days = models.PositiveSmallIntegerField(default=0, help_text="Generate the work order this many days early")
    window_start_time = models.TimeField(default=datetime.time(8, 0))
    window_hours = models.PositiveSmallIntegerField(default=8, help_text="Length of the maintenance window")
    reminder_days = models.PositiveSmallIntegerField(default=0, help_text="Remind planners this many days before due")
    # generation state
    is_active = models.BooleanField(default=True)
    next_sequence = models.PositiveIntegerField(default=0)
    # Occurrence numbers (``next_sequence``, ``MaintenanceCycle.sequence``) are never reused. When the recurrence is
    # edited the new cadence is re-indexed above everything already generated: the k-th occurrence of the CURRENT
    # recurrence has the sequence ``sequence_offset + k`` (offset 0 until the first re-index).
    sequence_offset = models.PositiveIntegerField(default=0)
    next_due_date = models.DateField(null=True, blank=True)
    next_due_value = models.DecimalField(max_digits=16, decimal_places=3, null=True, blank=True)
    last_reminded_sequence = models.IntegerField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["plan__name", "created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(trigger_type="TIME", frequency__in=["DAILY", "WEEKLY", "MONTHLY", "QUARTERLY",
                                                                       "YEARLY"], interval_count__gte=1,
                                   start_date__isnull=False, meter__isnull=True, interval_value__isnull=True)
                | models.Q(trigger_type="METER", meter__isnull=False, interval_value__gt=0, frequency="",
                           interval_count__isnull=True, start_date__isnull=True),
                name="schedule_trigger_fields"),
            models.CheckConstraint(
                condition=models.Q(lead_days__lte=60, window_hours__gte=1, window_hours__lte=72,
                                   reminder_days__lte=60), name="schedule_window_ranges"),
            models.UniqueConstraint(
                fields=["plan", "frequency", "interval_count"], condition=models.Q(trigger_type="TIME"),
                name="uniq_time_schedule_per_plan"),
            models.UniqueConstraint(
                fields=["plan", "meter", "interval_value"], condition=models.Q(trigger_type="METER"),
                name="uniq_meter_schedule_per_plan"),
        ]
        indexes = [models.Index(fields=["organization", "is_active", "next_due_date"])]

    def __str__(self):
        return f"{self.plan.name}: {self.describe()}"

    def describe(self) -> str:
        if self.trigger_type == self.Trigger.TIME:
            unit = self.get_frequency_display().replace("(s)", "" if self.interval_count == 1 else "s")
            return f"every {self.interval_count} {unit.lower()}"
        return f"every {num(self.interval_value)} {self.meter.unit} ({self.meter.name})"


class MaintenanceCycle(TenantOwnedModel):
    """One generated occurrence of a schedule. ``sequence`` identifies the occurrence (time: k-th date, meter:
    k-th threshold); ``skipped`` counts the older occurrences that were collapsed into this one (D-043)."""

    schedule = models.ForeignKey(MaintenanceSchedule, on_delete=models.PROTECT, related_name="cycles")
    sequence = models.PositiveIntegerField()
    due_date = models.DateField(null=True, blank=True)
    due_value = models.DecimalField(max_digits=16, decimal_places=3, null=True, blank=True)
    skipped = models.PositiveIntegerField(default=0)
    work_order = models.OneToOneField("workorders.WorkOrder", null=True, on_delete=models.PROTECT,
                                      related_name="maintenance_cycle")
    trigger = models.CharField(max_length=10, default="scheduler")  # scheduler | manual
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["schedule", "sequence"], name="uniq_cycle_per_schedule_sequence")]
        indexes = [models.Index(fields=["organization", "schedule"])]

    def __str__(self):
        return f"{self.schedule} #{self.sequence}"
