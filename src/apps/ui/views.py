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
    {"id": "FON-M01", "name": "Site & Location Master", "category": "Master Data", "app": "apps.sites", "desc": "Multi-site facility hierarchy: Organization, Site, Building, Floor, Zone, and Room mappings."},
    {"id": "FON-M02", "name": "Asset Registry", "category": "Asset Lifecycle", "app": "apps.assets", "desc": "Master asset records, categorization, documentation, telemetry readings, and depreciation."},
    {"id": "FON-M03", "name": "Asset Hierarchy", "category": "Asset Lifecycle", "app": "apps.assets", "desc": "Parent-child equipment relationships, lineage trees, and component swaps."},
    {"id": "FON-M04", "name": "Preventive Maintenance", "category": "Maintenance", "app": "apps.maintenance", "desc": "Time and meter-based recurrence schedules, seasonal blackouts, and auto work-order creation."},
    {"id": "FON-M05", "name": "Incident / Breakdown", "category": "Service Ops", "app": "apps.incidents", "desc": "Emergency breakdowns, service request triage, priority assignment, and work-order conversion."},
    {"id": "FON-M06", "name": "Work Order Management", "category": "Execution", "app": "apps.workorders", "desc": "Comprehensive job lifecycle states: draft, planned, assigned, in-progress, hold, and closure."},
    {"id": "FON-M07", "name": "Technician Workspace", "category": "Execution", "app": "apps.workspace", "desc": "Field mobility interface for checklist execution, labor timesheets, parts usage, and photo evidence."},
    {"id": "FON-M08", "name": "Inspection & Checklist", "category": "Quality & Safety", "app": "apps.checklists", "desc": "Dynamic inspection templates, pass/fail item criteria, and corrective finding logs."},
    {"id": "FON-M09", "name": "Spare Parts & Inventory", "category": "Supply Chain", "app": "apps.inventory", "desc": "Multi-warehouse bins, double-entry stock ledger, reservations, issuance, and reorder alerts."},
    {"id": "FON-M10", "name": "Warranty / AMC / Contract", "category": "Commercial", "app": "apps.contracts", "desc": "Vendor agreements, warranty coverage checks, service entitlement, and expiration alerts."},
    {"id": "FON-M11", "name": "SLA & Escalation", "category": "Service Ops", "app": "apps.sla", "desc": "First-response and resolution deadlines, pause tracking during holds, and breach escalations."},
    {"id": "FON-M12", "name": "QR / Barcode Identification", "category": "Identification", "app": "apps.identification", "desc": "Barcode/QR asset label generation, camera scanner lookup, and instant ticket logging."},
    {"id": "FON-M13", "name": "Client / Requester Portal", "category": "Self-Service", "app": "apps.portal", "desc": "Multi-tenant client portal for submitting tickets, tracking progress, and approving work completion."},
    {"id": "FON-M14", "name": "Operational Dashboards", "category": "Analytics", "app": "apps.dashboards", "desc": "Role-tailored KPI summaries: MTTR, MTBF, technician utilization, and PM compliance rates."},
    {"id": "FON-M15", "name": "Audit & Compliance", "category": "Governance", "app": "apps.audit", "desc": "Immutable, append-only security logs, state transition attributions, and compliance exports."}
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
    mod = next((m for m in PRD_MODULES if m["id"].upper() == module_id.upper()), None)
    return render(request, "public/modules.html", {"active_page": "modules", "module_list": PRD_MODULES, "selected_module": mod})


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
    return render(request, "public/legal.html", {"active_page": "legal", "title": title, "content": content})


@login_required
def home(request):
    """Org dashboard. Foundation metrics are real counts; operational KPIs arrive with M14."""
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
    if perms and all(p.startswith("portal.request.") or p in client_allowed for p in perms):  # client-only account: portal is home
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
