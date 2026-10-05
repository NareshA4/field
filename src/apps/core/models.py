import uuid

from django.db import models

from .tenant import TenantManager, get_current_org


class BaseModel(models.Model):
    """UUID primary keys: opaque identifiers, no enumerable sequential IDs in URLs/APIs."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TenantOwnedModel(BaseModel):
    """Every tenant-owned record links directly to an Organization (docs/DECISIONS.md D-004)."""

    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+", editable=False
    )

    objects = TenantManager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        ctx = get_current_org()
        if self._state.adding and not self.organization_id and ctx is not None:
            self.organization = ctx
        if not self.organization_id:
            raise ValueError(f"{type(self).__name__} requires an organization.")
        if ctx is not None and self.organization_id != ctx.pk:
            raise ValueError("Cross-tenant write blocked: object organization differs from active tenant.")
        super().save(*args, **kwargs)


class DocumentSequence(TenantOwnedModel):
    """Per-organization, per-document-type counter (service request / work order numbers). Allocation is a
    single UPDATE ... SET last_value = last_value + 1, so concurrent allocations serialise on the row lock and
    never repeat a number (see ``core.sequences.next_number``)."""

    key = models.CharField(max_length=40)
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "key"], name="uniq_sequence_per_org_key")]

    def __str__(self):
        return f"{self.key}: {self.last_value}"
