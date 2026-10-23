from django.urls import NoReverseMatch, reverse

from apps.rbac import services as rbac
from apps.rbac.models import MembershipRole
from apps.tenancy import selectors

from . import navigation
from .utils import is_client_membership


def _resolve_active(sections, req_path):
    """Marks the single most specific matching nav item as active."""
    best_item = None
    best_len = -1
    for sec, items in sections.items():
        for item in items:
            mp = item.get("match_prefix", "") or item.get("url", "")
            if mp == "/app/" or mp == "/app":
                if req_path.rstrip("/") == "/app" and len(mp) > best_len:
                    best_len = len(mp)
                    best_item = item
            elif mp and req_path.startswith(mp):
                if len(mp) > best_len:
                    best_len = len(mp)
                    best_item = item
    if best_item:
        best_item["active"] = True


def shell(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}

    req_path = request.path
    membership = getattr(request, "membership", None)

    # 1. Platform Super Admin (NAV-01) - Dedicated Governance Navigation
    if getattr(user, "is_platform_admin", False) and membership is None:
        sections = {
            "Platform Governance": [
                {"label": "Organizations & Tenants", "url": reverse("platform_admin:organizations"), "icon": "building", "match_prefix": "/platform/organizations"},
                {"label": "Platform Audit Trail", "url": reverse("platform_admin:audit"), "icon": "shield", "match_prefix": "/platform/audit"},
                {"label": "Incident Dashboard", "url": reverse("platform_admin:incident_dashboard"), "icon": "activity", "match_prefix": "/platform/incidents"},
            ]
        }
        _resolve_active(sections, req_path)
        return {
            "shell_org": None,
            "shell_membership": None,
            "shell_nav_sections": sections,
            "shell_memberships": [],
            "shell_tenant_error": None,
            "is_client": False,
        }

    if membership is None:
        return {
            "shell_org": None,
            "shell_membership": None,
            "shell_nav_sections": {},
            "shell_memberships": list(selectors.active_memberships(user)),
            "shell_tenant_error": getattr(request, "tenant_error", None),
            "is_client": False,
        }

    # 2. Client / Requester Navigation (M13)
    is_client = is_client_membership(membership)
    if is_client:
        sections = {
            "Portal": [
                {"label": "Client Portal", "url": reverse("portal:dashboard"), "icon": "grid", "match_prefix": "/app/portal/"},
                {"label": "My Requests", "url": reverse("portal:requests_list"), "icon": "list-check", "match_prefix": "/app/portal/requests"},
                {"label": "New Request", "url": reverse("portal:request_create"), "icon": "plus-circle", "match_prefix": "/app/portal/requests/new"},
            ]
        }
        _resolve_active(sections, req_path)
        return {
            "shell_org": getattr(request, "organization", None),
            "shell_membership": membership,
            "shell_nav_sections": sections,
            "shell_memberships": getattr(request, "active_memberships", None) or list(selectors.active_memberships(user)),
            "shell_tenant_error": getattr(request, "tenant_error", None),
            "is_client": True,
        }

    # 3. Internal Tenant Staff Roles - Clean Sidebar Allocation (Section H)
    role_keys = set(
        MembershipRole.objects.filter(membership=membership).values_list("role__system_key", flat=True)
    )
    is_owner = rbac.is_owner(membership)
    is_admin = "admin" in role_keys or rbac.has_permission(membership, "role.manage")
    sections = {}

    if is_owner or is_admin:
        sections["Overview"] = [
            {"label": "Operations Overview", "url": reverse("ui:home"), "icon": "grid", "match_prefix": "/app/"},
        ]
        sections["Administration"] = [
            {"label": "Organization", "url": reverse("tenancy:organization"), "icon": "building", "match_prefix": "/app/organization"},
            {"label": "Users", "url": reverse("tenancy:members"), "icon": "people", "match_prefix": "/app/users"},
            {"label": "Roles & Permissions", "url": reverse("rbac:roles"), "icon": "shield", "match_prefix": "/app/roles"},
            {"label": "Portal Accounts", "url": reverse("portal:accounts_list"), "icon": "people", "match_prefix": "/app/portal/accounts"},
        ]
        sections["Facilities Master"] = [
            {"label": "Sites & Locations", "url": reverse("sites:list"), "icon": "pin", "match_prefix": "/app/sites"},
        ]
        sections["Analytics & Governance"] = [
            {"label": "Operational Dashboards", "url": reverse("dashboards:hub"), "icon": "grid", "match_prefix": "/app/dashboards"},
            {"label": "Audit Trail", "url": reverse("audit:list"), "icon": "clock", "match_prefix": "/app/audit"},
        ]

    elif role_keys & {"technician", "field_technician"}:
        sections["Field Execution"] = [
            {"label": "My Assigned Jobs", "url": reverse("workspace:my_jobs"), "icon": "wrench", "match_prefix": "/app/workspace"},
        ]

    elif role_keys & {"stores_manager", "stores_inventory"}:
        sections["Inventory & Warehouses"] = [
            {"label": "Parts", "url": reverse("inventory:parts"), "icon": "box", "match_prefix": "/app/inventory/parts"},
            {"label": "Stock", "url": reverse("inventory:stock"), "icon": "grid", "match_prefix": "/app/inventory/stock"},
            {"label": "Warehouses", "url": reverse("inventory:warehouses"), "icon": "building", "match_prefix": "/app/inventory/warehouses"},
            {"label": "Reservations", "url": reverse("inventory:reservations"), "icon": "list-check", "match_prefix": "/app/inventory/reservations"},
            {"label": "Movements", "url": reverse("inventory:movements"), "icon": "clipboard", "match_prefix": "/app/inventory/movements"},
        ]
        if rbac.has_permission_anywhere(membership, "report.view"):
            sections["Analytics"] = [
                {"label": "Analytics Hub", "url": reverse("dashboards:hub"), "icon": "grid", "match_prefix": "/app/dashboards"},
            ]

    elif "asset_manager" in role_keys:
        sections["Asset Lifecycle"] = [
            {"label": "Sites & Locations", "url": reverse("sites:list"), "icon": "pin", "match_prefix": "/app/sites"},
            {"label": "Assets", "url": reverse("assets:list"), "icon": "box", "match_prefix": "/app/assets"},
            {"label": "Scan Label", "url": reverse("identification:scan"), "icon": "qr", "match_prefix": "/app/scan"},
        ]
        sections["Commercial"] = [
            {"label": "Warranties & contracts", "url": reverse("contracts:agreements"), "icon": "shield", "match_prefix": "/app/contracts/agreements"},
            {"label": "Expiring coverage", "url": reverse("contracts:expiry"), "icon": "clock", "match_prefix": "/app/contracts/expiry"},
        ]
        if rbac.has_permission_anywhere(membership, "report.view"):
            sections["Analytics"] = [
                {"label": "Analytics Hub", "url": reverse("dashboards:hub"), "icon": "grid", "match_prefix": "/app/dashboards"},
            ]

    elif "maintenance_planner" in role_keys:
        sections["Maintenance Planning"] = [
            {"label": "Maintenance plans", "url": reverse("maintenance:plans"), "icon": "wrench", "match_prefix": "/app/maintenance/plans"},
            {"label": "Due & upcoming", "url": reverse("maintenance:due"), "icon": "clock", "match_prefix": "/app/maintenance/due"},
            {"label": "PM history", "url": reverse("maintenance:history"), "icon": "list-check", "match_prefix": "/app/maintenance/history"},
        ]
        sections["Work Management"] = [
            {"label": "Work Orders", "url": reverse("workorders:list"), "icon": "clipboard-check", "match_prefix": "/app/work-orders"},
            {"label": "Checklists", "url": reverse("checklists:list"), "icon": "list-check", "match_prefix": "/app/checklists"},
        ]
        if rbac.has_permission_anywhere(membership, "report.view"):
            sections["Analytics"] = [
                {"label": "Analytics Hub", "url": reverse("dashboards:hub"), "icon": "grid", "match_prefix": "/app/dashboards"},
            ]

    elif "supervisor" in role_keys:
        sections["Supervisory Operations"] = [
            {"label": "Work Orders", "url": reverse("workorders:list"), "icon": "clipboard-check", "match_prefix": "/app/work-orders"},
            {"label": "Inspections", "url": reverse("checklists:inspections"), "icon": "clipboard", "match_prefix": "/app/inspections"},
            {"label": "Checklists", "url": reverse("checklists:list"), "icon": "list-check", "match_prefix": "/app/checklists"},
            {"label": "Incidents & Requests", "url": reverse("incidents:list"), "icon": "bell", "match_prefix": "/app/incidents"},
        ]
        if rbac.has_permission_anywhere(membership, "sla.view"):
            sections["Service Level Agreements"] = [
                {"label": "SLA & Escalation", "url": reverse("sla:trackings"), "icon": "clock", "match_prefix": "/app/sla/trackings"},
            ]
        if rbac.has_permission_anywhere(membership, "report.view"):
            sections["Analytics"] = [
                {"label": "Analytics Hub", "url": reverse("dashboards:hub"), "icon": "grid", "match_prefix": "/app/dashboards"},
            ]

    elif "service_dispatcher" in role_keys:
        sections["Operations"] = [
            {"label": "Operations Overview", "url": reverse("ui:home"), "icon": "grid", "match_prefix": "/app/"},
            {"label": "Incidents & Requests", "url": reverse("incidents:list"), "icon": "bell", "match_prefix": "/app/incidents"},
            {"label": "Work Orders", "url": reverse("workorders:list"), "icon": "clipboard-check", "match_prefix": "/app/work-orders"},
        ]
        sections["Service Level Agreements"] = [
            {"label": "SLA & Escalation", "url": reverse("sla:trackings"), "icon": "clock", "match_prefix": "/app/sla/trackings"},
        ]

    elif "service_manager" in role_keys:
        sections["Service Assurance"] = [
            {"label": "Incidents & Requests", "url": reverse("incidents:list"), "icon": "bell", "match_prefix": "/app/incidents"},
            {"label": "SLA & Escalation", "url": reverse("sla:trackings"), "icon": "clock", "match_prefix": "/app/sla/trackings"},
            {"label": "Warranties & contracts", "url": reverse("contracts:agreements"), "icon": "shield", "match_prefix": "/app/contracts/agreements"},
            {"label": "Analytics Hub", "url": reverse("dashboards:hub"), "icon": "grid", "match_prefix": "/app/dashboards"},
        ]

    elif role_keys & {"auditor", "compliance"}:
        sections["Audit & Compliance"] = [
            {"label": "Audit Trail", "url": reverse("audit:list"), "icon": "shield", "match_prefix": "/app/audit/"},
            {"label": "Compliance reports", "url": reverse("audit:reports"), "icon": "list-check", "match_prefix": "/app/audit/reports"},
            {"label": "Analytics Hub", "url": reverse("dashboards:hub"), "icon": "grid", "match_prefix": "/app/dashboards"},
        ]

    else:
        # Fallback for legacy operations_manager or custom composite roles
        sections["Overview"] = [
            {"label": "Operations Overview", "url": reverse("ui:home"), "icon": "grid", "match_prefix": "/app/"},
        ]
        if rbac.has_permission_anywhere(membership, "work_order.view"):
            sections.setdefault("Operations", []).append(
                {"label": "Work Orders", "url": reverse("workorders:list"), "icon": "clipboard-check", "match_prefix": "/app/work-orders"}
            )
        if rbac.has_permission_anywhere(membership, "incident.view"):
            sections.setdefault("Operations", []).append(
                {"label": "Incidents & Requests", "url": reverse("incidents:list"), "icon": "bell", "match_prefix": "/app/incidents"}
            )
        if rbac.has_permission_anywhere(membership, "asset.view"):
            sections.setdefault("Assets & Locations", []).append(
                {"label": "Sites & Locations", "url": reverse("sites:list"), "icon": "pin", "match_prefix": "/app/sites"}
            )
            sections.setdefault("Assets & Locations", []).append(
                {"label": "Assets", "url": reverse("assets:list"), "icon": "box", "match_prefix": "/app/assets"}
            )
        if rbac.has_permission_anywhere(membership, "sla.view"):
            sections["Service Level Agreements"] = [
                {"label": "SLA & Escalation", "url": reverse("sla:trackings"), "icon": "clock", "match_prefix": "/app/sla/trackings"},
            ]
        if rbac.has_permission_anywhere(membership, "report.view"):
            sections["Analytics"] = [
                {"label": "Analytics Hub", "url": reverse("dashboards:hub"), "icon": "grid", "match_prefix": "/app/dashboards"},
            ]

    _resolve_active(sections, req_path)

    return {
        "shell_org": getattr(request, "organization", None),
        "shell_membership": membership,
        "shell_nav_sections": sections,
        "shell_memberships": getattr(request, "active_memberships", None) or list(selectors.active_memberships(user)),
        "shell_tenant_error": getattr(request, "tenant_error", None),
        "is_client": is_client,
    }
