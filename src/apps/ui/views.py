from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.audit.models import AuditLog
from apps.rbac import services as rbac
from apps.rbac.models import Role
from apps.tenancy import selectors
from apps.tenancy.models import Membership


@login_required
def root(request):
    if request.user.is_platform_admin and request.membership is None:
        return redirect("platform_admin:organizations")
    return redirect("ui:home")


@login_required
def home(request):
    """Org dashboard. Foundation metrics are real counts; operational KPIs arrive with M14."""
    if request.tenant_error:
        return render(request, "errors/tenant.html", {"code": request.tenant_error}, status=403)
    if request.membership is None:
        if request.user.is_platform_admin:
            return redirect("platform_admin:organizations")
        return redirect("ui:choose_org")
    org = request.organization
    perms = rbac.membership_permissions(request.membership)
    if perms and all(p.startswith("portal.request.") for p in perms):  # client-only account: portal is home
        return redirect("portal:dashboard")
    stats = {
        "active_members": Membership.objects.for_organization(org).filter(status=Membership.Status.ACTIVE).count(),
        "pending_invites": Membership.objects.for_organization(org).filter(status=Membership.Status.INVITED).count(),
        "roles": Role.objects.for_organization(org).count(),
    }
    recent = []
    if rbac.has_permission(request.membership, "audit.view"):
        recent = list(AuditLog.objects.for_organization(org)[:8])
    return render(request, "ui/home.html", {"stats": stats, "recent": recent})


@login_required
def choose_org(request):
    memberships = list(selectors.active_memberships(request.user))
    if request.user.is_platform_admin and not memberships:
        return redirect("platform_admin:organizations")
    return render(request, "ui/choose_org.html", {"choices": memberships})


@login_required
@require_POST
def switch_org(request):
    """Selects among the user's OWN active memberships; nothing is granted by the posted value."""
    target = request.POST.get("organization")
    match = next((m for m in selectors.active_memberships(request.user) if str(m.organization_id) == target), None)
    if match is None:
        messages.error(request, "You are not a member of that organization.")
        return redirect("ui:choose_org")
    request.session[selectors.SESSION_KEY] = str(match.organization_id)
    return redirect("ui:home")


@login_required
@require_GET
def search(request):
    """Global search foundation (HTMX partial). Providers are permission-gated and tenant-scoped."""
    q = (request.GET.get("q") or "").strip()
    results = []
    m = request.membership
    if m is not None and len(q) >= 2:
        org = request.organization
        if rbac.has_permission(m, "user.view"):
            for mem in Membership.objects.for_organization(org).filter(
                Q(user__full_name__icontains=q) | Q(user__email__icontains=q)
            ).select_related("user")[:5]:
                results.append({"kind": "User", "label": mem.user.display_name, "sub": mem.user.email,
                                "url": f"/app/users/{mem.pk}/"})
        if rbac.has_permission(m, "role.view"):
            for r in Role.objects.for_organization(org).filter(name__icontains=q)[:5]:
                results.append({"kind": "Role", "label": r.name, "sub": r.description, "url": f"/app/roles/{r.pk}/"})
    return render(request, "ui/_search_results.html", {"results": results, "q": q})


def forbidden(request, exception=None):
    return render(request, "errors/403.html", status=403)


def not_found(request, exception=None):
    return render(request, "errors/404.html", status=404)


def server_error(request):
    return HttpResponse("Server error. Please quote the X-Request-ID header when reporting this.", status=500)
