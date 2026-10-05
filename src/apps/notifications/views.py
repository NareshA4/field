from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.ui.mixins import TenantPermissionMixin

from . import services
from .models import Notification


class MemberOnlyMixin(TenantPermissionMixin):
    """Own notifications need no permission code: any ACTIVE member sees their own."""

    def dispatch(self, request, *args, **kwargs):
        self.required_permission = None
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.membership is None:
            return redirect("ui:choose_org")
        return View.dispatch(self, request, *args, **kwargs)


def _mine(request):
    return Notification.objects.for_organization(request.organization).filter(recipient=request.user)


class NotificationListView(MemberOnlyMixin, View):
    def get(self, request):
        from django.core.paginator import Paginator

        page = Paginator(_mine(request), 20).get_page(request.GET.get("page"))
        return render(request, "notifications/list.html", {"page": page})


class BellView(MemberOnlyMixin, View):
    """HTMX partial for the topbar bell."""

    def get(self, request):
        items = list(_mine(request)[:6])
        count = services.unread_count(request.organization, request.user)
        return render(request, "notifications/_bell.html", {"items": items, "unread": count})


class MarkReadView(MemberOnlyMixin, View):
    def post(self, request, pk):
        n = get_object_or_404(_mine(request), pk=pk)
        n.mark_read()
        target = n.link if n.link.startswith("/") and not n.link.startswith("//") else "notifications:list"
        return redirect(target)


class MarkAllReadView(MemberOnlyMixin, View):
    def post(self, request):
        services.mark_all_read(request.organization, request.user)
        return redirect("notifications:list")
