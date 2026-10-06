"""M10 Warranty / AMC / Contract models.

A ``CoverageAgreement`` (warranty, AMC or service contract) is issued by a ``ServiceProvider`` for one site and
covers a set of assets (``CoveredAsset``). Work types the agreement does NOT cover are explicit
``CoverageExclusion`` rows, so eligibility is computed from data, never typed as free text. ``CoverageCheck`` is the
persisted evidence of an eligibility decision for one M06 work order. The validity state (UPCOMING / ACTIVE /
EXPIRED / INACTIVE) is derived from the dates and ``is_active``, never stored twice.
"""
import datetime

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

from apps.core.models import TenantOwnedModel


class ServiceProvider(TenantOwnedModel):
    name = models.CharField(max_length=150)
    contact_name = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    notes = models.CharField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), "organization", name="uniq_provider_name_per_org")]

    def __str__(self):
        return self.name


class CoverageAgreement(TenantOwnedModel):
    class Kind(models.TextChoices):
        WARRANTY = "WARRANTY", "Warranty"
        AMC = "AMC", "Annual maintenance contract"
        SERVICE_CONTRACT = "SERVICE_CONTRACT", "Service contract"

    reference = models.CharField(max_length=80, help_text="Contract / warranty number, unique per organization.")
    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    provider = models.ForeignKey(ServiceProvider, on_delete=models.PROTECT, related_name="agreements")
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="coverage_agreements")
    start_date = models.DateField()
    end_date = models.DateField()
    terms = models.TextField(blank=True, help_text="Coverage terms.")
    exclusion_notes = models.TextField(blank=True, help_text="Free-text exclusions (structured ones are rows).")
    sla_terms = models.CharField(max_length=300, blank=True,
                                 help_text="Provider response terms (information only; M11 owns SLA behaviour).")
    sla_profile = models.ForeignKey(
        "sla.SLAProfile", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Internal SLA profile (M11) applied to work covered by this agreement; it overrides the "
                  "site / organization profile for the covered assets while the agreement is in force.")
    renewal_alert_days = models.PositiveSmallIntegerField(default=30)
    renewal_alerted_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    deactivation_reason = models.CharField(max_length=300, blank=True)
    renewed_from = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="renewal")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["end_date", "reference"]
        constraints = [
            models.UniqueConstraint(Lower("reference"), "organization", name="uniq_agreement_ref_per_org"),
            models.CheckConstraint(condition=models.Q(end_date__gte=models.F("start_date")),
                                   name="agreement_end_after_start"),
            models.CheckConstraint(condition=models.Q(renewal_alert_days__lte=365), name="agreement_alert_days"),
        ]
        indexes = [models.Index(fields=["organization", "site"]),
                   models.Index(fields=["organization", "is_active", "end_date"])]

    def __str__(self):
        return f"{self.reference} · {self.title}"

    def state(self, on: datetime.date | None = None) -> str:
        on = on or datetime.date.today()
        if not self.is_active:
            return "INACTIVE"
        if on < self.start_date:
            return "UPCOMING"
        if on > self.end_date:
            return "EXPIRED"
        return "ACTIVE"


class CoverageExclusion(TenantOwnedModel):
    agreement = models.ForeignKey(CoverageAgreement, on_delete=models.CASCADE, related_name="exclusions")
    work_type = models.CharField(max_length=20)  # a workorders.WorkOrder.WorkType value

    class Meta:
        constraints = [models.UniqueConstraint(fields=["agreement", "work_type"], name="uniq_exclusion_per_type")]


class CoveredAsset(TenantOwnedModel):
    agreement = models.ForeignKey(CoverageAgreement, on_delete=models.CASCADE, related_name="covered_assets")
    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT, related_name="coverage_links")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["agreement", "asset"], name="uniq_asset_per_agreement")]
        indexes = [models.Index(fields=["organization", "asset"])]


class CoverageCheck(TenantOwnedModel):
    """Persisted eligibility decision for one work order (history: checking again appends a row)."""

    work_order = models.ForeignKey("workorders.WorkOrder", on_delete=models.PROTECT, related_name="coverage_checks")
    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT, related_name="+")
    agreement = models.ForeignKey(CoverageAgreement, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="checks")
    work_type = models.CharField(max_length=20)
    reference_date = models.DateField()
    eligible = models.BooleanField()
    reason = models.CharField(max_length=500)
    checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "work_order"])]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("CoverageCheck is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("CoverageCheck is append-only.")
