from django.db import models
from django.db.models.functions import Lower

from apps.core.models import BaseModel, TenantOwnedModel


class Permission(BaseModel):
    """Global permission catalog row (synced from code: ``rbac.catalog``). Not tenant data."""

    code = models.CharField(max_length=80, unique=True)
    module = models.CharField(max_length=40, db_index=True)
    description = models.CharField(max_length=200)

    class Meta:
        ordering = ["module", "code"]

    def __str__(self):
        return self.code


class Role(TenantOwnedModel):
    """Organization-scoped role. ``system_key`` marks roles seeded from templates."""

    name = models.CharField(max_length=80)
    description = models.CharField(max_length=300, blank=True)
    system_key = models.CharField(max_length=40, blank=True, db_index=True)
    is_owner = models.BooleanField(default=False, help_text="Implicitly holds every permission.")
    permissions = models.ManyToManyField(Permission, through="RolePermission", related_name="roles")

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), "organization", name="uniq_role_name_per_org"),
            models.UniqueConstraint(
                fields=["organization", "system_key"],
                condition=~models.Q(system_key=""),
                name="uniq_system_role_per_org",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_system(self):
        return bool(self.system_key)


class RolePermission(BaseModel):
    # Tenant link is indirect (via role.organization). Only accessed through Role / rbac.services.
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_permissions")
    permission = models.ForeignKey(Permission, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["role", "permission"], name="uniq_role_permission")]


class MembershipRole(BaseModel):
    """A role held by a membership. ``site`` NULL = organization-wide; otherwise the role (its site-scopable
    permissions) applies to that site only. Tenant link is indirect (via membership.organization)."""

    membership = models.ForeignKey(
        "tenancy.Membership", on_delete=models.CASCADE, related_name="membership_roles"
    )
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="membership_roles")
    site = models.ForeignKey(
        "sites.Site", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["membership", "role"], condition=models.Q(site__isnull=True),
                name="uniq_membership_role_orgwide"),
            models.UniqueConstraint(
                fields=["membership", "role", "site"], condition=models.Q(site__isnull=False),
                name="uniq_membership_role_site"),
        ]
