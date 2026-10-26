from django.urls import NoReverseMatch, reverse

from apps.rbac import services as rbac
from apps.tenancy import selectors

from . import navigation
from .utils import is_client_membership


def shell(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    membership = getattr(request, "membership", None)
    nav = []
    is_client = False
    if membership is not None:
        is_client = is_client_membership(membership)
        raw_items = []
        for item in navigation.items():
            if is_client:
                # Client user should ONLY see client sub-modules (Portal section, excluding staff portal.manage)
                if item.section != "Portal" or item.permission == "portal.manage":
                    continue
            else:
                # Internal staff should NOT see client portal requester navigation items
                if item.section == "Portal":
                    continue
                if item.permission and not rbac.has_permission_anywhere(membership, item.permission):
                    continue
                if item.url_name in ("workspace:my_jobs", "workorders:mine"):
                    is_tech = bool(
                        membership.membership_roles.filter(role__system_key="technician").exists()
                        or (
                            not rbac.has_permission_anywhere(membership, "work_order.view")
                            and rbac.has_permission_anywhere(membership, "work_order.view_assigned")
                        )
                    )
                    if not is_tech:
                        continue

            try:
                url = reverse(item.url_name)
            except NoReverseMatch:
                continue
            raw_items.append((item, url))

        # Select the single most specific matching nav item
        active_url_name = None
        best_len = -1
        req_path = request.path
        for item, _ in raw_items:
            mp = item.match_prefix
            if mp == "/app/":
                if req_path.rstrip("/") == "/app" and len(mp) > best_len:
                    best_len = len(mp)
                    active_url_name = item.url_name
            elif mp and req_path.startswith(mp):
                if len(mp) > best_len:
                    best_len = len(mp)
                    active_url_name = item.url_name

        for item, url in raw_items:
            nav.append({
                "label": item.label, "url": url, "icon": item.icon, "section": item.section,
                "active": (item.url_name == active_url_name),
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
        "is_client": is_client,
    }
