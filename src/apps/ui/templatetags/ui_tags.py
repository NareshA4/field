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
