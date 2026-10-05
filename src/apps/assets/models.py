from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

from apps.core.models import TenantOwnedModel

from .workflow import ACTIVE, STATES


class AssetCategory(TenantOwnedModel):
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=300, blank=True)
    is_active = models.BooleanField(default=True)
    attribute_definitions = models.JSONField(
        default=list, blank=True,
        help_text="Custom attributes assets of this category carry (see assets.attributes).")

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "asset categories"
        constraints = [models.UniqueConstraint(Lower("name"), "organization", name="uniq_asset_category_per_org")]

    @property
    def attribute_text(self) -> str:
        from .attributes import to_text

        return to_text(self.attribute_definitions)

    def __str__(self):
        return self.name


class Asset(TenantOwnedModel):
    """M02 asset master. ``status`` is changed ONLY by ``assets.services.change_status``."""

    Status = models.TextChoices("Status", {s: (s, s.replace("_", " ").title()) for s in STATES})

    asset_tag = models.CharField(max_length=40, help_text="Human-readable identifier, unique per organization.")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.ForeignKey(AssetCategory, on_delete=models.PROTECT, related_name="assets")
    manufacturer = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    serial_number = models.CharField(max_length=100, blank=True)
    purchase_date = models.DateField(null=True, blank=True)
    commission_date = models.DateField(null=True, blank=True)
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="assets")
    zone = models.ForeignKey("sites.Zone", null=True, blank=True, on_delete=models.PROTECT, related_name="assets")
    owner = models.ForeignKey(
        "tenancy.Membership", null=True, blank=True, on_delete=models.PROTECT, related_name="owned_assets")
    warranty_ref = models.CharField(
        max_length=200, blank=True, help_text="Free-text reference; coverage logic belongs to M10 (later).")
    attributes = models.JSONField(default=dict, blank=True,
                                  help_text="Values of the category's custom attributes (key -> text).")
    status = models.CharField(max_length=20, choices=Status.choices, default=ACTIVE, db_index=True)

    class Meta:
        ordering = ["asset_tag"]
        constraints = [
            models.UniqueConstraint(Lower("asset_tag"), "organization", name="uniq_asset_tag_per_org"),
            models.UniqueConstraint(
                Lower("manufacturer"), Lower("serial_number"), "organization",
                condition=~models.Q(serial_number=""), name="uniq_asset_serial_per_maker_org"),
            models.CheckConstraint(
                condition=models.Q(purchase_date__isnull=True) | models.Q(commission_date__isnull=True)
                | models.Q(commission_date__gte=models.F("purchase_date")),
                name="asset_commission_after_purchase"),
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["organization", "site"]),
            models.Index(fields=["organization", "category"]),
            models.Index(fields=["organization", "serial_number"]),
        ]

    def __str__(self):
        return f"{self.asset_tag} · {self.name}"


class _AppendOnly(models.Model):
    """History rows are never updated or deleted."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError(f"{type(self).__name__} is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(f"{type(self).__name__} is append-only.")


class AssetStatusHistory(_AppendOnly, TenantOwnedModel):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="status_history")
    from_status = models.CharField(max_length=20, blank=True)  # blank = registration
    to_status = models.CharField(max_length=20)
    action = models.CharField(max_length=30)
    reason = models.CharField(max_length=500, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    source = models.CharField(max_length=40, default="manual", help_text="manual, or the module that caused it")

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["asset", "-created_at"])]

    def __str__(self):
        return f"{self.from_status or 'new'} -> {self.to_status}"


class AssetLocationHistory(_AppendOnly, TenantOwnedModel):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="location_history")
    from_site = models.ForeignKey("sites.Site", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    from_zone = models.ForeignKey("sites.Zone", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    to_site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="+")
    to_zone = models.ForeignKey("sites.Zone", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=500, blank=True)
    moved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["asset", "-created_at"])]

    def __str__(self):
        return f"{self.asset_id} moved to {self.to_site_id}"


class AssetDocument(TenantOwnedModel):
    class DocType(models.TextChoices):
        MANUAL = "MANUAL", "Manual"
        DATASHEET = "DATASHEET", "Datasheet / specification"
        CERTIFICATE = "CERTIFICATE", "Certificate"
        COMMISSIONING = "COMMISSIONING", "Commissioning record"
        PHOTO = "PHOTO", "Photograph"
        OTHER = "OTHER", "Other"

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="documents")
    title = models.CharField(max_length=150)
    doc_type = models.CharField(max_length=14, choices=DocType.choices, default=DocType.OTHER)
    attachment = models.OneToOneField(
        "files.Attachment", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    # Removal is a soft state change (the stored file and the audit trail stay): a removed document is hidden and
    # can no longer be downloaded.
    is_active = models.BooleanField(default=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    removed_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["asset", "-created_at"])]

    def __str__(self):
        return self.title


class AssetMeter(TenantOwnedModel):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="meters")
    name = models.CharField(max_length=80, help_text="e.g. Run hours, Odometer, Cycle count")
    unit = models.CharField(max_length=20, help_text="e.g. h, km, cycles")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), "asset", name="uniq_meter_name_per_asset")]

    def __str__(self):
        return f"{self.name} ({self.unit})"


class AssetMeterReading(TenantOwnedModel):
    """Monotonic non-decreasing readings (enforced in ``assets.services.record_reading``)."""

    meter = models.ForeignKey(AssetMeter, on_delete=models.PROTECT, related_name="readings")
    value = models.DecimalField(max_digits=16, decimal_places=3)
    read_at = models.DateTimeField()
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    source = models.CharField(max_length=40, default="manual")
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-read_at", "-created_at"]
        indexes = [models.Index(fields=["meter", "-read_at"])]
        constraints = [models.CheckConstraint(condition=models.Q(value__gte=0), name="meter_reading_gte_0")]


class AssetComponent(TenantOwnedModel):
    """M03 hierarchy edge: ``child`` is an assembly/component/replaceable part of ``parent``.
    A child has at most one parent (OneToOne), which makes the structure a forest; cycles, depth and
    same-organization/site rules are enforced in ``assets.hierarchy``."""

    class Relationship(models.TextChoices):
        ASSEMBLY = "ASSEMBLY", "Assembly"
        COMPONENT = "COMPONENT", "Component"
        REPLACEABLE_PART = "REPLACEABLE_PART", "Replaceable part"

    parent = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="child_links")
    child = models.OneToOneField(Asset, on_delete=models.PROTECT, related_name="parent_link")
    relationship_type = models.CharField(max_length=20, choices=Relationship.choices, default=Relationship.COMPONENT)
    quantity = models.PositiveSmallIntegerField(default=1)
    part_number = models.CharField(max_length=100, blank=True, help_text="Bridge to M09 inventory parts (later).")
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["parent__asset_tag", "child__asset_tag"]
        constraints = [
            models.CheckConstraint(condition=~models.Q(parent=models.F("child")), name="component_not_self"),
            models.CheckConstraint(condition=models.Q(quantity__gte=1), name="component_quantity_gte_1"),
        ]
        indexes = [models.Index(fields=["organization", "parent"])]
