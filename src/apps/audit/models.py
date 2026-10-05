from django.db import models

from apps.core.models import BaseModel


class ImmutableError(Exception):
    pass


class AuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ImmutableError("Audit records are append-only.")

    def delete(self):
        raise ImmutableError("Audit records are append-only.")

    def for_organization(self, org):
        return self.filter(organization=org)


class AuditLog(BaseModel):
    """Append-only evidence of critical actions. Immutability is enforced in three layers:
    model/queryset guards (here), and a PostgreSQL trigger rejecting UPDATE/DELETE (migration 0002)."""

    organization = models.ForeignKey(
        "tenancy.Organization", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )  # NULL = platform-level event
    actor = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    actor_email = models.CharField(max_length=254, blank=True)  # snapshot at event time
    action = models.CharField(max_length=80, db_index=True)  # e.g. "user.invited"
    target_type = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    target_repr = models.CharField(max_length=300, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    # M15: the site of the audited record when it has one (derived by ``audit.record``); NULL = organization-level
    site_id = models.UUIDField(null=True, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["organization", "-occurred_at"]),
            models.Index(fields=["organization", "action"]),
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["organization", "site_id", "-occurred_at"]),
            models.Index(fields=["organization", "actor_email"]),
        ]

    def __str__(self):
        return f"{self.action} by {self.actor_email or 'system'}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableError("Audit records are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError("Audit records are append-only.")
