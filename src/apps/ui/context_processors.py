from django.urls import NoReverseMatch, reverse

from apps.rbac import services as rbac
from apps.rbac.models import MembershipRole
from apps.tenancy import selectors

from . import navigation
from .utils import is_client_membership

# Role-Tailored Navigation Rules (PRD §3.2 & §3.3)
ROLE_NAV_RULES = {
    "admin": {
        "allowed_sections": {"Overview", "Administration", "Assets & Locations", "Analytics", "Compliance"},
        "allowed_url_names": {
            "ui:home", "tenancy:organization", "tenancy:members", "rbac:roles",
            "sites:list", "assets:list", "identification:scan", "dashboards:hub",
            "audit:list", "audit:reports",
        },
    },
    "asset_manager": {
        "allowed_sections": {"Overview", "Assets & Locations", "Coverage", "Inventory", "Analytics"},
        "allowed_url_names": {
            "ui:home", "assets:list", "sites:list", "identification:scan",
            "contracts:agreements", "contracts:expiry", "contracts:providers",
            "inventory:movements", "dashboards:hub",
        },
    },
    "maintenance_planner": {
        "allowed_sections": {"Overview", "Maintenance", "Operations", "Assets & Locations", "Analytics"},
        "allowed_url_names": {
            "ui:home", "maintenance:plans", "maintenance:due", "maintenance:history",
            "workorders:list", "checklists:list", "assets:list", "dashboards:hub",
        },
    },
    "service_dispatcher": {
        "allowed_sections": {"Overview", "Operations", "SLA & Escalation", "Assets & Locations", "Analytics"},
        "allowed_url_names": {
            "ui:home", "incidents:list", "workorders:list", "sla:trackings",
            "sites:list", "dashboards:hub",
        },
    },
    "technician": {
        "allowed_sections": {"Overview", "Operations", "Assets & Locations"},
        "allowed_url_names": {
            "ui:home", "workspace:my_jobs", "dashboards:my_work",
            "checklists:inspections", "identification:scan",
        },
    },
    "field_technician": {
        "allowed_sections": {"Overview", "Operations", "Assets & Locations"},
        "allowed_url_names": {
            "ui:home", "workspace:my_jobs", "dashboards:my_work",
            "checklists:inspections", "identification:scan",
        },
    },
    "supervisor": {
        "allowed_sections": {"Overview", "Operations", "SLA & Escalation", "Analytics"},
        "allowed_url_names": {
            "ui:home", "workorders:list", "incidents:list", "checklists:inspections",
            "checklists:list", "sla:trackings", "dashboards:hub",
        },
    },
    "stores_manager": {
        "allowed_sections": {"Overview", "Inventory", "Assets & Locations", "Analytics"},
        "allowed_url_names": {
            "ui:home", "inventory:parts", "inventory:stock", "inventory:warehouses",
            "inventory:reservations", "inventory:movements", "assets:list", "dashboards:hub",
        },
    },
    "stores_inventory": {
        "allowed_sections": {"Overview", "Inventory", "Assets & Locations", "Analytics"},
        "allowed_url_names": {
            "ui:home", "inventory:parts", "inventory:stock", "inventory:warehouses",
            "inventory:reservations", "inventory:movements", "assets:list", "dashboards:hub",
        },
    },
    "service_manager": {
        "allowed_sections": {"Overview", "Operations", "SLA & Escalation", "Coverage", "Analytics", "Administration"},
        "allowed_url_names": {
            "ui:home", "incidents:list", "sla:trackings", "contracts:agreements",
            "contracts:providers", "portal:accounts_list", "dashboards:hub",
        },
    },
    "auditor": {
        "allowed_sections": {"Overview", "Compliance", "Assets & Locations", "Operations", "Analytics"},
        "allowed_url_names": {
            "ui:home", "audit:list", "audit:reports", "assets:list",
            "workorders:list", "incidents:list", "dashboards:hub",
        },
    },
    "compliance": {
        "allowed_sections": {"Overview", "Compliance", "Assets & Locations", "Operations", "Analytics"},
        "allowed_url_names": {
            "ui:home", "audit:list", "audit:reports", "assets:list",
            "workorders:list", "incidents:list", "dashboards:hub",
        },
    },
    "operations_manager": {
        "allowed_sections": {"Overview", "Operations", "Assets & Locations", "Maintenance", "SLA & Escalation", "Analytics"},
        "allowed_url_names": {
            "ui:home", "workorders:list", "incidents:list", "checklists:list", "checklists:inspections",
            "sites:list", "assets:list", "maintenance:plans", "maintenance:due", "sla:trackings",
            "dashboards:hub",
        },
    },
}


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

        # Resolve role persona tailoring
        role_keys = set(
            MembershipRole.objects.filter(membership=membership).values_list("role__system_key", flat=True)
        )
        is_owner = rbac.is_owner(membership) or "owner" in role_keys
        allowed_sections = None
        allowed_url_names = None

        if not is_owner and role_keys:
            matched_rules = [ROLE_NAV_RULES[k] for k in role_keys if k in ROLE_NAV_RULES]
            if matched_rules:
                allowed_sections = set()
                allowed_url_names = set()
                for rule in matched_rules:
                    if "allowed_sections" in rule:
                        allowed_sections.update(rule["allowed_sections"])
                    if "allowed_url_names" in rule:
                        allowed_url_names.update(rule["allowed_url_names"])

        for item in navigation.items():
            if is_client:
                # Client user should ONLY see client sub-modules (Portal section, excluding staff portal.manage)
                if item.section != "Portal" or item.permission == "portal.manage":
                    continue
            else:
                # Internal staff should NOT see client portal requester navigation items
                if item.section == "Portal":
                    continue
                # Permission check
                if item.permission and not rbac.has_permission_anywhere(membership, item.permission):
                    continue
                # Role-Tailored Section & URL Scoping
                if allowed_sections is not None and item.section not in allowed_sections:
                    continue
                if allowed_url_names is not None and item.url_name not in allowed_url_names:
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
