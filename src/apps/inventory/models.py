"""M09 Spare Parts & Inventory models.

Stock truth: ``StockBalance`` (on_hand / reserved per warehouse and part) changes ONLY through
``inventory.services`` and every change writes exactly one append-only ``StockMovement`` carrying the signed
deltas and the resulting balance. ``WorkOrderPart`` is the part requirement of a work order (requested ->
reserved -> issued -> consumed / returned -> reconciled); ``PartReservation`` is the stock-side hold behind it.
"""
from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

from apps.core.models import TenantOwnedModel


class Warehouse(TenantOwnedModel):
    """A stock location inside one site (a store room, a van, a yard)."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="warehouses")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(Lower("code"), "organization", name="uniq_warehouse_code_per_org"),
        ]
        indexes = [models.Index(fields=["organization", "site"])]

    def __str__(self):
        return f"{self.code} · {self.name}"


class Part(TenantOwnedModel):
    """Spare-part catalogue entry (organization-wide). Min / max / reorder here are defaults; a balance may
    override them per warehouse."""

    part_number = models.CharField(max_length=60)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    unit = models.CharField(max_length=20, default="pcs")
    min_stock = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    max_stock = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    reorder_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["part_number"]
        constraints = [
            models.UniqueConstraint(Lower("part_number"), "organization", name="uniq_part_number_per_org"),
            models.CheckConstraint(
                condition=models.Q(min_stock__isnull=True) | models.Q(min_stock__gte=0), name="part_min_stock_nonneg"),
            models.CheckConstraint(
                condition=models.Q(min_stock__isnull=True) | models.Q(max_stock__isnull=True)
                | models.Q(max_stock__gte=models.F("min_stock")), name="part_max_gte_min"),
        ]

    def __str__(self):
        return f"{self.part_number} · {self.name}"


class StockBalance(TenantOwnedModel):
    """Quantity of one part in one warehouse. ``available = on_hand - reserved``; never negative."""

    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="balances")
    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name="balances")
    on_hand = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    reserved = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    min_level = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    max_level = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    reorder_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)

    class Meta:
        ordering = ["warehouse__code", "part__part_number"]
        constraints = [
            models.UniqueConstraint(fields=["warehouse", "part"], name="uniq_balance_per_warehouse_part"),
            models.CheckConstraint(condition=models.Q(on_hand__gte=0), name="balance_on_hand_nonneg"),
            models.CheckConstraint(condition=models.Q(reserved__gte=0), name="balance_reserved_nonneg"),
            models.CheckConstraint(condition=models.Q(reserved__lte=models.F("on_hand")),
                                   name="balance_reserved_lte_on_hand"),
            models.CheckConstraint(
                condition=models.Q(min_level__isnull=True) | models.Q(max_level__isnull=True)
                | models.Q(max_level__gte=models.F("min_level")), name="balance_max_gte_min"),
        ]
        indexes = [models.Index(fields=["organization", "part"])]

    @property
    def available(self):
        return self.on_hand - self.reserved

    @property
    def effective_min(self):
        return self.min_level if self.min_level is not None else self.part.min_stock

    @property
    def effective_max(self):
        return self.max_level if self.max_level is not None else self.part.max_stock

    @property
    def effective_reorder_quantity(self):
        return self.reorder_quantity if self.reorder_quantity is not None else self.part.reorder_quantity

    @property
    def is_low(self) -> bool:
        low = self.effective_min
        return low is not None and self.available <= low

    def __str__(self):
        return f"{self.warehouse.code}/{self.part.part_number}: {self.on_hand}"


class WorkOrderPart(TenantOwnedModel):
    """Part requirement of a work order (the M09 'part request'). ``status`` is recomputed from the quantities in
    ``inventory.services`` (single place); RECONCILED and CANCELLED are explicit, terminal state-machine actions."""

    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        RESERVED = "RESERVED", "Reserved"
        ISSUED = "ISSUED", "Issued"
        CONSUMED = "CONSUMED", "Consumed"
        RETURNED = "RETURNED", "Returned"
        RECONCILED = "RECONCILED", "Reconciled"
        CANCELLED = "CANCELLED", "Cancelled"

    work_order = models.ForeignKey("workorders.WorkOrder", on_delete=models.PROTECT, related_name="part_lines")
    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name="work_order_lines")
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="work_order_lines")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    quantity_requested = models.DecimalField(max_digits=14, decimal_places=3)
    quantity_issued = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_consumed = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_returned = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    notes = models.CharField(max_length=300, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["work_order", "part"], name="uniq_part_line_per_work_order"),
            models.CheckConstraint(condition=models.Q(quantity_requested__gt=0), name="part_line_requested_pos"),
            models.CheckConstraint(
                condition=models.Q(quantity_issued__gte=0, quantity_consumed__gte=0, quantity_returned__gte=0),
                name="part_line_quantities_nonneg"),
            models.CheckConstraint(
                condition=models.Q(quantity_consumed__lte=models.F("quantity_issued") - models.F("quantity_returned")),
                name="part_line_consumed_returned_lte_issued"),
        ]
        indexes = [models.Index(fields=["organization", "work_order"]),
                   models.Index(fields=["organization", "status"])]

    @property
    def outstanding(self):
        """Issued to the work order and neither consumed nor returned yet."""
        return self.quantity_issued - self.quantity_consumed - self.quantity_returned

    @property
    def remaining_to_issue(self):
        return self.quantity_requested - (self.quantity_issued - self.quantity_returned)

    def __str__(self):
        return f"{self.work_order.number} · {self.part.part_number} x {self.quantity_requested}"


class PartReservation(TenantOwnedModel):
    """Stock-side hold for one work-order part line. ``quantity`` is what is currently held; the sum of the
    ACTIVE reservations of a (warehouse, part) always equals ``StockBalance.reserved`` (tested invariant)."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        RELEASED = "RELEASED", "Released"
        FULFILLED = "FULFILLED", "Fulfilled"

    part_line = models.OneToOneField(WorkOrderPart, on_delete=models.PROTECT, related_name="reservation")
    work_order = models.ForeignKey("workorders.WorkOrder", on_delete=models.PROTECT, related_name="part_reservations")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="reservations")
    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name="reservations")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gte=0), name="reservation_quantity_nonneg"),
            models.CheckConstraint(
                condition=~models.Q(status="ACTIVE") | models.Q(quantity__gt=0), name="reservation_active_has_qty"),
        ]
        indexes = [models.Index(fields=["organization", "status"]),
                   models.Index(fields=["organization", "warehouse", "part"])]

    def __str__(self):
        return f"{self.work_order.number} holds {self.quantity} x {self.part.part_number}"


class StockMovement(TenantOwnedModel):
    """Append-only ledger row. Exactly one per stock change: the deltas applied to the balance, the resulting
    balance, who did it and (when applicable) the work order / line it belongs to."""

    class Type(models.TextChoices):
        RECEIPT = "RECEIPT", "Receipt"
        ISSUE = "ISSUE", "Issue to work order"
        RETURN = "RETURN", "Return from work order"
        TRANSFER_OUT = "TRANSFER_OUT", "Transfer out"
        TRANSFER_IN = "TRANSFER_IN", "Transfer in"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"
        RESERVE = "RESERVE", "Reserve"
        RELEASE = "RELEASE", "Release reservation"

    balance = models.ForeignKey(StockBalance, on_delete=models.PROTECT, related_name="movements")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="movements")
    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=14, choices=Type.choices, db_index=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    on_hand_delta = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    reserved_delta = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    on_hand_after = models.DecimalField(max_digits=14, decimal_places=3)
    reserved_after = models.DecimalField(max_digits=14, decimal_places=3)
    work_order = models.ForeignKey("workorders.WorkOrder", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="stock_movements")
    part_line = models.ForeignKey(WorkOrderPart, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="movements")
    transfer_ref = models.UUIDField(null=True, blank=True, db_index=True)
    reference = models.CharField(max_length=100, blank=True)
    reason = models.CharField(max_length=300, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="movement_quantity_pos"),
            # no movement without a balance change
            models.CheckConstraint(condition=~models.Q(on_hand_delta=0) | ~models.Q(reserved_delta=0),
                                   name="movement_changes_balance"),
            models.CheckConstraint(condition=models.Q(on_hand_after__gte=0, reserved_after__gte=0),
                                   name="movement_after_nonneg"),
        ]
        indexes = [models.Index(fields=["organization", "warehouse", "part", "-created_at"]),
                   models.Index(fields=["organization", "work_order"])]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("StockMovement is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("StockMovement is append-only.")

    def __str__(self):
        return f"{self.movement_type} {self.quantity} x {self.part.part_number} @ {self.warehouse.code}"
