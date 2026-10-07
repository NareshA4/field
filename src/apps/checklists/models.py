from django.conf import settings
from django.db import models

from apps.core.models import TenantOwnedModel

from .workflow import ACTIVE, COMPLETED, DRAFT, IN_PROGRESS, INSPECTION_STATES, TEMPLATE_STATES

TEMPLATE_STATUS_CHOICES = [(s, s.title()) for s in TEMPLATE_STATES]
INSPECTION_STATUS_CHOICES = [(s, s.replace("_", " ").title()) for s in INSPECTION_STATES]


class ChecklistTemplate(TenantOwnedModel):
    """A versioned checklist. ``key`` groups the versions of one checklist; at most one version per key is ACTIVE.
    Items of an ACTIVE or INACTIVE version are frozen (services refuse edits), so a historical inspection always
    shows the questions it was answered against."""

    key = models.UUIDField(editable=False, db_index=True)
    version = models.PositiveIntegerField(default=1)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=TEMPLATE_STATUS_CHOICES, default=DRAFT,
                              db_index=True)
    work_type = models.CharField(max_length=20, blank=True, help_text="Blank = any work type")
    is_required = models.BooleanField(default=False,
                                      help_text="Must be completed before matching work can be completed / closed")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "key", "version"], name="uniq_checklist_version"),
            models.UniqueConstraint(fields=["organization", "key"], condition=models.Q(status=ACTIVE),
                                    name="uniq_active_checklist_per_key"),
        ]
        indexes = [models.Index(fields=["organization", "status"])]

    def __str__(self):
        return f"{self.name} v{self.version}"


class ChecklistItem(TenantOwnedModel):
    class ItemType(models.TextChoices):
        TEXT = "TEXT", "Text"
        NUMERIC = "NUMERIC", "Number"
        BOOLEAN = "BOOLEAN", "Pass / fail"
        SELECTION = "SELECTION", "Selection"

    template = models.ForeignKey(ChecklistTemplate, on_delete=models.PROTECT, related_name="items")
    position = models.PositiveIntegerField()
    prompt = models.CharField(max_length=300)
    guidance = models.TextField(blank=True)
    item_type = models.CharField(max_length=10, choices=ItemType.choices)
    required = models.BooleanField(default=True)
    options = models.JSONField(default=list, blank=True)  # SELECTION: allowed values
    exception_options = models.JSONField(default=list, blank=True)  # SELECTION: values that need a finding
    min_value = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    max_value = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    unit = models.CharField(max_length=20, blank=True)
    evidence_required = models.BooleanField(default=False)

    class Meta:
        ordering = ["position", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["template", "position"], name="uniq_checklist_item_position",
                                    deferrable=models.Deferrable.DEFERRED),
            models.CheckConstraint(condition=models.Q(min_value__isnull=True) | models.Q(max_value__isnull=True)
                                   | models.Q(max_value__gte=models.F("min_value")), name="checklist_item_range"),
        ]

    def __str__(self):
        return self.prompt


class Inspection(TenantOwnedModel):
    """One execution of a template, normally for a work order. ``status`` changes only through services."""

    template = models.ForeignKey(ChecklistTemplate, on_delete=models.PROTECT, related_name="inspections")
    work_order = models.ForeignKey("workorders.WorkOrder", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="inspections")
    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT, related_name="inspections")
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="inspections")
    status = models.CharField(max_length=12, choices=INSPECTION_STATUS_CHOICES,
                              default=IN_PROGRESS, db_index=True)
    started_by = models.ForeignKey("tenancy.Membership", on_delete=models.PROTECT, related_name="+")
    started_at = models.DateTimeField(auto_now_add=True)
    completed_by = models.ForeignKey("tenancy.Membership", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+")
    completed_at = models.DateTimeField(null=True, blank=True)
    summary = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            # duplicate execution of the same checklist version on a work order is refused
            models.UniqueConstraint(fields=["work_order", "template"], condition=models.Q(work_order__isnull=False),
                                    name="uniq_inspection_per_work_order_template"),
            models.CheckConstraint(condition=~models.Q(status=COMPLETED) | models.Q(completed_at__isnull=False),
                                   name="inspection_completed_has_timestamp"),
        ]
        indexes = [models.Index(fields=["organization", "status"]), models.Index(fields=["organization", "site"]),
                   models.Index(fields=["organization", "work_order"])]

    def __str__(self):
        return f"{self.template} / {self.asset_id}"


class InspectionResponse(TenantOwnedModel):
    inspection = models.ForeignKey(Inspection, on_delete=models.PROTECT, related_name="responses")
    item = models.ForeignKey(ChecklistItem, on_delete=models.PROTECT, related_name="responses")
    value_text = models.TextField(blank=True)  # TEXT and SELECTION
    value_number = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    value_bool = models.BooleanField(null=True)  # BOOLEAN: True = pass
    is_exception = models.BooleanField(default=False)  # fail / out of range / exception option: needs a finding
    answered_by = models.ForeignKey("tenancy.Membership", on_delete=models.PROTECT, related_name="+")
    answered_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["inspection", "item"], name="uniq_response_per_item")]
        indexes = [models.Index(fields=["inspection"])]


class Finding(TenantOwnedModel):
    class Severity(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        RESOLVED = "RESOLVED", "Resolved"

    inspection = models.ForeignKey(Inspection, on_delete=models.PROTECT, related_name="findings")
    item = models.ForeignKey(ChecklistItem, null=True, blank=True, on_delete=models.PROTECT, related_name="findings")
    work_order = models.ForeignKey("workorders.WorkOrder", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="findings")
    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT, related_name="findings")
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="findings")
    description = models.TextField()
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.MEDIUM)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status"]), models.Index(fields=["organization", "site"])]
