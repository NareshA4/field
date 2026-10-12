from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import TenantOwnedModel


class TravelLog(TenantOwnedModel):
    """Travel record logged by a technician while in transit or on arrival at a job site."""

    work_order = models.ForeignKey(
        "workorders.WorkOrder",
        on_delete=models.PROTECT,
        related_name="workspace_travel_logs",
    )
    technician = models.ForeignKey(
        "tenancy.Membership",
        on_delete=models.PROTECT,
        related_name="workspace_travel_logs",
    )
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    duration_hours = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    distance_km = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    route_details = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    labor_entry = models.ForeignKey(
        "workorders.WorkOrderLabor",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="workspace_travel_origin",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(duration_hours__gt=0) & Q(duration_hours__lte=24),
                name="workspace_travel_duration_1_24",
            )
        ]

    def clean(self):
        super().clean()
        if self.work_order_id and self.organization_id != self.work_order.organization_id:
            raise ValidationError("Work order must belong to the same organization.")
        if self.technician_id and self.organization_id != self.technician.organization_id:
            raise ValidationError("Technician must belong to the same organization.")
        if self.duration_hours is not None and (self.duration_hours <= 0 or self.duration_hours > 24):
            raise ValidationError({"duration_hours": "Travel duration must be between 0 and 24 hours."})


class WorkNote(TenantOwnedModel):
    """Append-only technician notes attached to a work order during execution (D-041)."""

    work_order = models.ForeignKey(
        "workorders.WorkOrder",
        on_delete=models.PROTECT,
        related_name="workspace_notes",
    )
    author = models.ForeignKey(
        "tenancy.Membership",
        on_delete=models.PROTECT,
        related_name="workspace_notes",
    )
    body = models.TextField()

    class Meta:
        ordering = ["created_at"]

    def clean(self):
        super().clean()
        if self.work_order_id and self.organization_id != self.work_order.organization_id:
            raise ValidationError("Work order must belong to the same organization.")
        if not (self.body or "").strip():
            raise ValidationError({"body": "Note body cannot be empty."})
