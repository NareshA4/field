from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import TenantOwnedModel
from apps.core.tenant import TenantQuerySet


class WorkOrder(TenantOwnedModel):
    class SourceType(models.TextChoices):
        REQUEST = "REQUEST", "Service request"
        INCIDENT = "INCIDENT", "Incident"
        PM = "PM", "Preventive maintenance"
        MANUAL = "MANUAL", "Manual"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PLANNED = "PLANNED", "Planned"
        ASSIGNED = "ASSIGNED", "Assigned"
        DISPATCHED = "DISPATCHED", "Dispatched"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        ON_HOLD = "ON_HOLD", "On Hold"
        COMPLETED = "COMPLETED", "Completed"
        SUPERVISOR_REVIEW = "SUPERVISOR_REVIEW", "Supervisor Review"
        CLOSED = "CLOSED", "Closed"
        CANCELLED = "CANCELLED", "Cancelled"

    class WorkType(models.TextChoices):
        CORRECTIVE = "CORRECTIVE", "Corrective"
        PREVENTIVE = "PREVENTIVE", "Preventive"
        EMERGENCY = "EMERGENCY", "Emergency"
        INSPECTION = "INSPECTION", "Inspection"
        INSTALLATION = "INSTALLATION", "Installation"

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    order_number = models.CharField(max_length=32)
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="work_orders")
    source_type = models.CharField(max_length=12, choices=SourceType.choices, default=SourceType.MANUAL, db_index=True)
    source_reference = models.CharField(max_length=150, blank=True)
    # Optional until the upstream Asset Registry app is available in this checkout.
    asset_reference = models.CharField(max_length=100, blank=True)
    service_request_reference = models.CharField(max_length=100, blank=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    work_type = models.CharField(max_length=20, choices=WorkType.choices, default=WorkType.CORRECTIVE)
    priority = models.CharField(max_length=12, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT, db_index=True)
    version = models.PositiveIntegerField(default=1, editable=False)
    planned_start = models.DateTimeField(null=True, blank=True, db_index=True)
    planned_end = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    estimated_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    assigned_to = models.ForeignKey("tenancy.Membership", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="assigned_work_orders")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_work_orders")
    dispatched_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="closed_work_orders")
    hold_reason = models.CharField(max_length=300, blank=True)
    resolution_notes = models.TextField(blank=True)
    checklist_items = models.JSONField(default=list, blank=True)
    supervisor_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "order_number"], name="uniq_wo_number_org"),
            models.CheckConstraint(condition=Q(planned_end__isnull=True) | Q(planned_start__isnull=True)
                                   | Q(planned_end__gt=models.F("planned_start")), name="wo_planned_window_valid"),
            models.CheckConstraint(condition=Q(estimated_hours__gte=0), name="wo_estimated_hours_nonnegative"),
        ]
        indexes = [models.Index(fields=["organization", "status"]),
                   models.Index(fields=["site", "status"]),
                   models.Index(fields=["assigned_to", "status"])]

    def clean(self):
        super().clean()
        if self.site_id and self.site.organization_id != self.organization_id:
            raise ValidationError({"site": "Site must belong to this work order's organization."})
        if self.assigned_to_id and self.assigned_to.organization_id != self.organization_id:
            raise ValidationError({"assigned_to": "Assignee must belong to this organization."})

    @property
    def completion_evidence(self):
        return WorkOrderEvidence.objects.filter(work_order=self).select_related("attachment")

    def __str__(self):
        return f"{self.order_number} · {self.title}"

    @property
    def number(self):
        return self.order_number


class WorkOrderEventQuerySet(TenantQuerySet):
    def update(self, **kwargs):
        raise ValidationError("Work order events are append-only.")

    def delete(self):
        raise ValidationError("Work order events are append-only.")

    def for_organization(self, organization):
        return self.filter(organization=organization)


class WorkOrderEvent(TenantOwnedModel):
    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    action = models.CharField(max_length=40)
    from_status = models.CharField(max_length=24, blank=True)
    to_status = models.CharField(max_length=24, blank=True)
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    assigned_to = models.ForeignKey("tenancy.Membership", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="work_order_assignment_events")

    objects = WorkOrderEventQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Work order events are append-only.")
        if self.work_order_id and self.organization_id != self.work_order.organization_id:
            raise ValidationError("Work order event and work order must belong to the same organization.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Work order events are append-only.")

    @property
    def notes(self):
        return self.reason


class WorkOrderLabor(TenantOwnedModel):
    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, related_name="labor_entries")
    technician = models.ForeignKey("tenancy.Membership", on_delete=models.PROTECT, related_name="work_order_labor")
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    work_date = models.DateField()
    hours = models.DecimalField(max_digits=4, decimal_places=2)
    description = models.CharField(max_length=500)

    class Meta:
        ordering = ["-work_date", "-created_at"]
        constraints = [models.CheckConstraint(condition=Q(hours__gt=0) & Q(hours__lte=24),
                                              name="wo_labor_hours_1_24")]

    @property
    def notes(self):
        return self.description


class WorkOrderMaterial(TenantOwnedModel):
    """Usage note only. M09 remains the authority for stock and stock movements."""
    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, related_name="material_entries")
    part_reference = models.CharField(max_length=100, blank=True)
    item_name = models.CharField(max_length=150)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=30, blank=True)
    notes = models.CharField(max_length=500, blank=True)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="wo_material_qty_positive")]

    @property
    def description(self):
        return self.item_name

    @property
    def part_number(self):
        return self.part_reference


class WorkOrderEvidence(TenantOwnedModel):
    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, related_name="evidence_links")
    attachment = models.ForeignKey("files.Attachment", on_delete=models.PROTECT, related_name="work_order_links")
    linked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["work_order", "attachment"], name="uniq_wo_evidence")]


class ClosureApprovalQuerySet(TenantQuerySet):
    def update(self, **kwargs):
        raise ValidationError("Closure decisions are append-only.")

    def delete(self):
        raise ValidationError("Closure decisions are append-only.")


class ClosureApproval(TenantOwnedModel):
    class Decision(models.TextChoices):
        APPROVED = "APPROVED", "Approved"
        RETURNED = "RETURNED", "Returned for rework"

    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, related_name="closure_approvals")
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                 related_name="work_order_closure_decisions")
    decision = models.CharField(max_length=12, choices=Decision.choices)
    comment = models.TextField()
    objects = ClosureApprovalQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.work_order.order_number}: {self.get_decision_display()}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Closure decisions are append-only.")
        if self.work_order_id and self.organization_id != self.work_order.organization_id:
            raise ValidationError("Closure decision and work order must belong to the same organization.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Closure decisions are append-only.")
