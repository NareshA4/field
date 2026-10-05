"""DRF building blocks for tenant isolation + backend RBAC.

Every tenant API view mixes in ``TenantAPIMixin`` and declares which permission each action needs:

    class FooViewSet(TenantAPIMixin, viewsets.ModelViewSet):
        permission_map = {"list": "foo.view", "create": "foo.create", ...}

An action with no mapping is DENIED (fail closed).
"""
from django.conf import settings
from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied

from apps.core import tenant
from apps.core.exceptions import PermissionDenied as DomainPermissionDenied
from apps.rbac import services as rbac

from . import selectors

MEMBER_ONLY = "__member__"  # pseudo-permission: any ACTIVE member, for strictly self-scoped data


class TenantAPIMixin:
    """Resolves + verifies the active membership right after authentication and binds the tenant context."""

    permission_map: dict[str, str] = {}
    required_permission: str | None = None

    def get_permissions(self):
        return [permissions.IsAuthenticated(), HasOrgPermission()]

    def perform_authentication(self, request):
        super().perform_authentication(request)  # populates request.user (JWT / session)
        request.membership = None
        request.organization = None
        user = request.user
        if not user.is_authenticated:
            return
        header = request.headers.get(settings.ORGANIZATION_HEADER)
        try:
            membership = selectors.resolve_membership(
                user, org_key=header, session_org_id=request.session.get(selectors.SESSION_KEY)
                if hasattr(request, "session") and not header else None,
            )
        except DomainPermissionDenied as exc:
            raise DRFPermissionDenied(exc.message, code=exc.code) from exc
        if membership is not None:
            request.membership = membership
            request.organization = membership.organization
            tenant.set_current(membership.organization)

    def get_required_permission(self) -> str | None:
        action = getattr(self, "action", None) or self.request.method.lower()
        # an action may map per HTTP method ("documents:post") when GET and POST need different permissions
        method_key = f"{action}:{self.request.method.lower()}"
        if method_key in self.permission_map:
            return self.permission_map[method_key]
        return self.permission_map.get(action, self.required_permission if not self.permission_map else None)


class HasOrgPermission(permissions.BasePermission):
    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        membership = getattr(request, "membership", None)
        if membership is None:
            self.message = "Select an organization (X-Organization header) with an active membership."
            raise DRFPermissionDenied(self.message, code="organization_required")
        code = view.get_required_permission()
        if code is None:
            return False  # unmapped action -> deny
        if code == MEMBER_ONLY:
            return membership.is_active  # self-scoped resources (own notifications)
        return rbac.has_permission_anywhere(membership, code)


class PlatformAPIMixin:
    """Platform (Super Admin) API: PLATFORM tenant mode, platform admins only. No tenant data permissions."""

    def get_permissions(self):
        return [permissions.IsAuthenticated(), IsPlatformAdmin()]

    def perform_authentication(self, request):
        super().perform_authentication(request)
        if request.user.is_authenticated and request.user.is_platform_admin:
            tenant.set_current(tenant.PLATFORM)


class IsPlatformAdmin(permissions.BasePermission):
    message = "Platform administrator access required."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_platform_admin)
