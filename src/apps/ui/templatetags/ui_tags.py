import json

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

_ICONS = {
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "building": '<path d="M4 21V5a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v16M14 9h5a1 1 0 0 1 1 1v11M2 21h20M8 8h2M8 12h2M8 16h2"/>',
    "people": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.5 3-6 6.5-6s6.5 2.5 6.5 6M16 4.5a3.5 3.5 0 0 1 0 7M18 14c2.4.6 3.5 2.6 3.5 6"/>',
    "shield": '<path d="M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6l8-3z"/><path d="M9 12l2 2 4-4"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "bell": '<path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4l2-2zM10 21a2 2 0 0 0 4 0"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/>',
    "pin": '<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    "box": '<path d="M3 7.5L12 3l9 4.5v9L12 21l-9-4.5v-9z"/><path d="M3 7.5L12 12l9-4.5M12 12v9"/>',
    "list-check": '<path d="M9 6h11M9 12h11M9 18h11M3.5 6l1.5 1.5L7.5 5M3.5 12l1.5 1.5L7.5 11M3.5 18l1.5 1.5L7.5 17"/>',
    "clipboard": '<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4h6v3H9zM9 12h6M9 16h4"/>',
    "wrench": '<path d="M14.5 6.5a4 4 0 0 0 5 5L21 13l-8 8-4-4 8-8-2.5-2.5zM6 18l-3 3"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "package": '<line x1="16.5" y1="9.4" x2="7.5" y2="4.21"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
    "list": '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
    "hard-drive": '<line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/>',
    "refresh-cw": '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
    "alert-circle": '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>',
    "layers": '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
    "external-link": '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
    "align-justify": '<line x1="21" y1="10" x2="3" y2="10"/><line x1="21" y1="6" x2="3" y2="6"/><line x1="21" y1="14" x2="3" y2="14"/><line x1="21" y1="18" x2="3" y2="18"/>',
    "maximize-2": '<polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>',
    "minimize-2": '<polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/>',
    "info": '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "git-branch": '<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
    "trash-2": '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
    "eye": '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "x": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "folder": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    "tag": '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/>',
    "more-horizontal": '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    "arrow-up-right": '<line x1="7" y1="17" x2="17" y2="7"/><polyline points="7 7 17 7 17 17"/>',
}


@register.simple_tag
def icon(name, size=18):
    path = _ICONS.get(name, _ICONS["grid"])
    return format_html(
        '<svg class="fx-icon" width="{0}" height="{0}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{1}</svg>',
        size, mark_safe(path),
    )


_BADGES = {
    "ACTIVE": "success", "INVITED": "info", "SUSPENDED": "danger",
    "INACTIVE": "secondary", "UNDER_MAINTENANCE": "warning", "OUT_OF_SERVICE": "danger",
    "RETIRED": "secondary", "DISPOSED": "secondary", "INFO": "info", "SUCCESS": "success", "WARNING": "warning", "CRITICAL": "danger",
    # M05 requests / M06 work orders
    "NEW": "info", "TRIAGED": "info", "APPROVED": "success", "REJECTED": "danger", "WORK_ORDER_CREATED": "info",
    "IN_SERVICE": "warning", "RESOLVED": "success", "CONFIRMED": "success", "CLOSED": "secondary",
    "DRAFT": "secondary", "PLANNED": "info", "ASSIGNED": "info", "DISPATCHED": "info", "IN_PROGRESS": "warning",
    "ON_HOLD": "danger", "COMPLETED": "success", "SUPERVISOR_REVIEW": "warning", "CANCELLED": "secondary",
    "OPEN": "warning", "NOT_STARTED": "secondary",
    "LOW": "secondary", "MEDIUM": "info", "HIGH": "warning", "URGENT": "danger",
    # M09 part lines / reservations / movements
    "REQUESTED": "info", "RESERVED": "info", "ISSUED": "warning", "CONSUMED": "success", "RETURNED": "secondary",
    "RECONCILED": "secondary", "RELEASED": "secondary", "FULFILLED": "success", "RECEIPT": "success",
    "ISSUE": "warning", "RETURN": "info", "TRANSFER_OUT": "info", "TRANSFER_IN": "info", "ADJUSTMENT": "secondary",
    "RESERVE": "info", "RELEASE": "secondary",
    # M04 schedules / cycles
    "BREACHED": "danger", "MET": "success", "MET_LATE": "warning", "PENDING": "info", "PAUSED": "warning",
    "ACKNOWLEDGED": "info", "NOT_APPLICABLE": "secondary", "MAJOR": "warning", "MINOR": "info",
    "UPCOMING": "info", "EXPIRED": "danger", "COVERING": "success", "EXCLUDED": "warning", "RENEWED": "success",
    "DUE": "warning", "SCHEDULED": "info", "DISABLED": "secondary", "GENERATED": "info", "VERIFIED": "success",
}


@register.simple_tag
def status_badge(value):
    tone = _BADGES.get(str(value).upper(), "secondary")
    return format_html('<span class="badge fx-badge fx-badge-{}">{}</span>', tone, str(value).replace("_", " ").title())


@register.filter
def pretty_json(value):
    if value in (None, ""):
        return ""
    return json.dumps(value, indent=2, sort_keys=True, default=str)


@register.filter
def get_item(mapping, key):
    return mapping.get(key, "") if hasattr(mapping, "get") else ""


@register.simple_tag(takes_context=True)
def qs_without_page(context):
    """Query string of the current request minus ?page=, for pagination links."""
    request = context["request"]
    params = request.GET.copy()
    params.pop("page", None)
    encoded = params.urlencode()
    return f"&{encoded}" if encoded else ""


@register.simple_tag
def hierarchy_trail(asset):
    """Renders breadcrumb trail from root machine to child asset for maintenance and work order contexts."""
    if not asset:
        return ""
    from apps.assets import hierarchy
    try:
        ancs = list(reversed(hierarchy.ancestors(asset)))
    except Exception:
        ancs = []
    if not ancs:
        return ""
    items = []
    for a in ancs:
        items.append(f'<a href="/app/assets/{a.pk}/?tab=hierarchy" class="text-decoration-none">{a.asset_tag}</a>')
    items.append(f'<span class="fw-semibold text-dark">{asset.asset_tag}</span>')
    trail_html = ' <span class="text-muted mx-1">›</span> '.join(items)
    return format_html('<span class="fx-hierarchy-trail small text-body-secondary">Machine Path: {}</span>', mark_safe(trail_html))

