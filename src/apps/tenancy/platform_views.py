from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import render

from apps.audit.models import AuditLog

from .models import Organization


def platform_admin_required(view):
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_platform_admin:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped


@platform_admin_required
def organizations(request):
    page = Paginator(Organization.objects.order_by("name"), 25).get_page(request.GET.get("page"))
    return render(request, "tenancy/platform_organizations.html", {"page": page})


@platform_admin_required
def audit(request):
    page = Paginator(AuditLog.objects.select_related("organization").order_by("-occurred_at"), 50).get_page(
        request.GET.get("page")
    )
    return render(request, "tenancy/platform_audit.html", {"page": page})
