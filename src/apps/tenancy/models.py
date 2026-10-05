from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import BaseModel, TenantOwnedModel


class Organization(BaseModel):
    """The tenant boundary. Organizations are never hard-deleted: they are suspended (PROTECT FKs)."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        SUSPENDED = "SUSPENDED", "Suspended"

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=60, unique=True)
    legal_name = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    suspended_reason = models.CharField(max_length=300, blank=True)
    timezone = models.CharField(max_length=64, default="UTC")
    country = models.CharField(max_length=2, blank=True, help_text="ISO 3166-1 alpha-2")
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    address = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), name="uniq_org_name_ci")]

    def __str__(self):
        return self.name

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE

    @staticmethod
    def make_slug(name: str) -> str:
        return slugify(name)[:60]


class Membership(TenantOwnedModel):
    """User <-> Organization link. Roles are attached through ``rbac.MembershipRole``."""

    class Status(models.TextChoices):
        INVITED = "INVITED", "Invited"
        ACTIVE = "ACTIVE", "Active"
        SUSPENDED = "SUSPENDED", "Suspended"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="memberships")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.INVITED, db_index=True)
    job_title = models.CharField(max_length=100, blank=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    invited_at = models.DateTimeField(default=timezone.now)
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "organization"], name="uniq_membership")]
        indexes = [models.Index(fields=["organization", "status"])]
        ordering = ["user__full_name"]

    def __str__(self):
        return f"{self.user.email} @ {self.organization.slug}"

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE
