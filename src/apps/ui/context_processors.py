from django.urls import NoReverseMatch, reverse

from apps.rbac import services as rbac
from apps.tenancy import selectors

from . import navigation


def shell(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    membership = getattr(request, "membership", None)
    nav = []
    if membership is not None:
        for item in navigation.items():
            if item.permission and not rbac.has_permission_anywhere(membership, item.permission):
                continue
            try:
                url = reverse(item.url_name)
            except NoReverseMatch:
                continue
            nav.append({
                "label": item.label, "url": url, "icon": item.icon, "section": item.section,
                "active": request.path.startswith(item.match_prefix) if item.match_prefix != "/app/"
                else request.path.rstrip("/") == "/app",
            })
    sections: dict[str, list] = {}
    for n in nav:
        sections.setdefault(n["section"], []).append(n)
    return {
        "shell_org": getattr(request, "organization", None),
        "shell_membership": membership,
        "shell_nav_sections": sections,
        "shell_memberships": getattr(request, "active_memberships", None) or list(selectors.active_memberships(user)),
        "shell_tenant_error": getattr(request, "tenant_error", None),
    }
