from django.conf import settings
from django.db import models

from apps.core.models import TenantOwnedModel

from .workflow import CANCELLED, CLOSED, DRAFT, STATES


class WorkOrder(TenantOwnedModel):
    """M06 work order. ``status`` changes ONLY through ``workorders.services``."""

    class WorkType(models.TextChoices):
        CORRECTIVE = "CORRECTIVE", "Corrective (breakdown)"
        PREVENTIVE = "PREVENTIVE", "Preventive"
        INSPECTION = "INSPECTION", "Inspection"
        INSTALLATION = "INSTALLATION", "Installation / modification"
        OTHER = "OTHER", "Other"

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        URGENT = "URGENT", "Urgent"

    class SourceType(models.TextChoices):
        SERVICE_REQUEST = "SERVICE_REQUEST", "Service request"
        PREVENTIVE_MAINTENANCE = "PREVENTIVE_MAINTENANCE", "Preventive maintenance"

    Status = models.TextChoices("Status", {s: (s, s.replace("_", " ").title()) for s in STATES})

    number = models.CharField(max_length=20, editable=False)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    work_type = models.CharField(max_length=20, choices=WorkType.choices, default=WorkType.CORRECTIVE)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=DRAFT, db_index=True)
    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT, related_name="work_orders")
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="work_orders")
    source_request = models.ForeignKey(
        "incidents.ServiceRequest", null=True, blank=True, on_delete=models.PROTECT, related_name="work_orders")
    # Origin of the order when it was created by another module (D-043): SERVICE_REQUEST (M05, id = request) or
    # PREVENTIVE_MAINTENANCE (M04, id = the maintenance cycle). Blank / NULL = created directly in M06.
    source_type = models.CharField(max_length=30, blank=True, choices=SourceType.choices)
    source_id = models.UUIDField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    planned_start = models.DateTimeField(null=True, blank=True)
    planned_end = models.DateTimeField(null=True, blank=True)
    estimated_hours = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    assigned_to = models.ForeignKey(
        "tenancy.Membership", null=True, blank=True, on_delete=models.PROTECT, related_name="assigned_work_orders")
    dispatched_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    review_started_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    hold_reason = models.CharField(max_length=500, blank=True)
    resolution_notes = models.TextField(blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "number"], name="uniq_work_order_number_per_org"),
            # one live work order per request (a cancelled or closed one does not block rework)
            models.UniqueConstraint(
                fields=["source_request"], condition=models.Q(source_request__isnull=False)
                & ~models.Q(status__in=[CANCELLED, CLOSED]), name="uniq_live_work_order_per_request"),
            models.CheckConstraint(
                condition=(models.Q(source_type="") & models.Q(source_id__isnull=True))
                | (~models.Q(source_type="") & models.Q(source_id__isnull=False)), name="work_order_source_pair"),
            # one cycle = one work order, enforced by the database whatever the scheduler does
            models.UniqueConstraint(
                fields=["source_id"], condition=models.Q(source_type="PREVENTIVE_MAINTENANCE"),
                name="uniq_work_order_per_pm_source"),
            models.CheckConstraint(
                condition=models.Q(planned_start__isnull=True) | models.Q(planned_end__isnull=True)
                | models.Q(planned_end__gte=models.F("planned_start")), name="work_order_plan_window_ordered"),
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["organization", "site"]),
            models.Index(fields=["organization", "asset"]),
            models.Index(fields=["organization", "assigned_to", "status"]),
        ]

    def __str__(self):
        return f"{self.number} · {self.title}"


class _AppendOnly(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError(f"{type(self).__name__} is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(f"{type(self).__name__} is append-only.")


class WorkOrderEvent(_AppendOnly, TenantOwnedModel):
    """Append-only lifecycle log: transitions and (re)assignments."""

    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, related_name="events")
    action = models.CharField(max_length=30)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    reason = models.CharField(max_length=500, blank=True)
    assigned_to = models.ForeignKey("tenancy.Membership", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.action}: {self.from_status or 'new'} -> {self.to_status}"


class WorkOrderLabor(TenantOwnedModel):
    """Labor / time captured against a work order (hours per technician per day)."""

    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, related_name="labor")
    technician = models.ForeignKey("tenancy.Membership", on_delete=models.PROTECT, related_name="+")
    work_date = models.DateField()
    hours = models.DecimalField(max_digits=5, decimal_places=2)
    notes = models.CharField(max_length=300, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["work_date", "created_at"]
        constraints = [models.CheckConstraint(condition=models.Q(hours__gt=0, hours__lte=24),
                                              name="work_order_labor_hours_range")]


class WorkOrderMaterial(TenantOwnedModel):
    """Material used. Stock truth (reservation / issue / return movements) belongs to M09 Inventory: a line with
    ``part_line`` set was written by ``inventory.services.consume`` for stock issued to this order; a free-text
    line (``part_line`` NULL) records a non-stocked consumable and moves no stock."""

    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, related_name="materials")
    description = models.CharField(max_length=200)
    part_number = models.CharField(max_length=60, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=3)
    unit = models.CharField(max_length=20, default="pcs")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    # M09: set when the line records consumption of stock issued through an inventory part line (D-042); NULL =
    # a free-text line for non-stocked consumables (moves no stock).
    part_line = models.ForeignKey("inventory.WorkOrderPart", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="material_rows")

    class Meta:
        ordering = ["created_at"]
        constraints = [models.CheckConstraint(condition=models.Q(quantity__gt=0), name="work_order_material_qty_pos")]
