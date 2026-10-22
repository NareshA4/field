"""UI utilities and role/persona detectors."""
from __future__ import annotations


def is_client_membership(membership) -> bool:
    """Returns True if the membership belongs to a client/requester portal persona.

    Checks:
    1. Active PortalAccount in the organization.
    2. Any enabled PortalAccount record for this membership.
    3. Assigned `client_requester` RBAC role.
    4. Demo/Persona email convention (client.requester@...).
    5. Pure client-only portal permissions.
    """
    if membership is None:
        return False

    # 1. Active portal account via selector
    try:
        from apps.portal.selectors import is_portal_account_active

        if is_portal_account_active(membership):
            return True
    except Exception:
        pass

    # 2. PortalAccount existence in org
    try:
        from apps.portal.models import PortalAccount

        if PortalAccount.objects.for_organization(membership.organization_id).filter(
            membership=membership,
            is_enabled=True,
        ).exists():
            return True
    except Exception:
        pass

    # 3. Role check: client_requester
    try:
        from apps.rbac.models import MembershipRole

        if MembershipRole.objects.filter(
            membership=membership,
            role__system_key="client_requester",
        ).exists():
            return True
    except Exception:
        pass

    # 4. User email or job title prefix
    user = getattr(membership, "user", None)
    email = getattr(user, "email", "")
    if email and email.lower().startswith("client.requester@"):
        return True

    # 5. Permission fallback
    try:
        from apps.rbac import services as rbac

        perms = rbac.membership_permissions(membership)
        client_allowed = {
            "incident.report_owned",
            "incident.view_owned",
            "incident.view_assigned",
            "incident.attach",
            "incident.confirm",
        }
        if perms and all(p.startswith("portal.request.") or p in client_allowed for p in perms):
            return True
    except Exception:
        pass

    return False
