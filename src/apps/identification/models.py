from django.conf import settings
from django.db import models

from apps.core.models import TenantOwnedModel


class AssetIdentifier(TenantOwnedModel):
    class Kind(models.TextChoices):
        QR = "QR", "QR Code"
        BARCODE = "BARCODE", "Barcode"

    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT, related_name="identifiers")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.QR)
    token = models.CharField(max_length=64, unique=True, db_index=True)
    is_active = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    revoked_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "kind"],
                condition=models.Q(is_active=True),
                name="uniq_active_identifier_per_asset_kind",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "asset", "kind"]),
            models.Index(fields=["token"]),
        ]

    def __str__(self):
        return f"{self.kind} ({self.token}) for {self.asset.asset_tag}"


class ScanEvent(TenantOwnedModel):
    identifier = models.ForeignKey(
        AssetIdentifier, null=True, blank=True, on_delete=models.SET_NULL, related_name="scans"
    )
    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT, related_name="scans")
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["asset", "-created_at"]),
            models.Index(fields=["organization", "-created_at"]),
        ]

    @property
    def service_request(self):
        return None

    def __str__(self):
        return f"Scan of {self.asset.asset_tag} at {self.created_at}"
