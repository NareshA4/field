from apps.core import tenant
from apps.core.exceptions import PermissionDenied

from . import selectors

PLATFORM_PREFIXES = ("/platform/", "/api/v1/platform/")


class TenantContextMiddleware:
    """Starts every request FAIL-CLOSED (no tenant), then establishes the verified tenant context.

    * ``/api/`` paths: resolved later, after DRF authentication, by ``tenancy.api.TenantAPIMixin``.
    * ``/platform/`` paths: PLATFORM mode for platform admins only.
    * everything else: session-based active organization (validated against ACTIVE memberships)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = tenant.set_current(tenant.NO_TENANT)
        request.membership = None
        request.organization = None
        request.tenant_error = None
        try:
            user = getattr(request, "user", None)
            path = request.path
            if user is not None and user.is_authenticated:
                if path.startswith(PLATFORM_PREFIXES):
                    if user.is_platform_admin:
                        tenant.set_current(tenant.PLATFORM)
                elif not path.startswith("/api/"):
                    self._resolve_session_tenant(request, user)
            return self.get_response(request)
        finally:
            tenant.reset_current(token)

    @staticmethod
    def _resolve_session_tenant(request, user):
        # one query for the user's memberships, shared with the page shell (context processor) for this request
        request.active_memberships = list(selectors.active_memberships(user))
        try:
            membership = selectors.resolve_membership(
                user, session_org_id=request.session.get(selectors.SESSION_KEY),
                candidates=request.active_memberships,
            )
        except PermissionDenied as exc:
            request.tenant_error = exc.code
            return
        if membership is not None:
            request.membership = membership
            request.organization = membership.organization
            org_id = str(membership.organization_id)
            # assigning an unchanged value still marks the session modified (a database write on EVERY page view)
            if request.session.get(selectors.SESSION_KEY) != org_id:
                request.session[selectors.SESSION_KEY] = org_id
            tenant.set_current(membership.organization)
