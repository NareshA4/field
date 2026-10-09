from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

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

    class ChecklistStatus(models.TextChoices):
        NOT_REQUIRED = "NOT_REQUIRED", "Not required"
        PENDING = "PENDING", "Pending"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    order_number = models.CharField(max_length=32)
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="work_orders")
    # Nullable for historical compatibility. All new records require an active same-site asset
    # through the create service; older reference-only records are backfilled where possible.
    asset = models.ForeignKey("assets.Asset", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="work_orders")
    component = models.ForeignKey("assets.Asset", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="component_work_orders")
    source_request = models.OneToOneField("incidents.ServiceRequest", null=True, blank=True,
        on_delete=models.PROTECT, related_name="work_order")
    # Stable upstream identifier for integrations (PM, requests, or other source systems).
    source_id = models.UUIDField(null=True, blank=True)
    source_type = models.CharField(max_length=12, choices=SourceType.choices, default=SourceType.MANUAL, db_index=True)
    source_reference = models.CharField(max_length=150, blank=True)
    # Optional until the upstream Asset Registry app is available in this checkout.
    asset_reference = models.CharField(max_length=100, blank=True)
    component_reference = models.CharField(max_length=100, blank=True)
    service_request_reference = models.CharField(max_length=100, blank=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    work_type = models.CharField(max_length=20, choices=WorkType.choices, null=True, blank=True, default=None)
    priority = models.CharField(max_length=12, choices=Priority.choices, null=True, blank=True, default=None)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT, db_index=True)
    version = models.PositiveIntegerField(default=1, editable=False)
    planned_start = models.DateTimeField(null=True, blank=True, db_index=True)
    planned_end = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    estimated_hours = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, default=None)
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
    # Temporary M06-only notes retained for development until M08 is connected.
    # They are never completion evidence. Official checklist data lives in M08.
    provisional_checklist = models.JSONField(default=list, blank=True)
    checklist_required = models.BooleanField(default=False)
    checklist_template_ref = models.CharField(max_length=150, blank=True)
    checklist_instance_ref = models.CharField(max_length=150, blank=True)
    checklist_status = models.CharField(max_length=20, choices=ChecklistStatus.choices,
                                        default=ChecklistStatus.NOT_REQUIRED, db_index=True)
    checklist_completed_at = models.DateTimeField(null=True, blank=True)
    checklist_validated_at = models.DateTimeField(null=True, blank=True)
    checklist_validation_state = models.CharField(max_length=20, choices=[
        ("NOT_REQUIRED", "Not required"), ("PENDING", "Pending"), ("VALID", "Valid"),
        ("INVALID", "Invalid")], default="NOT_REQUIRED", db_index=True)
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
                   models.Index(fields=["assigned_to", "status"]),
                   models.Index(fields=["organization", "checklist_status"], name="workorders_org_checklist_idx")]

    def clean(self):
        super().clean()
        if self.site_id and self.site.organization_id != self.organization_id:
            raise ValidationError({"site": "Site must belong to this work order's organization."})
        if self.asset_id:
            if self.asset.organization_id != self.organization_id:
                raise ValidationError({"asset": "Asset must belong to this work order's organization."})
            if self.asset.site_id != self.site_id:
                raise ValidationError({"asset": "Asset must belong to the selected site."})
            if self.asset.status != "ACTIVE":
                raise ValidationError({"asset": "Selected asset is inactive."})
        if self.component_id:
            if self.component.organization_id != self.organization_id or not self.asset_id:
                raise ValidationError({"component": "Component must belong to the selected asset."})
            from apps.assets.models import AssetComponent
            if not AssetComponent.objects.filter(parent_id=self.asset_id, child_id=self.component_id).exists():
                raise ValidationError({"component": "Invalid component for the selected asset."})
        if self.source_request_id:
            if self.source_request.organization_id != self.organization_id:
                raise ValidationError({"source_request": "Source request must belong to this organization."})
            if (self.source_request.site_id != self.site_id or not self.asset_id or
                    self.source_request.asset_id != self.asset_id):
                raise ValidationError({"source_request": "Site and asset must match the source request."})
            expected_source = (self.SourceType.REQUEST
                if self.source_request.kind == "SERVICE_REQUEST" else self.SourceType.INCIDENT)
            if self.source_type != expected_source or self.source_reference != self.source_request.number:
                raise ValidationError({"source_request": "Source type and reference must match the linked request."})
        if self._state.adding:
            if self.source_type in (self.SourceType.REQUEST, self.SourceType.INCIDENT) and not self.source_request_id:
                raise ValidationError({"source_request": "A valid linked incident or request is required."})
            if self.source_type == self.SourceType.MANUAL and self.source_request_id:
                raise ValidationError({"source_request": "Manual work orders cannot link to an incident or request."})
            if self.source_type == self.SourceType.PM and not self.source_reference.strip():
                raise ValidationError({"source_reference": "A preventive-maintenance schedule reference is required."})
        if self.assigned_to_id and self.assigned_to.organization_id != self.organization_id:
            raise ValidationError({"assigned_to": "Assignee must belong to this organization."})

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        writes_status = update_fields is None or "status" in update_fields
        if (not self._state.adding and writes_status and
                not getattr(self, "_workflow_transition_write", False)):
            stored_status = type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if stored_status is not None and stored_status != self.status:
                raise ValidationError({"status": "Change work-order status through the workflow transition service."})
        return super().save(*args, **kwargs)

    @property
    def completion_evidence(self):
        return WorkOrderEvidence.objects.filter(work_order=self).select_related("attachment")

    def __str__(self):
        return f"{self.order_number} · {self.title}"

    @property
    def number(self):
        return self.order_number


class WorkOrderAssignment(TenantOwnedModel):
    """Persist the technician and scheduled window accepted at PLANNED → ASSIGNED."""
    work_order = models.OneToOneField(WorkOrder, on_delete=models.PROTECT, related_name="assignment")
    technician = models.ForeignKey("tenancy.Membership", on_delete=models.PROTECT,
                                   related_name="work_order_assignments")
    scheduled_start = models.DateTimeField()
    scheduled_end = models.DateTimeField()
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    related_name="created_work_order_assignments")

    class Meta:
        ordering = ["scheduled_start", "id"]
        constraints = [models.CheckConstraint(condition=Q(scheduled_end__gt=F("scheduled_start")),
                                              name="wo_assignment_window_valid")]

    def clean(self):
        super().clean()
        if self.scheduled_end <= self.scheduled_start:
            raise ValidationError({"scheduled_end": "Scheduled end must be later than scheduled start."})
        if self.work_order_id and self.organization_id != self.work_order.organization_id:
            raise ValidationError("Assignment and work order must belong to the same organization.")
        if self.technician_id and self.technician.organization_id != self.organization_id:
            raise ValidationError({"technician": "Technician must belong to this organization."})


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
    part_line = models.ForeignKey("inventory.WorkOrderPart", null=True, blank=True,
                                  on_delete=models.PROTECT, related_name="material_rows")
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
