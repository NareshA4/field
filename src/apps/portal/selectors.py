from __future__ import annotations

import uuid
from django.db.models import QuerySet

from apps.tenancy.models import Membership, Organization
from .models import PortalAccount


STAGE_MAP = {
    "NEW": "PENDING",
    "TRIAGED": "PENDING",
    "APPROVED": "PENDING",
    "WORK_ORDER_CREATED": "IN_PROGRESS",
    "IN_SERVICE": "IN_PROGRESS",
    "RESOLVED": "RESOLVED",
    "CONFIRMED": "RESOLVED",
    "CLOSED": "CLOSED",
    "REJECTED": "REJECTED",
}

STATUS_LABELS = {
    "NEW": "Submitted",
    "TRIAGED": "Under Review",
    "APPROVED": "Approved",
    "WORK_ORDER_CREATED": "Scheduled",
    "IN_SERVICE": "In Service",
    "RESOLVED": "Resolved",
    "CONFIRMED": "Confirmed",
    "CLOSED": "Closed",
    "REJECTED": "Declined",
}

ACTION_LABELS = {
    "report": "Request submitted",
    "triage": "Triaged by operations",
    "approve": "Request approved",
    "reject": "Request declined",
    "link_work_order": "Work order scheduled",
    "work_order_cancelled": "Work order rescheduled",
    "start_service": "Technician commenced work",
    "resolve": "Work completed / Resolved",
    "resume_service": "Service resumed",
    "confirm": "Resolution confirmed",
    "reopen": "Request reopened",
    "close": "Request closed",
}


def get_status_stage(status: str) -> str:
    """Returns high-level lifecycle stage for an M05 status."""
    return STAGE_MAP.get(status, "PENDING")


def get_status_label(status: str) -> str:
    """Returns human-readable client-facing label for an M05 status."""
    return STATUS_LABELS.get(status, str(status).replace("_", " ").title())


def get_portal_account(membership: Membership | None) -> PortalAccount | None:
    """Returns the portal account for the given membership, if any."""
    if membership is None:
        return None
    return PortalAccount.objects.for_organization(membership.organization).filter(
        membership=membership,
    ).select_related("membership", "membership__user", "organization").first()


def get_portal_account_by_id(organization: Organization, account_id: str | uuid.UUID) -> PortalAccount | None:
    """Returns a portal account by ID scoped to the given organization."""
    return PortalAccount.objects.for_organization(organization).filter(
        pk=account_id,
    ).select_related("membership", "membership__user").first()


def accounts_for_organization(organization: Organization) -> QuerySet[PortalAccount]:
    """Returns all portal accounts for an organization."""
    return PortalAccount.objects.for_organization(organization).select_related(
        "membership", "membership__user"
    ).order_by("company_name", "membership__user__full_name")


def is_portal_account_active(membership: Membership | None) -> bool:
    """Returns True if the membership has an active enabled portal account in its organization."""
    account = get_portal_account(membership)
    return bool(account and account.is_enabled and account.membership.is_active)


def client_requests_for(membership: Membership | None) -> QuerySet[ServiceRequest]:
    """Returns service requests visible to the client portal user.

    Scoped strictly to the caller's organization and reported_by=membership.
    """
    if membership is None:
        from apps.incidents.models import ServiceRequest
        return ServiceRequest.objects.none()
    from apps.incidents.models import ServiceRequest
    return (
        ServiceRequest.objects.for_organization(membership.organization)
        .filter(reported_by=membership)
        .select_related("asset", "site", "reported_by__user")
        .order_by("-created_at")
    )


def get_client_request(membership: Membership | None, request_id: str | uuid.UUID) -> ServiceRequest | None:
    """Returns a single service request scoped to the client portal user.

    Prevents cross-client and cross-tenant access. Returns None if not found or unauthorized.
    Supports lookup by UUID (pk) or reference number.
    """
    if membership is None:
        return None
    qs = client_requests_for(membership)
    try:
        val = uuid.UUID(str(request_id))
        obj = qs.filter(pk=val).first()
        if obj:
            return obj
    except (ValueError, TypeError):
        pass
    return qs.filter(number=str(request_id).strip()).first()


def client_request_history_for(sr: ServiceRequest) -> QuerySet[ServiceRequestHistory]:
    """Returns the append-only status history for a service request in chronological order."""
    from apps.incidents.models import ServiceRequestHistory
    return (
        ServiceRequestHistory.objects.for_organization(sr.organization)
        .filter(request=sr)
        .order_by("created_at")
    )


def granted_assets_for(membership: Membership | None):
    """Returns non-terminal assets explicitly granted to the client's portal account."""
    if membership is None:
        from apps.assets.models import Asset
        return Asset.objects.none()
    account = get_portal_account(membership)
    if account is None or not account.is_enabled or not membership.is_active:
        from apps.assets.models import Asset
        return Asset.objects.none()
    from apps.assets.models import Asset
    return (
        Asset.objects.for_organization(membership.organization)
        .filter(portal_grants__portal_account=account)
        .exclude(status__in=["RETIRED", "DISPOSED"])
        .select_related("site")
        .order_by("asset_tag")
    )


def is_asset_granted_to_client(membership: Membership | None, asset_id: str | uuid.UUID) -> bool:
    """Returns True if the asset is explicitly granted and accessible to the membership."""
    if membership is None or not asset_id:
        return False
    try:
        val = uuid.UUID(str(asset_id))
    except (ValueError, TypeError):
        return False
    return granted_assets_for(membership).filter(pk=val).exists()


def get_linked_work_order(sr: ServiceRequest):
    """Returns the primary or latest linked work order for a service request with assigned technician."""
    from apps.workorders.models import WorkOrder
    return (
        WorkOrder.objects.for_organization(sr.organization)
        .filter(source_request=sr)
        .select_related("assigned_to__user")
        .order_by("-created_at")
        .first()
        or WorkOrder.objects.for_organization(sr.organization)
        .filter(source_reference=sr.number)
        .select_related("assigned_to__user")
        .order_by("-created_at")
        .first()
    )

