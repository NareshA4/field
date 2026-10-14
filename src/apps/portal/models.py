from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TenantOwnedModel


class PortalAccount(TenantOwnedModel):
    """Client / Requester portal account bound to an organization membership.

    Each client account is linked to an active membership within the organization.
    Access to the client portal requires an enabled PortalAccount with portal permissions.
    """

    membership = models.OneToOneField(
        "tenancy.Membership",
        on_delete=models.PROTECT,
        related_name="portal_account",
    )
    company_name = models.CharField(max_length=150, blank=True)
    is_enabled = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["company_name", "membership__user__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "membership"],
                name="uniq_portal_account_membership",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "is_enabled"]),
        ]

    def __str__(self):
        name = self.company_name or str(self.membership)
        status = "enabled" if self.is_enabled else "disabled"
        return f"{name} ({status})"

    def clean(self):
        super().clean()
        if self.membership_id and self.organization_id:
            if self.membership.organization_id != self.organization_id:
                raise ValidationError("PortalAccount organization must match membership organization.")

    def save(self, *args, **kwargs):
        if self.membership_id and self.organization_id:
            if self.membership.organization_id != self.organization_id:
                raise ValueError("Cross-tenant write blocked: portal account organization differs from membership.")
        super().save(*args, **kwargs)


class PortalClosureConfirmation(TenantOwnedModel):
    """Persisted client closure confirmation for a completed service request."""

    request = models.OneToOneField(
        "incidents.ServiceRequest",
        on_delete=models.PROTECT,
        related_name="closure_confirmation",
    )
    work_order = models.ForeignKey(
        "workorders.WorkOrder",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="portal_closure_confirmations",
    )
    portal_account = models.ForeignKey(
        PortalAccount,
        on_delete=models.PROTECT,
        related_name="closure_confirmations",
    )
    confirmed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="+",
    )
    confirmed_at = models.DateTimeField(auto_now_add=True)
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    feedback = models.TextField(blank=True)

    class Meta:
        ordering = ["-confirmed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "request"],
                name="uniq_portal_closure_confirmation_per_request",
            ),
        ]

    def __str__(self):
        return f"Closure Confirmation for {self.request.number} by {self.confirmed_by.email}"

    def clean(self):
        super().clean()
        if self.request_id and self.organization_id != self.request.organization_id:
            raise ValidationError("Confirmation organization must match request organization.")
        if self.portal_account_id and self.organization_id != self.portal_account.organization_id:
            raise ValidationError("Confirmation organization must match portal account organization.")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Portal closure confirmations are append-only.")
        if self.request_id and self.organization_id != self.request.organization_id:
            raise ValueError("Cross-tenant confirmation blocked.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Portal closure confirmations are permanent and cannot be deleted.")


class PortalAssetGrant(TenantOwnedModel):
    """Explicit grant permitting a client PortalAccount to access and report on an Asset."""

    portal_account = models.ForeignKey(
        PortalAccount,
        on_delete=models.CASCADE,
        related_name="asset_grants",
    )
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.CASCADE,
        related_name="portal_grants",
    )

    class Meta:
        ordering = ["asset__asset_tag"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "portal_account", "asset"],
                name="uniq_portal_account_asset_grant",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "portal_account"],
                name="portal_asset_grant_org_acc_idx",
            ),
        ]

    def __str__(self):
        return f"Grant: {self.portal_account} -> {self.asset.asset_tag}"

    def clean(self):
        super().clean()
        if self.portal_account_id and self.organization_id != self.portal_account.organization_id:
            raise ValidationError("Grant organization must match portal account organization.")
        if self.asset_id and self.organization_id != self.asset.organization_id:
            raise ValidationError("Grant organization must match asset organization.")

    def save(self, *args, **kwargs):
        if self.portal_account_id and self.organization_id != self.portal_account.organization_id:
            raise ValueError("Cross-tenant grant blocked: organization differs from portal account.")
        if self.asset_id and self.organization_id != self.asset.organization_id:
            raise ValueError("Cross-tenant grant blocked: organization differs from asset.")
        super().save(*args, **kwargs)

