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
from .forms import ClientOnboardingForm
from .utils import is_client_membership


PRD_MODULES = [
    {"id": "FON-M01", "slug": "organization-management", "name": "Organization & Location Master", "category": "Master Data", "app": "apps.sites", "desc": "Multi-site facility hierarchy: Organization, Site, Building, Floor, Zone, and Room mappings."},
    {"id": "FON-M02", "slug": "asset-management", "name": "Asset Management", "category": "Asset Lifecycle", "app": "apps.assets", "desc": "Master asset records, categorization, documentation, telemetry readings, and depreciation."},
    {"id": "FON-M03", "slug": "asset-hierarchy", "name": "Asset Hierarchy", "category": "Asset Lifecycle", "app": "apps.assets", "desc": "Parent-child equipment relationships, lineage trees, and component swaps."},
    {"id": "FON-M04", "slug": "maintenance-management", "name": "Maintenance Management", "category": "Maintenance", "app": "apps.maintenance", "desc": "Time and meter-based recurrence schedules, seasonal blackouts, and auto work-order creation."},
    {"id": "FON-M05", "slug": "service-management", "name": "Service Management", "category": "Service Ops", "app": "apps.incidents", "desc": "Emergency breakdowns, service request triage, priority assignment, and work-order conversion."},
    {"id": "FON-M06", "slug": "work-order-management", "name": "Work Order Management", "category": "Execution", "app": "apps.workorders", "desc": "Comprehensive job lifecycle states: draft, planned, assigned, in-progress, hold, and closure."},
    {"id": "FON-M07", "slug": "technician-workspace", "name": "Technician Workspace", "category": "Execution", "app": "apps.workspace", "desc": "Field mobility interface for checklist execution, labor timesheets, parts usage, and photo evidence."},
    {"id": "FON-M08", "slug": "inspection-checklist", "name": "Inspection & Checklist", "category": "Quality & Safety", "app": "apps.checklists", "desc": "Dynamic inspection templates, pass/fail item criteria, and corrective finding logs."},
    {"id": "FON-M09", "slug": "inventory-management", "name": "Inventory Management", "category": "Supply Chain", "app": "apps.inventory", "desc": "Multi-warehouse bins, double-entry stock ledger, reservations, issuance, and reorder alerts."},
    {"id": "FON-M10", "slug": "contracts-warranty", "name": "Warranty / AMC / Contract", "category": "Commercial", "app": "apps.contracts", "desc": "Vendor agreements, warranty coverage checks, service entitlement, and expiration alerts."},
    {"id": "FON-M11", "slug": "sla-management", "name": "SLA & Escalation", "category": "Service Ops", "app": "apps.sla", "desc": "First-response and resolution deadlines, pause tracking during holds, and breach escalations."},
    {"id": "FON-M12", "slug": "qr-identification", "name": "QR / Barcode Identification", "category": "Identification", "app": "apps.identification", "desc": "Barcode/QR asset label generation, camera scanner lookup, and instant ticket logging."},
    {"id": "FON-M13", "slug": "client-portal", "name": "Client / Requester Portal", "category": "Self-Service", "app": "apps.portal", "desc": "Multi-tenant client portal for submitting tickets, tracking progress, and approving work completion."},
    {"id": "FON-M14", "slug": "analytics-reporting", "name": "Analytics & Reporting", "category": "Analytics", "app": "apps.dashboards", "desc": "Role-tailored KPI summaries: MTTR, MTBF, technician utilization, and PM compliance rates."},
    {"id": "FON-M15", "slug": "audit-traceability", "name": "Audit & Traceability", "category": "Governance", "app": "apps.audit", "desc": "Immutable, append-only security logs, state transition attributions, and compliance exports."}
]


def root(request):
    """Public landing page for visitors; authenticated users are routed to their workspace."""
    if request.user.is_authenticated:
        membership = getattr(request, "membership", None)
        if membership and is_client_membership(membership):
            return redirect("portal:dashboard")
        return redirect("ui:home")
    return render(request, "public/landing.html", {"active_page": "home"})


def public_features(request):
    return render(request, "public/features.html", {"active_page": "features"})


def public_solutions(request):
    return render(request, "public/solutions.html", {"active_page": "solutions"})


def public_solution(request, slug):
    return render(request, "public/solutions.html", {"active_page": "solutions", "selected_slug": slug})


def public_modules(request):
    return render(request, "public/modules.html", {"active_page": "modules", "module_list": PRD_MODULES})


def public_module(request, module_id):
    slug_lower = module_id.lower().strip()
    mod = next((m for m in PRD_MODULES if m.get("slug") == slug_lower or m["id"].lower() == slug_lower), None)
    return render(request, "public/modules.html", {"active_page": "modules", "module_list": PRD_MODULES, "selected_module": mod, "selected_slug": slug_lower})


def public_resources(request):
    return render(request, "public/resources.html", {"active_page": "resources"})


def public_about(request):
    return render(request, "public/about.html", {"active_page": "about"})


def client_register(request):
    """Public self-service client onboarding request. Audits and queues request safely without rogue privilege grant."""
    if request.user.is_authenticated:
        return redirect("ui:home")
    if request.method == "POST":
        form = ClientOnboardingForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"].strip().lower()
            company = form.cleaned_data["company_name"].strip()
            name = form.cleaned_data["full_name"].strip()
            AuditLog.objects.create(
                action="portal.client_onboarding_requested",
                target_repr=f"Client Request: {company} ({email})",
                metadata={
                    "full_name": name,
                    "email": email,
                    "company_name": company,
                    "phone": form.cleaned_data.get("phone", ""),
                    "address": form.cleaned_data.get("facility_address", ""),
                    "service_scope": form.cleaned_data.get("service_scope", ""),
                },
                actor_email=email,
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            return render(request, "accounts/client_register.html", {
                "submitted": True,
                "submitted_name": name,
                "submitted_company": company,
                "submitted_email": email,
            })
    else:
        form = ClientOnboardingForm()
    return render(request, "accounts/client_register.html", {"form": form})


def public_legal(request, slug):
    titles = {
        "privacy-policy": "Privacy Policy & Data Protection",
        "terms-and-conditions": "Terms and Conditions of Use",
        "disclaimer": "Enterprise System Disclaimer",
        "cookie-policy": "Cookie & Session Policy",
    }
    title = titles.get(slug, "Legal Notice")
    contents = {
        "privacy-policy": "<p>FieldOps Nexus enforces strict tenant-isolated data protection compliant with enterprise standards. User credentials and operational telemetry are encrypted and never shared cross-tenant.</p><p>Audit logs record user interactions solely for traceability, accountability, and compliance verification under HPE Delivery Oversight guidelines.</p>",
        "terms-and-conditions": "<p>This enterprise software platform is licensed under the governance of HPE-PRD-2026-FOPS02. All authorized users must adhere to organizational role boundaries and data access controls.</p><p>Unauthorized attempts to bypass tenant boundaries or tamper with audit records constitute strict grounds for immediate revocation and incident reporting.</p>",
        "disclaimer": "<p>This system operates as an Enterprise Asset, Maintenance & Field Service ERP. System specifications and operational workflows adhere to verified technical draft standards.</p><p>Final formal acceptance is subject to the mandatory 90-calendar-day evaluation gate.</p>",
        "cookie-policy": "<p>FieldOps Nexus utilizes strictly necessary session cookies (<code>sessionid</code> and <code>csrftoken</code>) configured with <code>HttpOnly</code> and <code>SameSite=Lax</code> protections to maintain authenticated sessions and protect against cross-site request forgery.</p>",
    }
    content = contents.get(slug, "<p>Legal terms and conditions apply as specified in project governance documentation.</p>")
    return render(request, "public/legal.html", {"active_page": "legal", "selected_slug": slug, "title": title, "content": content})


class ActivityEntry:
    def __init__(self, action: str, occurred_at, actor_email: str = "system", target_repr: str = ""):
        self.action = action
        self.occurred_at = occurred_at
        self.actor_email = actor_email
        self.target_repr = target_repr


ROLE_PERSONA_CONFIG = {
    "owner": {
        "badge": "System Administration & Governance",
        "title": "Operations & Enterprise Overview",
        "role_name": "Organization Owner",
        "subtitle": "Complete tenant governance, administrative oversight, and organizational metrics.",
    },
    "admin": {
        "badge": "System Administration & Governance",
        "title": "Operations & Governance Overview",
        "role_name": "Organization Admin",
        "subtitle": "System configuration, user administration, roles, and facility master records.",
    },
    "operations_manager": {
        "badge": "Field Operations & Strategy",
        "title": "Operations Management Overview",
        "role_name": "Head of Operations",
        "subtitle": "Holistic view of facility sites, equipment assets, work orders, and field operations.",
    },
    "asset_manager": {
        "badge": "Asset Lifecycle & Operations",
        "title": "Asset Management & Master Registry",
        "role_name": "Principal Asset Manager",
        "subtitle": "Physical equipment master, plant hierarchy, meter telemetry, and warranty agreements.",
    },
    "maintenance_planner": {
        "badge": "Maintenance Planning & PM",
        "title": "Preventive Maintenance & Job Planning",
        "role_name": "Senior Maintenance Planner",
        "subtitle": "Time and meter-based recurrence plans, job specifications, and PM work order backlog.",
    },
    "service_dispatcher": {
        "badge": "Service Dispatch & Operations",
        "title": "Service Dispatch & SLA Coordination",
        "role_name": "Service Dispatcher",
        "subtitle": "Emergency incident triage, technician assignments, dispatch board, and SLA countdowns.",
    },
    "technician": {
        "badge": "Field Mobility & Execution",
        "title": "Field Technician Workspace",
        "role_name": "Lead Field Technician",
        "subtitle": "Assigned job queue, dynamic inspection checklists, parts consumption, and labor tracking.",
    },
    "field_technician": {
        "badge": "Field Mobility & Execution",
        "title": "Field Technician Workspace",
        "role_name": "Lead Field Technician",
        "subtitle": "Assigned job queue, dynamic inspection checklists, parts consumption, and labor tracking.",
    },
    "supervisor": {
        "badge": "Maintenance Supervision & Oversight",
        "title": "Maintenance Supervision & Review Cockpit",
        "role_name": "Maintenance Supervisor",
        "subtitle": "Job completion sign-offs, closure approvals, safety findings, and SLA breach management.",
    },
    "stores_manager": {
        "badge": "Supply Chain & Warehouses",
        "title": "Stores & Spare Parts Inventory",
        "role_name": "Stores & Inventory Manager",
        "subtitle": "Part master catalog, multi-warehouse stock levels, reservations, and stock movements.",
    },
    "stores_inventory": {
        "badge": "Supply Chain & Warehouses",
        "title": "Stores & Spare Parts Inventory",
        "role_name": "Stores & Inventory Manager",
        "subtitle": "Part master catalog, multi-warehouse stock levels, reservations, and stock movements.",
    },
    "service_manager": {
        "badge": "Service Quality & Contract Management",
        "title": "Service Operations & SLA Management",
        "role_name": "Service & SLA Manager",
        "subtitle": "Contractual SLA targets, breach escalation review, customer portal, and AMC contracts.",
    },
    "auditor": {
        "badge": "Governance, Risk & Compliance",
        "title": "Compliance & Audit Traceability",
        "role_name": "Compliance & Audit Officer",
        "subtitle": "Immutable security ledger, state change attributions, inspection compliance, and reports.",
    },
    "compliance": {
        "badge": "Governance, Risk & Compliance",
        "title": "Compliance & Audit Traceability",
        "role_name": "Compliance & Audit Officer",
        "subtitle": "Immutable security ledger, state change attributions, inspection compliance, and reports.",
    },
}


@login_required
def home(request):
    """Org dashboard. Dynamically renders role-tailored metrics, shortcuts, and activity (PRD §3.2)."""
    from django.db.models import F
    from django.urls import reverse
    from apps.assets.models import Asset, AssetCategory, AssetComponent, AssetMeter
    from apps.checklists.models import ChecklistTemplate, Inspection
    from apps.contracts.models import CoverageAgreement
    from apps.incidents.models import ServiceRequest
    from apps.inventory.models import Part, PartReservation, StockBalance, StockMovement, Warehouse
    from apps.maintenance.models import MaintenancePlan
    from apps.portal.models import PortalAccount
    from apps.rbac.models import MembershipRole
    from apps.sites.models import Site
    from apps.sla.models import SLABreach
    from apps.workorders.models import WorkOrder

    if request.tenant_error:
        return render(request, "errors/tenant.html", {"code": request.tenant_error}, status=403)
    if request.membership is None:
        return redirect("ui:choose_org")
    if is_client_membership(request.membership):
        return redirect("portal:dashboard")

    org = request.organization
    perms = rbac.membership_permissions(request.membership)
    client_allowed = {
        "incident.report_owned", "incident.view_owned", "incident.view_assigned",
        "incident.attach", "incident.confirm"
    }
    if perms and all(p.startswith("portal.request.") or p in client_allowed for p in perms):
        return redirect("portal:dashboard")

    role_keys = set(
        MembershipRole.objects.filter(membership=request.membership).values_list("role__system_key", flat=True)
    )
    is_owner = rbac.is_owner(request.membership)
    is_admin = "admin" in role_keys or (
        rbac.has_permission(request.membership, "role.manage")
        or rbac.has_permission(request.membership, "organization.update")
    )

    # Resolve primary role persona
    if is_owner:
        persona_key = "owner"
    elif is_admin:
        persona_key = "admin"
    else:
        persona_order = [
            "operations_manager", "asset_manager", "maintenance_planner",
            "service_dispatcher", "supervisor", "stores_manager", "stores_inventory",
            "service_manager", "auditor", "compliance", "technician", "field_technician"
        ]
        persona_key = next((k for k in persona_order if k in role_keys), None)
        if not persona_key:
            if rbac.has_permission(request.membership, "inventory.view") and not rbac.has_permission(request.membership, "work_order.view"):
                persona_key = "stores_manager"
            elif rbac.has_permission(request.membership, "maintenance.view"):
                persona_key = "maintenance_planner"
            elif rbac.has_permission(request.membership, "asset.view") and not rbac.has_permission(request.membership, "work_order.view"):
                persona_key = "asset_manager"
            elif rbac.has_permission(request.membership, "work_order.view_assigned") and not rbac.has_permission(request.membership, "work_order.view"):
                persona_key = "technician"
            elif rbac.has_permission(request.membership, "audit.view"):
                persona_key = "auditor"
            else:
                persona_key = "operations_manager"

    persona = ROLE_PERSONA_CONFIG.get(persona_key, ROLE_PERSONA_CONFIG["operations_manager"])

    kpi_cards = []
    shortcuts = []
    header_actions = []
    recent = []
    activity_title = "Recent Operational Activity"
    activity_link = ""
    activity_link_label = ""

    # Standard stats mapping for backwards-compatibility
    can_view_users = rbac.has_permission(request.membership, "user.view")
    can_view_roles = rbac.has_permission(request.membership, "role.view")
    can_view_assets = rbac.has_permission(request.membership, "asset.view")
    can_view_sites = rbac.has_permission(request.membership, "site.view")
    can_view_work_orders = (
        rbac.has_permission(request.membership, "work_order.view")
        or rbac.has_permission(request.membership, "work_order.view_assigned")
    )

    stats = {
        "active_members": Membership.objects.for_organization(org).filter(status=Membership.Status.ACTIVE).count() if can_view_users else 0,
        "pending_invites": Membership.objects.for_organization(org).filter(status=Membership.Status.INVITED).count() if can_view_users else 0,
        "roles": Role.objects.for_organization(org).count() if can_view_roles else 0,
        "total_assets": Asset.objects.for_organization(org).exclude(status__in=["RETIRED", "DISPOSED"]).count() if can_view_assets else 0,
        "total_sites": Site.objects.for_organization(org).filter(status="ACTIVE").count() if can_view_sites else 0,
        "open_work_orders": WorkOrder.objects.for_organization(org).exclude(status__in=[WorkOrder.Status.COMPLETED, WorkOrder.Status.CANCELLED]).count() if can_view_work_orders else 0,
    }

    # Build Role-Tailored KPIs, Shortcuts, Actions, and Recent Activity
    if persona_key in ("owner", "admin"):
        kpi_cards = [
            {"title": "Active Users", "value": stats["active_members"], "subtitle": "Active tenant memberships", "tone": "accent", "icon": "people"},
            {"title": "Pending Invitations", "value": stats["pending_invites"], "subtitle": "Pending activation links", "tone": "warning", "icon": "clock"},
            {"title": "RBAC Roles", "value": stats["roles"], "subtitle": "Configured system & custom roles", "tone": "gold", "icon": "shield"},
            {"title": "Facility Sites", "value": stats["total_sites"], "subtitle": "Active physical plant sites", "tone": "accent", "icon": "building"},
        ]
        shortcuts = [
            {"label": "Users & Access", "url": reverse("tenancy:members"), "icon": "people"},
            {"label": "Roles & Permissions", "url": reverse("rbac:roles"), "icon": "shield"},
            {"label": "Facility Sites", "url": reverse("sites:list"), "icon": "building"},
            {"label": "Audit Trail", "url": reverse("audit:list"), "icon": "clock"},
        ]
        header_actions = [
            {"label": "Users", "url": reverse("tenancy:members"), "icon": "people"},
            {"label": "Audit Trail", "url": reverse("audit:list"), "icon": "shield"},
        ]
        if rbac.has_permission(request.membership, "audit.view"):
            recent = list(AuditLog.objects.for_organization(org)[:8])
            activity_title = "Recent Immutable Audit Activity"
            activity_link = reverse("audit:list")
            activity_link_label = "Full Audit Trail →"

    elif persona_key == "asset_manager":
        active_contracts = CoverageAgreement.objects.for_organization(org).filter(is_active=True).count() if rbac.has_permission(request.membership, "contract.view") else 0
        hierarchy_components = AssetComponent.objects.for_organization(org).count() if can_view_assets else 0
        active_meters = AssetMeter.objects.for_organization(org).filter(is_active=True).count() if can_view_assets else 0
        kpi_cards = [
            {"title": "Master Assets", "value": stats["total_assets"], "subtitle": "Operating plant & facility assets", "tone": "accent", "icon": "box"},
            {"title": "Facility Sites", "value": stats["total_sites"], "subtitle": "Active physical plant sites", "tone": "gold", "icon": "building"},
            {"title": "Hierarchy Components", "value": hierarchy_components, "subtitle": "Parent-child equipment hierarchy", "tone": "accent", "icon": "grid"},
            {"title": "Active Warranties/AMCs", "value": active_contracts, "subtitle": "Equipment coverage agreements", "tone": "warning", "icon": "shield"},
        ]
        shortcuts = [
            {"label": "Asset Registry", "url": reverse("assets:list"), "icon": "box"},
            {"label": "Asset Hierarchy", "url": reverse("assets:hierarchy_hub"), "icon": "grid"},
            {"label": "Stock Movements", "url": reverse("inventory:movements"), "icon": "clipboard"},
            {"label": "Warranties & Contracts", "url": reverse("contracts:agreements"), "icon": "shield"},
        ]
        header_actions = [
            {"label": "Asset Registry", "url": reverse("assets:list"), "icon": "box"},
            {"label": "Asset Hierarchy", "url": reverse("assets:hierarchy_hub"), "icon": "grid"},
            {"label": "Scan QR", "url": reverse("identification:scan"), "icon": "qr"},
        ]
        recent_objs = Asset.objects.for_organization(org).order_by("-updated_at")[:8]
        recent = [ActivityEntry(f"Asset [{a.status}]: {a.asset_tag}", a.updated_at or a.created_at, "operations", a.name) for a in recent_objs]
        activity_title = "Recent Asset Updates"
        activity_link = reverse("assets:list")
        activity_link_label = "Asset Registry →"

    elif persona_key == "maintenance_planner":
        active_pms = MaintenancePlan.objects.for_organization(org).filter(is_active=True).count()
        prev_wos = WorkOrder.objects.for_organization(org).filter(work_type="PREVENTIVE").exclude(status__in=["COMPLETED", "CANCELLED"]).count()
        backlog = WorkOrder.objects.for_organization(org).filter(status__in=["DRAFT", "PLANNED"]).count()
        checklists = ChecklistTemplate.objects.for_organization(org).count()
        kpi_cards = [
            {"title": "Active PM Plans", "value": active_pms, "subtitle": "Active recurrence plans", "tone": "accent", "icon": "wrench"},
            {"title": "Preventive Orders", "value": prev_wos, "subtitle": "Scheduled active PM jobs", "tone": "gold", "icon": "clipboard"},
            {"title": "Planning Backlog", "value": backlog, "subtitle": "Orders awaiting dispatch", "tone": "warning", "icon": "clock"},
            {"title": "Inspection Checklists", "value": checklists, "subtitle": "Configured templates", "tone": "accent", "icon": "list-check"},
        ]
        shortcuts = [
            {"label": "Maintenance Plans", "url": reverse("maintenance:plans"), "icon": "wrench"},
            {"label": "Due & Upcoming PMs", "url": reverse("maintenance:due"), "icon": "clock"},
            {"label": "Work Order Board", "url": reverse("workorders:list"), "icon": "clipboard"},
            {"label": "Inspection Checklists", "url": reverse("checklists:list"), "icon": "list-check"},
        ]
        header_actions = [
            {"label": "Maintenance Plans", "url": reverse("maintenance:plans"), "icon": "wrench"},
            {"label": "Work Orders", "url": reverse("workorders:list"), "icon": "clipboard"},
        ]
        recent_objs = WorkOrder.objects.for_organization(org).filter(work_type="PREVENTIVE").order_by("-created_at")[:8]
        recent = [ActivityEntry(f"PM Work Order [{w.status}]: {w.order_number}", w.updated_at or w.created_at, getattr(w.assigned_to.user, "email", "unassigned") if w.assigned_to else "unassigned", w.title) for w in recent_objs]
        activity_title = "Recent Preventive Work Orders"
        activity_link = reverse("maintenance:due")
        activity_link_label = "Due Maintenance →"

    elif persona_key == "service_dispatcher":
        open_reqs = ServiceRequest.objects.for_organization(org).exclude(status__in=["CLOSED", "REJECTED", "CANCELLED"]).count()
        unassigned = WorkOrder.objects.for_organization(org).filter(assigned_to__isnull=True).exclude(status__in=["COMPLETED", "CANCELLED"]).count()
        dispatched = WorkOrder.objects.for_organization(org).filter(status__in=["DISPATCHED", "IN_PROGRESS"]).count()
        breaches = SLABreach.objects.for_organization(org).count()
        kpi_cards = [
            {"title": "Open Incidents", "value": open_reqs, "subtitle": "Unresolved breakdown tickets", "tone": "warning", "icon": "bell"},
            {"title": "Unassigned Jobs", "value": unassigned, "subtitle": "Orders requiring dispatch", "tone": "accent", "icon": "clipboard"},
            {"title": "Dispatched Orders", "value": dispatched, "subtitle": "Field jobs in execution", "tone": "gold", "icon": "activity"},
            {"title": "SLA Breaches", "value": breaches, "subtitle": "Missed response / resolution targets", "tone": "danger", "icon": "clock"},
        ]
        shortcuts = [
            {"label": "Incidents & Requests", "url": reverse("incidents:list"), "icon": "bell"},
            {"label": "Work Order Board", "url": reverse("workorders:list"), "icon": "clipboard"},
            {"label": "SLA & Escalation", "url": reverse("sla:trackings"), "icon": "clock"},
            {"label": "Service Analytics", "url": reverse("dashboards:hub") + "?section=service", "icon": "activity"},
        ]
        header_actions = [
            {"label": "Incidents", "url": reverse("incidents:list"), "icon": "bell"},
            {"label": "Work Orders", "url": reverse("workorders:list"), "icon": "clipboard"},
        ]
        recent_objs = ServiceRequest.objects.for_organization(org).order_by("-created_at")[:8]
        recent = [ActivityEntry(f"Service Request [{r.status}]: {r.number}", r.created_at, getattr(r.reported_by.user, "email", "system") if r.reported_by else "system", r.title) for r in recent_objs]
        activity_title = "Recent Incident & Service Calls"
        activity_link = reverse("incidents:list")
        activity_link_label = "All Incidents →"

    elif persona_key in ("technician", "field_technician"):
        my_assigned = WorkOrder.objects.for_organization(org).filter(assigned_to=request.membership).exclude(status__in=["COMPLETED", "CANCELLED"]).count()
        my_active = WorkOrder.objects.for_organization(org).filter(assigned_to=request.membership, status="IN_PROGRESS").count()
        my_completed = WorkOrder.objects.for_organization(org).filter(assigned_to=request.membership, status__in=["COMPLETED", "CLOSED"]).count()
        my_inspections = Inspection.objects.for_organization(org).filter(Q(started_by=request.membership) | Q(completed_by=request.membership)).count()
        kpi_cards = [
            {"title": "My Active Jobs", "value": my_assigned, "subtitle": "Current jobs assigned to you", "tone": "accent", "icon": "wrench"},
            {"title": "In Progress", "value": my_active, "subtitle": "Work orders currently clocked in", "tone": "warning", "icon": "activity"},
            {"title": "Completed Jobs", "value": my_completed, "subtitle": "Successfully resolved orders", "tone": "gold", "icon": "clipboard-check"},
            {"title": "My Inspections", "value": my_inspections, "subtitle": "Checklist audits conducted", "tone": "accent", "icon": "list-check"},
        ]
        shortcuts = [
            {"label": "Technician Workspace", "url": reverse("workspace:my_jobs"), "icon": "wrench"},
            {"label": "My Work Cockpit", "url": reverse("dashboards:my_work"), "icon": "activity"},
            {"label": "Scan Asset Label", "url": reverse("identification:scan"), "icon": "qr"},
            {"label": "Checklist Inspections", "url": reverse("checklists:inspections"), "icon": "list-check"},
        ]
        header_actions = [
            {"label": "My Jobs", "url": reverse("workspace:my_jobs"), "icon": "wrench"},
            {"label": "Scan QR", "url": reverse("identification:scan"), "icon": "qr"},
        ]
        recent_objs = WorkOrder.objects.for_organization(org).filter(assigned_to=request.membership).order_by("-updated_at")[:8]
        recent = [ActivityEntry(f"Work Order [{w.status}]: {w.order_number}", w.updated_at or w.created_at, request.user.email, w.title) for w in recent_objs]
        activity_title = "My Assigned Job Activity"
        activity_link = reverse("workspace:my_jobs")
        activity_link_label = "Technician Workspace →"

    elif persona_key == "supervisor":
        pending_rev = WorkOrder.objects.for_organization(org).filter(status="COMPLETED").count()
        active_orders = WorkOrder.objects.for_organization(org).filter(status__in=["DISPATCHED", "IN_PROGRESS", "ON_HOLD"]).count()
        breaches = SLABreach.objects.for_organization(org).count()
        inspections = Inspection.objects.for_organization(org).count()
        kpi_cards = [
            {"title": "Pending Review", "value": pending_rev, "subtitle": "Completed jobs awaiting closure", "tone": "warning", "icon": "clipboard-check"},
            {"title": "Active Field Jobs", "value": active_orders, "subtitle": "Work orders currently in execution", "tone": "accent", "icon": "clipboard"},
            {"title": "SLA Breaches", "value": breaches, "subtitle": "Response / resolution violations", "tone": "danger", "icon": "alert-triangle"},
            {"title": "Inspections Conducted", "value": inspections, "subtitle": "Quality & safety inspections", "tone": "gold", "icon": "shield"},
        ]
        shortcuts = [
            {"label": "Work Order Review Board", "url": reverse("workorders:list"), "icon": "clipboard-check"},
            {"label": "Incidents & Requests", "url": reverse("incidents:list"), "icon": "bell"},
            {"label": "SLA & Escalation", "url": reverse("sla:trackings"), "icon": "clock"},
            {"label": "Operational Dashboards", "url": reverse("dashboards:hub"), "icon": "grid"},
        ]
        header_actions = [
            {"label": "Work Orders", "url": reverse("workorders:list"), "icon": "clipboard"},
            {"label": "Dashboards", "url": reverse("dashboards:hub"), "icon": "grid"},
        ]
        recent_objs = WorkOrder.objects.for_organization(org).order_by("-updated_at")[:8]
        recent = [ActivityEntry(f"Work Order [{w.status}]: {w.order_number}", w.updated_at or w.created_at, getattr(w.assigned_to.user, "email", "system") if w.assigned_to else "system", w.title) for w in recent_objs]
        activity_title = "Recent Field Job Transitions"
        activity_link = reverse("workorders:list")
        activity_link_label = "All Work Orders →"

    elif persona_key in ("stores_manager", "stores_inventory"):
        total_parts = Part.objects.for_organization(org).count()
        low_stock = StockBalance.objects.for_organization(org).filter(min_level__isnull=False, on_hand__lte=F("min_level")).count()
        warehouses = Warehouse.objects.for_organization(org).count()
        reservations = PartReservation.objects.for_organization(org).filter(status="ACTIVE").count()
        kpi_cards = [
            {"title": "Cataloged Parts", "value": total_parts, "subtitle": "Active spare part SKU catalog", "tone": "accent", "icon": "box"},
            {"title": "Low Stock Alerts", "value": low_stock, "subtitle": "Items below reorder point", "tone": "danger", "icon": "alert-triangle"},
            {"title": "Storage Warehouses", "value": warehouses, "subtitle": "Active storage locations", "tone": "gold", "icon": "building"},
            {"title": "Active Reservations", "value": reservations, "subtitle": "Parts allocated to orders", "tone": "warning", "icon": "clipboard"},
        ]
        shortcuts = [
            {"label": "Spare Parts Catalog", "url": reverse("inventory:parts"), "icon": "box"},
            {"label": "Warehouse Stock", "url": reverse("inventory:stock"), "icon": "grid"},
            {"label": "Stock Reservations", "url": reverse("inventory:reservations"), "icon": "list-check"},
            {"label": "Stock Movements", "url": reverse("inventory:movements"), "icon": "clipboard"},
        ]
        header_actions = [
            {"label": "Parts Catalog", "url": reverse("inventory:parts"), "icon": "box"},
            {"label": "Stock Ledger", "url": reverse("inventory:stock"), "icon": "grid"},
        ]
        recent_objs = StockMovement.objects.for_organization(org).select_related("part").order_by("-created_at")[:8]
        recent = [ActivityEntry(f"Stock Movement [{m.movement_type}]: {m.quantity} units", m.created_at, getattr(m.actor, "email", "warehouse") if m.actor else "warehouse", m.part.name if m.part else "") for m in recent_objs]
        activity_title = "Recent Inventory & Stock Movements"
        activity_link = reverse("inventory:movements")
        activity_link_label = "Movement Ledger →"

    elif persona_key == "service_manager":
        open_reqs = ServiceRequest.objects.for_organization(org).exclude(status__in=["CLOSED", "REJECTED", "CANCELLED"]).count()
        breaches = SLABreach.objects.for_organization(org).count()
        contracts = CoverageAgreement.objects.for_organization(org).filter(is_active=True).count()
        portal_accs = PortalAccount.objects.for_organization(org).count()
        kpi_cards = [
            {"title": "Open Service Requests", "value": open_reqs, "subtitle": "Active customer incident calls", "tone": "warning", "icon": "bell"},
            {"title": "SLA Breaches", "value": breaches, "subtitle": "SLA commitment violations", "tone": "danger", "icon": "clock"},
            {"title": "Active AMC Contracts", "value": contracts, "subtitle": "Active coverage agreements", "tone": "accent", "icon": "shield"},
            {"title": "Client Accounts", "value": portal_accs, "subtitle": "Client portal accounts", "tone": "gold", "icon": "people"},
        ]
        shortcuts = [
            {"label": "Incidents & Requests", "url": reverse("incidents:list"), "icon": "bell"},
            {"label": "SLA & Escalation", "url": reverse("sla:trackings"), "icon": "clock"},
            {"label": "Warranties & Contracts", "url": reverse("contracts:agreements"), "icon": "shield"},
            {"label": "Portal Accounts", "url": reverse("portal:accounts_list"), "icon": "people"},
        ]
        header_actions = [
            {"label": "Incidents", "url": reverse("incidents:list"), "icon": "bell"},
            {"label": "SLA Policies", "url": reverse("sla:trackings"), "icon": "clock"},
        ]
        recent_objs = ServiceRequest.objects.for_organization(org).order_by("-created_at")[:8]
        recent = [ActivityEntry(f"Service Request [{r.status}]: {r.number}", r.created_at, getattr(r.reported_by.user, "email", "system") if r.reported_by else "system", r.title) for r in recent_objs]
        activity_title = "Recent Incident & Service Escalations"
        activity_link = reverse("incidents:list")
        activity_link_label = "All Incidents →"

    elif persona_key in ("auditor", "compliance"):
        total_audits = AuditLog.objects.for_organization(org).count()
        closed_wos = WorkOrder.objects.for_organization(org).filter(status="CLOSED").count()
        breaches = SLABreach.objects.for_organization(org).count()
        inspections = Inspection.objects.for_organization(org).count()
        kpi_cards = [
            {"title": "Immutable Audit Logs", "value": total_audits, "subtitle": "Tamper-evident log entries", "tone": "accent", "icon": "shield"},
            {"title": "Closed Work Orders", "value": closed_wos, "subtitle": "Archived completed orders", "tone": "gold", "icon": "clipboard-check"},
            {"title": "SLA Breaches Audited", "value": breaches, "subtitle": "Contractual SLA exceptions", "tone": "warning", "icon": "clock"},
            {"title": "Inspections Logged", "value": inspections, "subtitle": "Safety & compliance audits", "tone": "accent", "icon": "list-check"},
        ]
        shortcuts = [
            {"label": "Immutable Audit Trail", "url": reverse("audit:list"), "icon": "shield"},
            {"label": "Compliance Reports", "url": reverse("audit:reports"), "icon": "list-check"},
            {"label": "Asset History", "url": reverse("assets:list"), "icon": "box"},
            {"label": "Work Order Ledger", "url": reverse("workorders:list"), "icon": "clipboard"},
        ]
        header_actions = [
            {"label": "Audit Trail", "url": reverse("audit:list"), "icon": "shield"},
            {"label": "Compliance Reports", "url": reverse("audit:reports"), "icon": "list-check"},
        ]
        recent = list(AuditLog.objects.for_organization(org)[:8])
        activity_title = "Recent Immutable Audit Events"
        activity_link = reverse("audit:list")
        activity_link_label = "Full Audit Trail →"

    else:  # operations_manager / fallback
        open_incidents = ServiceRequest.objects.for_organization(org).exclude(status__in=["CLOSED", "REJECTED", "CANCELLED"]).count() if rbac.has_permission(request.membership, "incident.view") else 0
        kpi_cards = [
            {"title": "Master Assets", "value": stats["total_assets"], "subtitle": "Operating plant & facility assets", "tone": "accent", "icon": "box"},
            {"title": "Facility Sites", "value": stats["total_sites"], "subtitle": "Active physical plant sites", "tone": "gold", "icon": "building"},
            {"title": "Active Work Orders", "value": stats["open_work_orders"], "subtitle": "Scheduled & in-progress jobs", "tone": "warning", "icon": "clipboard"},
            {"title": "Open Incidents", "value": open_incidents, "subtitle": "Active failure calls", "tone": "accent", "icon": "bell"},
        ]
        shortcuts = [
            {"label": "Sites & Locations", "url": reverse("sites:list"), "icon": "building"},
            {"label": "Master Assets", "url": reverse("assets:list"), "icon": "box"},
            {"label": "Work Order Board", "url": reverse("workorders:list"), "icon": "clipboard"},
            {"label": "Spare Parts Catalog", "url": reverse("inventory:parts"), "icon": "box"},
        ]
        header_actions = [
            {"label": "Work Orders", "url": reverse("workorders:list"), "icon": "clipboard"},
            {"label": "Asset Registry", "url": reverse("assets:list"), "icon": "box"},
        ]
        if rbac.has_permission(request.membership, "audit.view"):
            recent = list(AuditLog.objects.for_organization(org)[:8])
            activity_title = "Recent Immutable Audit Activity"
            activity_link = reverse("audit:list")
            activity_link_label = "Full Audit Trail →"
        else:
            recent_objs = WorkOrder.objects.for_organization(org).order_by("-created_at")[:8]
            recent = [ActivityEntry(f"Work Order [{w.status}]: {w.order_number}", w.updated_at or w.created_at, getattr(w.assigned_to.user, "email", "system") if w.assigned_to else "system", w.title) for w in recent_objs]
            activity_title = "Recent Work Orders"
            activity_link = reverse("workorders:list")
            activity_link_label = "Work Order Board →"

    # Security Filter: ensure all shortcuts and header actions only include URLs the user has permission to visit
    filtered_shortcuts = []
    for s in shortcuts:
        u = s["url"]
        if "/app/organization" in u or "/app/users" in u or "/app/roles" in u:
            if not can_view_users:
                continue
        if "/app/audit" in u and not rbac.has_permission(request.membership, "audit.view"):
            continue
        if "/app/inventory" in u and not (rbac.has_permission(request.membership, "inventory.view") or rbac.has_permission(request.membership, "inventory.part.view")):
            continue
        if "/app/contracts" in u and not rbac.has_permission(request.membership, "contract.view"):
            continue
        if "/app/maintenance" in u and not rbac.has_permission(request.membership, "maintenance.view"):
            continue
        if "/app/incidents" in u and not (rbac.has_permission(request.membership, "incident.view") or rbac.has_permission(request.membership, "incident.view_assigned")):
            continue
        if "/app/sla" in u and not rbac.has_permission(request.membership, "sla.view"):
            continue
        filtered_shortcuts.append(s)

    filtered_headers = []
    for h in header_actions:
        u = h["url"]
        if "/app/users" in u and not can_view_users:
            continue
        if "/app/audit" in u and not rbac.has_permission(request.membership, "audit.view"):
            continue
        if "/app/assets" in u and not can_view_assets:
            continue
        if "/app/work-orders" in u and not can_view_work_orders:
            continue
        if "/app/inventory" in u and not (rbac.has_permission(request.membership, "inventory.part.view") or rbac.has_permission(request.membership, "inventory.view")):
            continue
        if "/app/maintenance" in u and not rbac.has_permission(request.membership, "maintenance.view"):
            continue
        if "/app/incidents" in u and not rbac.has_permission(request.membership, "incident.view"):
            continue
        filtered_headers.append(h)

    return render(request, "ui/home.html", {
        "persona": persona,
        "kpi_cards": kpi_cards,
        "shortcuts": filtered_shortcuts,
        "header_actions": filtered_headers,
        "stats": stats,
        "recent": recent,
        "activity_title": activity_title,
        "activity_link": activity_link,
        "activity_link_label": activity_link_label,
        "can_view_users": can_view_users,
        "can_view_assets": can_view_assets,
    })


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

