"""View mixins that enforce the authorization decision on the server for HTML views."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.views.generic import View

from apps.core.exceptions import DomainError
from apps.rbac import services as rbac


class TenantPermissionMixin(LoginRequiredMixin):
    """identity -> active organization membership -> permission. Object ownership is enforced by the
    tenant-scoped default managers; state by services. Subclasses set ``required_permission``."""

    required_permission: str | None = None
    raise_exception = False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.tenant_error:
            return self._render_tenant_error(request)
        if request.membership is None:
            return redirect("ui:choose_org")
        code = self.required_permission
        if code is None or not rbac.has_permission_anywhere(request.membership, code):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def _render_tenant_error(self, request):
        from django.shortcuts import render

        return render(request, "errors/tenant.html", {"code": request.tenant_error}, status=403)


class DomainErrorMixin(View):
    """Translates service-layer DomainErrors into a flash message + redirect/re-render."""

    def handle_domain_error(self, request, exc: DomainError, fallback: str):
        messages.error(request, exc.message)
        return redirect(fallback)
