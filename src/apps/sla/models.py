"""M11 SLA & Escalation models (D-044).

A ``SLAProfile`` (per organization, optionally per site) holds one ``SLATarget`` per priority (response and
resolution are SEPARATE minutes) and a few ``EscalationRule`` rows. When a service request (M05) or a work order
(M06) is created, an ``SLATracking`` row is started from the persisted ``created_at`` with absolute due times. Warnings,
breaches and escalations are persisted as ``SLAEvent`` (append-only, unique per dedupe key) and ``SLABreach`` (unique
per tracking and target), so a monitor that runs twice cannot duplicate anything.
"""
from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

from apps.core.models import TenantOwnedModel

REQUEST, WORK_ORDER = "REQUEST", "WORK_ORDER"
RESPONSE, RESOLUTION = "RESPONSE", "RESOLUTION"


class SLAProfile(TenantOwnedModel):
    class AppliesTo(models.TextChoices):
        REQUEST = "REQUEST", "Service requests / incidents (M05)"
        WORK_ORDER = "WORK_ORDER", "Work orders (M06)"

    name = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True)
    applies_to = models.CharField(max_length=12, choices=AppliesTo.choices)
    site = models.ForeignKey("sites.Site", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                             help_text="Blank = the whole organization; a site profile wins over it")
    work_type = models.CharField(max_length=20, blank=True, help_text="Work-order profiles: only this work type")
    # tokens such as WORK_ORDER:ON_HOLD or REQUEST:RESOLVED; empty = the timers never pause (D-044)
    pause_states = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    coverage_only = models.BooleanField(
        default=False,
        help_text="Used only through a warranty / AMC / contract that names it (M10); never resolved by scope.")

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), "organization", name="uniq_sla_profile_name_per_org"),
            # at most one ACTIVE profile per scope, so the profile of a request / work order is unambiguous
            models.UniqueConstraint(
                fields=["organization", "applies_to", "work_type"],
                condition=models.Q(is_active=True, site__isnull=True, coverage_only=False),
                name="uniq_active_sla_profile_org_scope"),
            models.UniqueConstraint(
                fields=["organization", "applies_to", "work_type", "site"],
                condition=models.Q(is_active=True, site__isnull=False, coverage_only=False),
                name="uniq_active_sla_profile_site_scope"),
        ]

    def __str__(self):
        return self.name


class SLATarget(TenantOwnedModel):
    profile = models.ForeignKey(SLAProfile, on_delete=models.CASCADE, related_name="targets")
    priority = models.CharField(max_length=10)  # request severity or work-order priority, per profile.applies_to
    response_minutes = models.PositiveIntegerField()
    resolution_minutes = models.PositiveIntegerField()
    warning_percent = models.PositiveSmallIntegerField(default=80)

    class Meta:
        ordering = ["profile__name", "priority"]
        constraints = [
            models.UniqueConstraint(fields=["profile", "priority"], name="uniq_sla_target_per_priority"),
            models.CheckConstraint(condition=models.Q(response_minutes__gte=1), name="sla_response_minutes_pos"),
            models.CheckConstraint(
                condition=models.Q(resolution_minutes__gte=models.F("response_minutes")),
                name="sla_resolution_gte_response"),
            models.CheckConstraint(condition=models.Q(warning_percent__gte=1, warning_percent__lte=99),
                                   name="sla_warning_percent_range"),
        ]

    def __str__(self):
        return f"{self.profile.name} / {self.priority}"


class EscalationRule(TenantOwnedModel):
    """One configured notification step. WARNING and BREACH fire when the target reaches that point;
    ESCALATION fires ``after_minutes`` after the breach if the target is still unmet. Each rule fires at most once
    per tracking (no chains beyond the rules a profile defines; at most 6 per profile)."""

    class Trigger(models.TextChoices):
        WARNING = "WARNING", "Warning threshold reached"
        BREACH = "BREACH", "Target breached"
        ESCALATION = "ESCALATION", "Still unmet after the breach"

    profile = models.ForeignKey(SLAProfile, on_delete=models.CASCADE, related_name="rules")
    target_kind = models.CharField(max_length=10, choices=[(RESPONSE, "Response"), (RESOLUTION, "Resolution")])
    trigger = models.CharField(max_length=10, choices=Trigger.choices)
    after_minutes = models.PositiveIntegerField(default=0)
    level = models.PositiveSmallIntegerField(default=1)
    notify_role = models.ForeignKey("rbac.Role", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    notify_assignee = models.BooleanField(default=False)

    class Meta:
        ordering = ["profile__name", "target_kind", "trigger", "after_minutes"]
        constraints = [
            models.UniqueConstraint(fields=["profile", "target_kind", "trigger", "after_minutes"],
                                    name="uniq_escalation_rule_step"),
            models.CheckConstraint(
                condition=models.Q(trigger="ESCALATION", after_minutes__gte=1)
                | (~models.Q(trigger="ESCALATION") & models.Q(after_minutes=0)), name="escalation_after_minutes"),
            models.CheckConstraint(condition=models.Q(level__gte=1, level__lte=3), name="escalation_level_range"),
            models.CheckConstraint(condition=models.Q(notify_role__isnull=False) | models.Q(notify_assignee=True),
                                   name="escalation_has_recipient"),
        ]

    def __str__(self):
        return f"{self.get_trigger_display()} ({self.target_kind.lower()})"


class SLATracking(TenantOwnedModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Running"
        PAUSED = "PAUSED", "Paused"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    class TargetState(models.TextChoices):
        PENDING = "PENDING", "Pending"
        MET = "MET", "Met"
        MET_LATE = "MET_LATE", "Met late"
        BREACHED = "BREACHED", "Breached"
        NOT_APPLICABLE = "NOT_APPLICABLE", "Not applicable"

    request = models.ForeignKey("incidents.ServiceRequest", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="sla_trackings")
    work_order = models.ForeignKey("workorders.WorkOrder", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="sla_trackings")
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="+")
    profile = models.ForeignKey(SLAProfile, on_delete=models.PROTECT, related_name="trackings")
    priority = models.CharField(max_length=10)
    response_minutes = models.PositiveIntegerField()
    resolution_minutes = models.PositiveIntegerField()
    warning_percent = models.PositiveSmallIntegerField()
    started_at = models.DateTimeField()
    response_due_at = models.DateTimeField()
    resolution_due_at = models.DateTimeField()
    response_met_at = models.DateTimeField(null=True, blank=True)
    resolution_met_at = models.DateTimeField(null=True, blank=True)
    response_state = models.CharField(max_length=14, choices=TargetState.choices, default=TargetState.PENDING)
    resolution_state = models.CharField(max_length=14, choices=TargetState.choices, default=TargetState.PENDING)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    paused_seconds = models.PositiveIntegerField(default=0)
    last_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(request__isnull=False) & models.Q(work_order__isnull=True))
                | (models.Q(request__isnull=True) & models.Q(work_order__isnull=False)), name="sla_one_subject"),
            models.UniqueConstraint(fields=["request"], condition=models.Q(request__isnull=False),
                                    name="uniq_sla_tracking_per_request"),
            models.UniqueConstraint(fields=["work_order"], condition=models.Q(work_order__isnull=False),
                                    name="uniq_sla_tracking_per_work_order"),
            models.CheckConstraint(condition=models.Q(status="PAUSED", paused_at__isnull=False)
                                   | (~models.Q(status="PAUSED") & models.Q(paused_at__isnull=True)),
                                   name="sla_paused_has_timestamp"),
        ]
        indexes = [models.Index(fields=["organization", "status"]), models.Index(fields=["organization", "site"]),
                   models.Index(fields=["organization", "priority"])]

    @property
    def subject(self):
        return self.request or self.work_order

    @property
    def subject_type(self) -> str:
        return REQUEST if self.request_id else WORK_ORDER

    def __str__(self):
        return f"SLA {self.subject}"


class SLAEvent(TenantOwnedModel):
    """Append-only SLA history. ``dedupe_key`` (unique per tracking when set) makes warnings / breaches /
    escalations fire exactly once however often the monitor runs."""

    tracking = models.ForeignKey(SLATracking, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=20)
    target_kind = models.CharField(max_length=10, blank=True)
    at = models.DateTimeField()
    detail = models.CharField(max_length=300, blank=True)
    rule = models.ForeignKey(EscalationRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    dedupe_key = models.CharField(max_length=80, blank=True)
    notified = models.PositiveSmallIntegerField(default=0)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")

    class Meta:
        ordering = ["at", "created_at"]
        constraints = [models.UniqueConstraint(fields=["tracking", "dedupe_key"], condition=~models.Q(dedupe_key=""),
                                               name="uniq_sla_event_dedupe")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("SLAEvent is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("SLAEvent is append-only.")


class SLABreach(TenantOwnedModel):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged"
        CLOSED = "CLOSED", "Closed"

    tracking = models.ForeignKey(SLATracking, on_delete=models.PROTECT, related_name="breaches")
    profile = models.ForeignKey(SLAProfile, on_delete=models.PROTECT, related_name="+")
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="+")
    target_kind = models.CharField(max_length=10, choices=[(RESPONSE, "Response"), (RESOLUTION, "Resolution")])
    priority = models.CharField(max_length=10)
    severity = models.CharField(max_length=10, default="MAJOR")
    breached_at = models.DateTimeField(help_text="The due instant that was missed")
    detected_at = models.DateTimeField()
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.OPEN, db_index=True)
    escalation_level = models.PositiveSmallIntegerField(default=0)
    escalated_at = models.DateTimeField(null=True, blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="+")
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_reason = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ["-breached_at"]
        constraints = [models.UniqueConstraint(fields=["tracking", "target_kind"], name="uniq_sla_breach_per_target")]
        indexes = [models.Index(fields=["organization", "status"]), models.Index(fields=["organization", "site"])]

    def __str__(self):
        return f"{self.target_kind.lower()} breach of {self.tracking}"
