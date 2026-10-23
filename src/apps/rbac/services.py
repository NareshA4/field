"""RBAC service layer: permission sync, system-role seeding, authorization checks, role management.

Authorization decision (docs/blueprint/06):
    identity + org membership + permission + tenant object + scope + valid state
``has_permission`` answers the *permission* term for an already-verified, active membership; the tenant
object term is enforced by ``TenantManager``; the state term by ``core.workflow``.
"""
from __future__ import annotations

import dataclasses
from fnmatch import fnmatchcase

from django.db import transaction

from apps.audit import services as audit
from apps.core.exceptions import Conflict, PermissionDenied, ValidationFailed

from . import catalog
from .models import MembershipRole, Permission, Role, RolePermission
from .role_templates import BY_KEY, TEMPLATES

# --- catalog / system roles ------------------------------------------------------------------


def sync_permissions() -> int:
    """Mirrors the in-code catalog into the Permission table. Returns number of rows created."""
    created = 0
    for d in catalog.all_defs():
        perm, is_created = Permission.objects.get_or_create(
            code=d.code,
            defaults={"module": d.module, "description": d.description},
        )
        if is_created:
            created += 1
        elif (perm.module, perm.description) != (d.module, d.description):
            perm.module, perm.description = d.module, d.description
            perm.save(update_fields=["module", "description", "updated_at"])
    return created


def _template_codes(template) -> set[str]:
    codes = catalog.all_codes()
    matched = {c for c in codes if any(fnmatchcase(c, p) for p in template.patterns)}
    literal = {p for p in template.patterns if not any(ch in p for ch in "*?[")}
    return {c for c in matched
            if c in literal or not any(fnmatchcase(c, x) for x in getattr(template, "excludes", ()))}


@transaction.atomic
def seed_system_roles(org, prune_extra: bool = False) -> dict[str, Role]:
    """Creates any missing system roles for ``org`` and adds missing template permissions.
    When ``prune_extra=True``, also removes unapproved extra permissions from system roles."""
    sync_permissions()
    perm_by_code = {p.code: p for p in Permission.objects.all()}
    roles: dict[str, Role] = {}
    for tpl in TEMPLATES:
        role = Role.objects.unscoped().filter(organization=org, system_key=tpl.key).first()
        if role is None:
            role = Role(organization=org, name=tpl.name, description=tpl.description,
                        system_key=tpl.key, is_owner=tpl.is_owner)
            role.save()
        have = set(role.role_permissions.values_list("permission__code", flat=True))
        expected = _template_codes(tpl)
        for code in sorted(expected - have):
            RolePermission.objects.create(role=role, permission=perm_by_code[code])
        if prune_extra and not tpl.is_owner:
            extra = have - expected
            if extra:
                RolePermission.objects.filter(role=role, permission__code__in=extra).delete()
        roles[tpl.key] = role

    for role in Role.objects.unscoped().filter(organization=org, system_key__gt=""):
        tpl = BY_KEY.get(role.system_key)
        if tpl and not tpl.is_owner:
            expected = _template_codes(tpl)
            have = set(role.role_permissions.values_list("permission__code", flat=True))
            for code in sorted(expected - have):
                RolePermission.objects.create(role=role, permission=perm_by_code[code])
            if prune_extra:
                extra = have - expected
                if extra:
                    RolePermission.objects.filter(role=role, permission__code__in=extra).delete()

    return roles


def sync_all_organizations(prune_extra: bool = False) -> int:
    from apps.tenancy.models import Organization

    count = 0
    for org in Organization.objects.all():
        seed_system_roles(org, prune_extra=prune_extra)
        count += 1
    return count


# --- checks ----------------------------------------------------------------------------------


def clear_cache(membership):
    membership._perm_cache = None
    membership._site_perm_cache = None


def membership_permissions(membership) -> frozenset[str]:
    """Organization-WIDE permission codes of an ACTIVE membership (cached on the instance per request).
    Roles assigned for a single site contribute nothing here; see ``site_permissions``."""
    cached = getattr(membership, "_perm_cache", None)
    if cached is not None:
        return cached
    if not membership.is_active:
        perms: frozenset[str] = frozenset()
    else:
        roles = Role.objects.unscoped().filter(
            organization_id=membership.organization_id, membership_roles__membership=membership,
            membership_roles__site__isnull=True,
        )
        if roles.filter(is_owner=True).exists():
            perms = frozenset(catalog.all_codes())
        else:
            perms = frozenset(
                RolePermission.objects.filter(role__in=roles).values_list("permission__code", flat=True)
            )
            perms = catalog.expand(perms)
    membership._perm_cache = perms
    return perms


def site_permissions(membership) -> dict:
    """{site_id: permission codes} from roles assigned for specific sites. Only permissions registered as
    ``site_scoped`` are honoured, so a site assignment can never widen organization-level rights."""
    cached = getattr(membership, "_site_perm_cache", None)
    if cached is not None:
        return cached
    result: dict = {}
    if membership.is_active:
        scopable = catalog.site_scoped_codes()
        rows = MembershipRole.objects.filter(
            membership=membership, site__isnull=False, role__is_owner=False,
            site__organization_id=membership.organization_id,
        ).values_list("site_id", "role__role_permissions__permission__code")
        acc: dict = {}
        for site_id, code in rows:
            if code and code in scopable:
                acc.setdefault(site_id, set()).add(code)
        result = {k: catalog.expand(v) for k, v in acc.items()}
    membership._site_perm_cache = result
    return result


def _site_id(site):
    return getattr(site, "pk", site)


def has_permission(membership, code: str, site=None) -> bool:
    """Is ``code`` granted organization-wide, or (when ``site`` is given) for that site by a site-scoped role?
    Without ``site`` only organization-wide grants count (strict)."""
    if membership is None:
        return False
    if not catalog.is_registered(code):
        raise ValueError(f"Unknown permission code '{code}' (not in catalog).")
    if code in membership_permissions(membership):
        return True
    if site is not None:
        return code in site_permissions(membership).get(_site_id(site), frozenset())
    return False


def has_permission_anywhere(membership, code: str) -> bool:
    """Granted organization-wide OR for at least one site: the gate for list/detail endpoints, which must then
    restrict data with ``site_scope``."""
    if has_permission(membership, code):
        return True
    return any(code in perms for perms in site_permissions(membership).values())


@dataclasses.dataclass(frozen=True)
class SiteScope:
    """The sites a membership may reach for one permission: every site, or an explicit id set."""

    all_sites: bool
    site_ids: frozenset = frozenset()

    def allows(self, site) -> bool:
        return self.all_sites or _site_id(site) in self.site_ids

    def filter(self, qs, field: str = "site_id"):
        """Restricts a queryset to the permitted sites (server-side; never rely on templates)."""
        return qs if self.all_sites else qs.filter(**{f"{field}__in": self.site_ids})


def site_scope(membership, code: str) -> SiteScope:
    if membership is None:
        return SiteScope(False)
    if has_permission(membership, code):
        return SiteScope(True)
    return SiteScope(False, frozenset(
        sid for sid, perms in site_permissions(membership).items() if code in perms))


def has_role_assignment_permission(membership, site=None) -> bool:
    """Checks whether the membership has role-assignment authority (user.update),
    either org-wide, or for a specific site."""
    if membership is None or not membership.is_active:
        return False
    if is_owner(membership):
        return True
    if has_permission(membership, "user.update"):
        return True
    qs = MembershipRole.objects.filter(
        membership=membership,
        role__role_permissions__permission__code="user.update",
    )
    if site is not None:
        qs = qs.filter(site_id=_site_id(site))
    return qs.exists()


def authorized_sites_for_actor(actor_membership) -> SiteScope:
    """The SiteScope of sites the actor is authorized to manage or assign roles for."""
    if actor_membership is None or not actor_membership.is_active:
        return SiteScope(False)
    if is_owner(actor_membership):
        return SiteScope(True)

    # Actor MUST hold role-assignment authority (user.update).
    # site.update alone DOES NOT grant permission to assign user roles.
    if not has_role_assignment_permission(actor_membership):
        return SiteScope(False)

    has_org_user_update = has_permission(actor_membership, "user.update")
    site_user_update_qs = MembershipRole.objects.filter(
        membership=actor_membership,
        site__isnull=False,
        site__organization_id=actor_membership.organization_id,
        role__role_permissions__permission__code="user.update",
    )

    if not has_org_user_update:
        # Only has site-scoped user.update
        site_ids = set(site_user_update_qs.values_list("site_id", flat=True))
        return SiteScope(False, frozenset(site_ids))

    # Actor has org-wide user.update.
    # Check if their site management authority is explicitly scoped:
    site_update_scope = site_scope(actor_membership, "site.update")
    site_view_scope = site_scope(actor_membership, "site.view")
    if site_update_scope.all_sites or site_view_scope.all_sites:
        return SiteScope(True)

    scoped_sites = set(site_update_scope.site_ids) | set(site_view_scope.site_ids)
    if not scoped_sites:
        return SiteScope(True)
    return SiteScope(False, frozenset(scoped_sites))


def is_owner(membership) -> bool:
    if membership is None or not membership.is_active:
        return False
    return MembershipRole.objects.filter(membership=membership, role__is_owner=True, site__isnull=True).exists()


# --- owner protection ----------------------------------------------------------------------


def owner_memberships(org, *, exclude=None):
    from apps.tenancy.models import Membership

    qs = Membership.objects.unscoped().filter(
        organization=org,
        status__in=[Membership.Status.ACTIVE, Membership.Status.INVITED],
        membership_roles__role__is_owner=True,
    )
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    return qs.distinct()


def assert_not_last_owner(membership):
    if is_owner(membership) or MembershipRole.objects.filter(membership=membership, role__is_owner=True).exists():
        if not owner_memberships(membership.organization, exclude=membership).exists():
            raise Conflict(
                "An organization must keep at least one Owner.", code="last_owner",
            )


# --- membership role assignment ----------------------------------------------------------------


def _describe(role, site) -> str:
    return f"{role.name} @ {site.code}" if site is not None else role.name


@transaction.atomic
def set_membership_roles(membership, roles: list[Role], *, actor, request=None, system: bool = False):
    """Replaces ALL role assignments of a membership with organization-wide ``roles``."""
    set_membership_assignments(membership, [(r, None) for r in roles], actor=actor, request=request,
                               system=system)


@transaction.atomic
def set_membership_assignments(membership, assignments: list, *, actor, request=None, system: bool = False):
    """Replaces the role assignments of a membership. ``assignments`` = [(Role, Site | None)]; a ``None`` site
    means organization-wide. Only Owners may grant or revoke the Owner role, which is always org-wide."""
    org = membership.organization
    wanted: dict = {}
    for role, site in assignments:
        if role.organization_id != org.pk:
            raise ValidationFailed("Role belongs to a different organization.", code="cross_tenant_role")
        if site is not None and site.organization_id != org.pk:
            raise ValidationFailed("Site belongs to a different organization.", code="cross_tenant_site")
        if site is not None and role.is_owner:
            raise ValidationFailed("The Owner role is organization-wide and cannot be limited to a site.",
                                   code="owner_not_site_scoped")
        wanted[(role.pk, _site_id(site))] = (role, site)
    current = {(mr.role_id, mr.site_id): mr
               for mr in MembershipRole.objects.filter(membership=membership).select_related("role", "site")}
    touches_owner = any(r.is_owner for key, (r, _) in wanted.items() if key not in current) or any(
        mr.role.is_owner for key, mr in current.items() if key not in wanted
    )
    if touches_owner and not system:
        actor_membership = _membership_of(actor, org)
        if not is_owner(actor_membership):
            raise PermissionDenied("Only an Owner can grant or revoke the Owner role.", code="owner_only")
    if not system and actor is not None:
        actor_membership = _membership_of(actor, org)
        if actor_membership is not None and not is_owner(actor_membership):
            new_assignments = {k: v for k, v in wanted.items() if k not in current}
            if new_assignments:
                if membership.user_id == getattr(actor, "pk", None):
                    raise PermissionDenied("You cannot assign new roles to yourself.", code="self_assignment")
                if not has_role_assignment_permission(actor_membership):
                    raise PermissionDenied(
                        "You do not have permission to assign roles.",
                        code="permission_denied",
                    )
                actor_site_scope = authorized_sites_for_actor(actor_membership)
                actor_org_perms = set(membership_permissions(actor_membership))
                actor_site_perms = site_permissions(actor_membership)
                for (role_pk, sid), (r, site) in new_assignments.items():
                    if site is None:
                        if not actor_site_scope.all_sites:
                            raise PermissionDenied(
                                "Organization-wide role assignment requires full site authority.",
                                code="site_scope_exceeded",
                            )
                        r_perms = set(r.role_permissions.values_list("permission__code", flat=True))
                        excess = r_perms - actor_org_perms
                        if excess:
                            raise PermissionDenied(
                                "Cannot assign roles with permissions beyond your own authority.",
                                code="privilege_escalation",
                                details={"excess": sorted(excess)},
                            )
                    else:
                        if not actor_site_scope.allows(site):
                            raise PermissionDenied(
                                f"You do not have permission to assign roles for site {getattr(site, 'code', site)}.",
                                code="unauthorized_site",
                            )
                        site_key = _site_id(site)
                        site_perms_for_site = set(actor_site_perms.get(site_key, frozenset()))
                        actor_effective = actor_org_perms | site_perms_for_site
                        r_site_perms = {
                            c for c in r.role_permissions.values_list("permission__code", flat=True)
                            if c in catalog.site_scoped_codes()
                        }
                        excess = r_site_perms - actor_effective
                        if excess:
                            raise PermissionDenied(
                                "Cannot assign roles with permissions beyond your own authority.",
                                code="privilege_escalation",
                                details={"excess": sorted(excess)},
                            )
    removed_owner = any(mr.role.is_owner for key, mr in current.items() if key not in wanted)
    if removed_owner and not any(r.is_owner for r, _ in wanted.values()):
        assert_not_last_owner(membership)

    before = sorted(_describe(mr.role, mr.site) for mr in current.values())
    for key, mr in current.items():
        if key not in wanted:
            mr.delete()
    for key, (r, site) in wanted.items():
        if key not in current:
            MembershipRole.objects.create(membership=membership, role=r, site=site)
    clear_cache(membership)
    after = sorted(_describe(r, s) for r, s in wanted.values())
    if before != after:
        audit.record("membership.roles_changed", actor=actor, organization=org, target=membership,
                     before={"roles": before}, after={"roles": after}, request=request)


def _membership_of(user, org):
    from apps.tenancy.models import Membership

    if user is None:
        return None
    return Membership.objects.unscoped().filter(user=user, organization=org).first()


# --- role management -------------------------------------------------------------------------


@transaction.atomic
def create_role(org, *, name: str, description: str, permission_codes: list[str], actor, request=None) -> Role:
    name = name.strip()
    if not name:
        raise ValidationFailed("Role name is required.")
    if Role.objects.unscoped().filter(organization=org, name__iexact=name).exists():
        raise Conflict("A role with this name already exists.", code="duplicate_role")
    actor_membership = _membership_of(actor, org)
    if actor_membership is not None and not is_owner(actor_membership):
        actor_org_perms = set(membership_permissions(actor_membership))
        excess = set(permission_codes) - actor_org_perms
        if excess:
            raise PermissionDenied(
                "Cannot create a role with permissions beyond your own authority.",
                code="privilege_escalation",
                details={"excess": sorted(excess)},
            )
    role = Role(organization=org, name=name, description=description.strip())
    role.save()
    _apply_permissions(role, permission_codes)
    audit.record("role.created", actor=actor, organization=org, target=role,
                 after={"name": name, "permissions": sorted(permission_codes)}, request=request)
    return role


@transaction.atomic
def update_role(role: Role, *, name: str, description: str, permission_codes: list[str], actor, request=None):
    if role.is_owner:
        raise PermissionDenied("The Owner role is locked.", code="role_locked")
    name = name.strip()
    if not name:
        raise ValidationFailed("Role name is required.")
    if role.is_system and name != role.name:
        aliases_map = {
            "technician": {"Technician", "Field Technician"},
            "stores_manager": {"Stores Manager", "Stores / Inventory", "Stores"},
            "auditor": {"Auditor", "Auditor / Report Consumer", "Auditor / Compliance", "Compliance"},
            "owner": {"Organization Owner", "Super Admin", "Super Admin / Organization Owner"},
            "supervisor": {"Supervisor", "Maintenance Supervisor"},
        }
        valid_names = {role.name} | aliases_map.get(role.system_key, set())
        tmpl = BY_KEY.get(role.system_key)
        if tmpl:
            valid_names.add(tmpl.name)
        if name not in valid_names:
            raise ValidationFailed("System roles cannot be renamed.", code="system_role")
    if Role.objects.unscoped().filter(organization=role.organization, name__iexact=name).exclude(pk=role.pk).exists():
        raise Conflict("A role with this name already exists.", code="duplicate_role")
    actor_membership = _membership_of(actor, role.organization)
    if actor_membership is not None and not is_owner(actor_membership):
        actor_org_perms = set(membership_permissions(actor_membership))
        excess = set(permission_codes) - actor_org_perms
        if excess:
            raise PermissionDenied(
                "Cannot grant permissions beyond your own authority.",
                code="privilege_escalation",
                details={"excess": sorted(excess)},
            )
    before = {"name": role.name, "description": role.description,
              "permissions": sorted(role.role_permissions.values_list("permission__code", flat=True))}
    role.name, role.description = name, description.strip()
    role.save()
    _apply_permissions(role, permission_codes)
    audit.record("role.updated", actor=actor, organization=role.organization, target=role, before=before,
                 after={"name": name, "description": role.description, "permissions": sorted(permission_codes)},
                 request=request)
    # cached permission sets of in-flight requests are per-request only; nothing global to invalidate


def _apply_permissions(role: Role, codes: list[str]):
    codes = set(codes)
    unknown = codes - catalog.all_codes()
    if unknown:
        raise ValidationFailed("Unknown permissions.", details={"unknown": sorted(unknown)})
    perm_by_code = {p.code: p for p in Permission.objects.filter(code__in=codes)}
    existing = {rp.permission.code: rp for rp in role.role_permissions.select_related("permission")}
    for code, rp in existing.items():
        if code not in codes:
            rp.delete()
    for code in codes - set(existing):
        RolePermission.objects.create(role=role, permission=perm_by_code[code])


@transaction.atomic
def delete_role(role: Role, *, actor, request=None):
    if role.is_system:
        raise PermissionDenied("System roles cannot be deleted.", code="system_role")
    if role.membership_roles.exists():
        raise Conflict("Role is assigned to users; reassign them first.", code="role_in_use")
    snap = {"name": role.name}
    org = role.organization
    pk = role.pk
    role.delete()
    audit.record("role.deleted", actor=actor, organization=org, target_repr=f"Role {snap['name']} ({pk})",
                 before=snap, request=request)


def system_role_by_key(org, key: str) -> Role:
    if key not in BY_KEY:
        raise KeyError(key)
    return Role.objects.unscoped().get(organization=org, system_key=key)
