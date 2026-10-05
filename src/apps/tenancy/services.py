"""Organization + membership lifecycle (service layer: validation, transactions, audit)."""
from __future__ import annotations

import re
import uuid
import zoneinfo

from django.contrib.auth import get_user_model
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import Conflict, NotFound, PermissionDenied, ValidationFailed
from apps.rbac import services as rbac
from apps.rbac.models import Role

from .models import Membership, Organization

User = get_user_model()
_ORG_FIELDS = ["name", "legal_name", "timezone", "country", "contact_email", "contact_phone", "address"]


def _check_email(email: str) -> str:
    email = (email or "").strip().lower()
    try:
        validate_email(email)
    except Exception as exc:
        raise ValidationFailed("Enter a valid email address.", details={"email": email}) from exc
    return email


def _check_timezone(tz: str):
    try:
        zoneinfo.ZoneInfo(tz)
    except Exception as exc:
        raise ValidationFailed(f"Unknown timezone '{tz}'.") from exc


# --- organizations (platform level) ---------------------------------------------------------


@transaction.atomic
def create_organization(*, name, owner_email, owner_name, actor, slug=None, timezone_name="UTC",
                        country="", contact_email="", request=None) -> tuple[Organization, Membership]:
    """Super Admin onboarding: creates the org, seeds system roles, invites the initial Owner."""
    if actor is None or not actor.is_platform_admin:
        raise PermissionDenied("Only platform administrators can create organizations.")
    name = (name or "").strip()
    if len(name) < 2:
        raise ValidationFailed("Organization name is required.")
    _check_timezone(timezone_name)
    owner_email = _check_email(owner_email)
    slug = (slug or Organization.make_slug(name)).strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,58}[a-z0-9]", slug):
        raise ValidationFailed("Slug must be 3-60 chars: lowercase letters, digits and hyphens.")
    try:
        with transaction.atomic():
            org = Organization.objects.create(
                name=name, slug=slug, timezone=timezone_name, country=country.upper()[:2],
                contact_email=contact_email, created_by=actor,
            )
    except IntegrityError as exc:
        raise Conflict("An organization with this name or slug already exists.", code="duplicate_organization") from exc
    roles = rbac.seed_system_roles(org)
    audit.record("organization.created", actor=actor, organization=org, target=org,
                 after=audit.snapshot(org, _ORG_FIELDS + ["slug", "status"]), request=request)
    membership = _invite(org, email=owner_email, full_name=owner_name, roles=[roles["owner"]],
                         invited_by=actor, request=request, system=True)
    return org, membership


@transaction.atomic
def set_organization_status(org: Organization, *, active: bool, actor, reason: str = "", request=None):
    if actor is None or not actor.is_platform_admin:
        raise PermissionDenied("Only platform administrators can change organization status.")
    target = Organization.Status.ACTIVE if active else Organization.Status.SUSPENDED
    if org.status == target:
        raise Conflict(f"Organization is already {target.lower()}.", code="no_change")
    if not active and not reason.strip():
        raise ValidationFailed("A reason is required to suspend an organization.")
    before = {"status": org.status, "suspended_reason": org.suspended_reason}
    org.status = target
    org.suspended_reason = "" if active else reason.strip()[:300]
    org.save(update_fields=["status", "suspended_reason", "updated_at"])
    audit.record("organization.activated" if active else "organization.suspended", actor=actor,
                 organization=org, target=org, before=before,
                 after={"status": org.status, "suspended_reason": org.suspended_reason}, request=request)


@transaction.atomic
def update_organization(org: Organization, *, actor, request=None, **changes) -> Organization:
    allowed = {k: v for k, v in changes.items() if k in _ORG_FIELDS}
    if "name" in allowed and len(allowed["name"].strip()) < 2:
        raise ValidationFailed("Organization name is required.")
    if "timezone" in allowed:
        _check_timezone(allowed["timezone"])
    before = audit.snapshot(org, _ORG_FIELDS)
    for k, v in allowed.items():
        setattr(org, k, v.strip() if isinstance(v, str) else v)
    try:
        with transaction.atomic():
            org.save()
    except IntegrityError as exc:
        raise Conflict("An organization with this name already exists.", code="duplicate_organization") from exc
    audit.record("organization.updated", actor=actor, organization=org, target=org, before=before,
                 after=audit.snapshot(org, _ORG_FIELDS), request=request)
    return org


# --- members ---------------------------------------------------------------------------------


def _invite(org, *, email, full_name, roles, invited_by, request=None, system=False,
            assignments=None) -> Membership:
    from apps.accounts import services as accounts

    email = _check_email(email)
    full_name = (full_name or "").strip()
    user = User.objects.filter(email__iexact=email).first()
    created_user = False
    if user is None:
        if not full_name:
            raise ValidationFailed("Full name is required for a new user.")
        user = User.objects.create_user(email=email, password=None, full_name=full_name)
        created_user = True
    if Membership.objects.unscoped().filter(user=user, organization=org).exists():
        raise Conflict("This user is already a member of the organization.", code="already_member")
    # Existing accounts (with a password) are activated at once (D-006); new accounts must set a password.
    status = Membership.Status.INVITED if not user.has_usable_password() else Membership.Status.ACTIVE
    membership = Membership(
        organization=org, user=user, status=status, invited_by=invited_by,
        activated_at=timezone.now() if status == Membership.Status.ACTIVE else None,
    )
    membership.save()
    assignments = assignments if assignments is not None else [(r, None) for r in roles]
    rbac.set_membership_assignments(membership, assignments, actor=invited_by, request=request, system=system)
    audit.record("user.invited", actor=invited_by, organization=org, target=membership,
                 target_repr=f"{user.email} @ {org.slug}",
                 after={"email": user.email, "status": status, "roles": sorted(rbac._describe(r, st) for r, st in assignments),
                        "new_account": created_user}, request=request)
    accounts.queue_invitation_email(user, org, membership, new_account=created_user)
    return membership


def resolve_assignments(org, *, role_ids=None, site_ids=None, assignments=None) -> list:
    """Turns client input into validated [(Role, Site | None)] for ``org``.

    ``assignments`` = [{"role_id", "site_id" | None}]; otherwise every role in ``role_ids`` is granted for each
    site in ``site_ids`` (or organization-wide when no sites are given). Roles and sites must belong to ``org``."""
    from apps.sites.models import Site

    if assignments is not None:
        pairs = [(a["role_id"], a.get("site_id")) for a in assignments]
    else:
        sites = list(dict.fromkeys(site_ids or [])) or [None]
        pairs = [(rid, sid) for rid in dict.fromkeys(role_ids or []) for sid in sites]
    role_keys = {rid for rid, _ in pairs}
    site_keys = {sid for _, sid in pairs if sid is not None}
    roles = {r.pk: r for r in Role.objects.unscoped().filter(organization=org, pk__in=role_keys)}
    if len(roles) != len(role_keys):
        raise ValidationFailed("One or more roles are invalid for this organization.", code="invalid_roles")
    sites = {x.pk: x for x in Site.objects.unscoped().filter(organization=org, pk__in=site_keys)}
    if len(sites) != len(site_keys):
        raise ValidationFailed("One or more sites are invalid for this organization.", code="invalid_sites")
    out = []
    for rid, sid in pairs:
        if sid is not None and roles[rid].is_owner:
            raise ValidationFailed("The Owner role cannot be limited to a site.", code="owner_not_site_scoped")
        out.append((roles[rid], sites[sid] if sid is not None else None))
    return out


@transaction.atomic
def invite_member(org, *, email, full_name, role_ids=None, actor, request=None, site_ids=None,
                  assignments=None) -> Membership:
    resolved = resolve_assignments(org, role_ids=role_ids, site_ids=site_ids, assignments=assignments)
    if not resolved:
        raise ValidationFailed("Assign at least one role.", code="roles_required")
    return _invite(org, email=email, full_name=full_name, roles=[r for r, _ in resolved], invited_by=actor,
                   request=request, assignments=resolved)


@transaction.atomic
def resend_invitation(membership: Membership, *, actor, request=None):
    from apps.accounts import services as accounts

    if membership.status != Membership.Status.INVITED:
        raise Conflict("Only pending invitations can be re-sent.", code="not_pending")
    audit.record("user.invitation_resent", actor=actor, organization=membership.organization,
                 target=membership, target_repr=membership.user.email, request=request)
    accounts.queue_invitation_email(membership.user, membership.organization, membership, new_account=True)


@transaction.atomic
def update_member(membership: Membership, *, actor, full_name=None, job_title=None, role_ids=None, request=None,
                  site_ids=None, assignments=None):
    user = membership.user
    before = {"full_name": user.full_name, "job_title": membership.job_title}
    if full_name is not None:
        if not full_name.strip():
            raise ValidationFailed("Full name is required.")
        user.full_name = full_name.strip()
        user.save(update_fields=["full_name"])
    if job_title is not None:
        membership.job_title = job_title.strip()
        membership.save(update_fields=["job_title", "updated_at"])
    if role_ids is not None or assignments is not None:
        resolved = resolve_assignments(membership.organization, role_ids=role_ids, site_ids=site_ids,
                                       assignments=assignments)
        if not resolved:
            raise ValidationFailed("Assign at least one valid role.", code="invalid_roles")
        rbac.set_membership_assignments(membership, resolved, actor=actor, request=request)
    after = {"full_name": user.full_name, "job_title": membership.job_title}
    if before != after:
        audit.record("user.updated", actor=actor, organization=membership.organization, target=membership,
                     target_repr=user.email, before=before, after=after, request=request)


@transaction.atomic
def add_role_assignment(membership: Membership, *, role_id, site_id=None, actor, request=None):
    """Adds one role (organization-wide, or for one site) without touching the other assignments."""
    current = [(mr.role, mr.site) for mr in membership.membership_roles.select_related("role", "site")]
    new = resolve_assignments(membership.organization, assignments=[{"role_id": role_id, "site_id": site_id}])
    rbac.set_membership_assignments(membership, current + new, actor=actor, request=request)


@transaction.atomic
def remove_role_assignment(membership: Membership, *, assignment_id, actor, request=None):
    mrs = list(membership.membership_roles.select_related("role", "site"))
    keep = [(mr.role, mr.site) for mr in mrs if str(mr.pk) != str(assignment_id)]
    if len(keep) == len(mrs):
        raise NotFound("Role assignment not found.")
    if not keep:
        raise ValidationFailed("A member must keep at least one role; deactivate the member instead.",
                               code="roles_required")
    rbac.set_membership_assignments(membership, keep, actor=actor, request=request)


@transaction.atomic
def set_member_active(membership: Membership, *, active: bool, actor, request=None):
    if membership.user_id == getattr(actor, "pk", None):
        raise Conflict("You cannot change your own access.", code="self_change")
    target = Membership.Status.ACTIVE if active else Membership.Status.SUSPENDED
    if membership.status == target:
        raise Conflict(f"Member is already {target.lower()}.", code="no_change")
    if membership.status == Membership.Status.INVITED and active:
        raise Conflict("Pending invitations are activated by the user.", code="not_activated")
    if not active:
        rbac.assert_not_last_owner(membership)
    before = {"status": membership.status}
    membership.status = target
    if active and membership.activated_at is None:
        membership.activated_at = timezone.now()
    rbac.clear_cache(membership)
    membership.save(update_fields=["status", "activated_at", "updated_at"])
    audit.record("user.reactivated" if active else "user.deactivated", actor=actor,
                 organization=membership.organization, target=membership, target_repr=membership.user.email,
                 before=before, after={"status": membership.status}, request=request)


def get_membership(org, pk) -> Membership:
    try:
        pk = uuid.UUID(str(pk))
    except ValueError as exc:
        raise NotFound("Member not found.") from exc
    m = Membership.objects.unscoped().filter(organization=org, pk=pk).select_related("user").first()
    if m is None:
        raise NotFound("Member not found.")
    return m
