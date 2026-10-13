from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.shortcuts import render
from django.utils import timezone

from apps.rbac import services as rbac
from apps.rbac.models import MembershipRole
from apps.workorders.models import WorkOrder

from .models import Downtime, ServiceRequest
from .workflow import CLOSED, REJECTED


ROLE_TITLES = {
    "admin": "Organization Owner",
    "operations_manager": "Operations Manager",
    "asset_manager": "Asset Manager",
    "maintenance_planner": "Maintenance Planner",
    "supervisor": "Maintenance Supervisor",
    "technician": "Lead Field Technician",
    "stores_manager": "Stores / Inventory Manager",
    "service_manager": "Service & SLA Manager",
    "auditor": "Compliance & Audit Officer",
    "client_requester": "Client / Requester",
}


def _dashboard_context(request, *, platform=False):
    from apps.audit.models import AuditLog
    from apps.incidents import selectors
    from apps.sla.models import SLABreach, SLATracking
    from apps.assets.models import Asset

    membership = request.membership
    organization = request.organization
    now = timezone.now()
    if platform:
        requests = ServiceRequest.objects.all().select_related("asset", "site", "reported_by__user")
        work_orders = WorkOrder.objects.all().select_related("site", "asset", "source_request", "assigned_to__user")
        title = "Platform Super Admin"
        audits = AuditLog.objects.all().filter(action__in=("incident.created", "incident.updated",
            "incident.status_changed", "incident.duplicate_detected", "incident.duplicate_linked",
            "incident.duplicate_overridden", "incident.downtime_recorded", "incident.downtime_updated",
            "incident.evidence_added"))
        breaches = SLABreach.objects.all().filter(tracking__request__isnull=False)
        has_incidents = has_work_orders = has_sla = has_audit = True
        role_keys = {"platform_admin"}
        client_no_assets = False
    else:
        if membership is None or not membership.is_active:
            raise PermissionDenied
        role_keys = set(MembershipRole.objects.filter(membership=membership).values_list("role__system_key", flat=True))
        if "admin" in role_keys or rbac.is_owner(membership):
            title = "Organization Owner"
        else:
            title = next((ROLE_TITLES[key] for key in (
                "operations_manager", "asset_manager", "maintenance_planner", "supervisor", "technician",
                "stores_manager", "service_manager", "auditor", "client_requester") if key in role_keys),
                "Incident & Service Dashboard")

        has_incidents = (rbac.has_permission_anywhere(membership, "incident.view") or
                         rbac.has_permission_anywhere(membership, "incident.view_assigned"))
        has_work_orders = (rbac.has_permission_anywhere(membership, "work_order.view") or
                           rbac.has_permission_anywhere(membership, "work_order.view_assigned"))
        has_sla = rbac.has_permission_anywhere(membership, "sla.view")
        has_audit = rbac.has_permission_anywhere(membership, "audit.view")
        requester_only = ("client_requester" in role_keys and not has_incidents)
        client_no_assets = ("client_requester" in role_keys and not Asset.objects.for_organization(
            organization).filter(owner=membership).exclude(status__in=("RETIRED", "DISPOSED")).exists())

        if has_incidents:
            requests = (selectors.requests_for(membership, organization, "incident.view") |
                        selectors.requests_for(membership, organization, "incident.view_assigned"))
        elif requester_only:
            requests = ServiceRequest.objects.for_organization(organization).filter(reported_by=membership)
        else:
            requests = ServiceRequest.objects.for_organization(organization).none()
        requests = requests.select_related("asset", "site", "reported_by__user")

        if rbac.has_permission_anywhere(membership, "work_order.view"):
            work_orders = rbac.site_scope(membership, "work_order.view").filter(
                WorkOrder.objects.for_organization(organization).select_related(
                    "site", "asset", "source_request", "assigned_to__user"), "site_id")
        elif rbac.has_permission_anywhere(membership, "work_order.view_assigned"):
            work_orders = WorkOrder.objects.for_organization(organization).filter(assigned_to=membership).select_related(
                "site", "asset", "source_request", "assigned_to__user")
        elif has_incidents:
            work_orders = WorkOrder.objects.for_organization(organization).filter(source_request__in=requests).select_related(
                "site", "asset", "source_request", "assigned_to__user")
        else:
            work_orders = WorkOrder.objects.for_organization(organization).none()

        request_ids = requests.values("pk")
        if has_audit:
            request_target_ids = [str(pk) for pk in requests.values_list("pk", flat=True)]
            audits = AuditLog.objects.for_organization(organization).filter(
                Q(action__startswith="incident.") | Q(target_id__in=request_target_ids)).order_by("-occurred_at")
        else:
            audits = AuditLog.objects.none()
        if has_sla:
            breaches = SLABreach.objects.for_organization(organization).filter(
                tracking__request_id__in=request_ids)
        else:
            breaches = SLABreach.objects.none()

        if not any((has_incidents, has_work_orders, has_sla, has_audit,
                    "client_requester" in role_keys)):
            raise PermissionDenied

    active_requests = requests.exclude(status__in=(CLOSED, REJECTED, "CANCELLED"))
    active_downtime = Downtime.objects.filter(request__in=active_requests, ended_at__isnull=True)
    waiting_planning = active_requests.filter(status="APPROVED", work_order__isnull=True)
    open_work_orders = work_orders.exclude(status__in=(WorkOrder.Status.CLOSED, WorkOrder.Status.CANCELLED))
    sla_risk = SLATracking.objects.filter(request__in=active_requests, status=SLATracking.Status.ACTIVE).filter(
        Q(response_state="PENDING", response_due_at__lte=now + timedelta(hours=1)) |
        Q(resolution_state="PENDING", resolution_due_at__lte=now + timedelta(hours=1)))
    repeated_assets = active_requests.values("asset_id", "asset__asset_tag", "asset__name", "site__code").annotate(
        report_count=Count("pk")).filter(report_count__gte=2).order_by("-report_count", "asset__asset_tag")[:8]
    by_category = active_requests.values("asset__category__name").annotate(
        total=Count("pk")).order_by("-total")[:8]
    by_site = active_requests.values("site__code").annotate(total=Count("pk")).order_by("-total")[:8]
    by_impact = active_requests.values("service_impact").annotate(total=Count("pk")).order_by("-total")

    can = {
        "create": (not platform and (rbac.has_permission_anywhere(membership, "incident.create") or
                                     selectors.is_owned_asset_reporter(membership))),
        "triage": not platform and rbac.has_permission_anywhere(membership, "incident.triage"),
        "approve": not platform and rbac.has_permission_anywhere(membership, "incident.approve"),
        "convert": not platform and rbac.has_permission_anywhere(membership, "work_order.create"),
        "assign": not platform and rbac.has_permission_anywhere(membership, "work_order.assign"),
        "dispatch": not platform and rbac.has_permission_anywhere(membership, "work_order.dispatch"),
        "confirm": not platform and rbac.has_permission_anywhere(membership, "incident.confirm"),
        "close": not platform and rbac.has_permission_anywhere(membership, "incident.close"),
        "sla_ack": not platform and rbac.has_permission_anywhere(membership, "sla.acknowledge"),
        "sla_process": not platform and rbac.has_permission(membership, "sla.process"),
        "plan": not platform and rbac.has_permission_anywhere(membership, "work_order.plan"),
        "sla": platform or has_sla,
        "audit": platform or has_audit,
        "asset_view": not platform and membership is not None and rbac.has_permission_anywhere(membership, "asset.view"),
        "open_incidents": not platform and has_incidents,
        "open_work_orders": not platform and has_work_orders,
        "parts": not platform and membership is not None and rbac.has_permission_anywhere(membership, "inventory.view"),
        "asset_manage": not platform and membership is not None and rbac.has_permission_anywhere(membership, "asset.update"),
        "sla_manage": not platform and membership is not None and rbac.has_permission(membership, "sla.manage"),
    }
    parts = []
    stock_by_part = {}
    if can["parts"]:
        from apps.inventory.models import StockBalance, WorkOrderPart

        parts = list(WorkOrderPart.objects.for_organization(organization).filter(
            work_order__in=work_orders, status__in=("REQUESTED", "RESERVED")).select_related(
                "work_order", "part", "warehouse").order_by("created_at")[:12])
        stock = StockBalance.objects.for_organization(organization).filter(part_id__in={p.part_id for p in parts})
        stock_scope = rbac.site_scope(membership, "inventory.view")
        stock = stock_scope.filter(stock, "warehouse__site_id")
        stock_by_part = {row["part_id"]: row for row in stock.values("part_id").annotate(
            on_hand=Sum("on_hand"), reserved=Sum("reserved"))}
        for line in parts:
            balance = stock_by_part.get(line.part_id, {})
            line.available_stock = (balance.get("on_hand") or 0) - (balance.get("reserved") or 0)
            line.shortage = max(line.remaining_to_issue - line.available_stock, 0)
    request_rows = list((requests if not platform and "client_requester" in role_keys and not has_incidents
                         else active_requests).order_by("-created_at")[:12])
    work_order_rows = list(open_work_orders.order_by("due_at", "-created_at")[:12])
    recent_audits = list(audits.order_by("-occurred_at")[:10])
    open_breaches = list(breaches.filter(status=SLABreach.Status.OPEN).select_related(
        "tracking__request", "site").order_by("-breached_at")[:10]) if can["sla"] else []
    at_risk = list(sla_risk.select_related("request", "site").order_by("response_due_at", "resolution_due_at")[:10])

    return {
        "platform": platform, "dashboard_title": title, "role_keys": role_keys,
        "requests": request_rows, "work_orders": work_order_rows, "parts": parts,
        "recent_audits": recent_audits, "open_breaches": open_breaches,
        "at_risk": at_risk,
        "repeated_assets": repeated_assets, "by_category": by_category, "by_site": by_site,
        "by_impact": by_impact,
        "counts": {
            "open": active_requests.count(), "critical": active_requests.filter(severity="CRITICAL").count(),
            "high": active_requests.filter(severity__in=("HIGH", "CRITICAL")).count(),
            "unassigned": active_requests.filter(assigned_team="", work_order__assigned_to__isnull=True).count(),
            "downtime": active_downtime.count(), "planning": waiting_planning.count(),
            "work_orders": open_work_orders.count(), "sla_risk": sla_risk.count(),
            "sla_breaches": breaches.filter(status=SLABreach.Status.OPEN).count(),
            "overdue_work_orders": open_work_orders.filter(due_at__lt=now).count(),
            "organizations": requests.values("organization_id").distinct().count() if platform else 1,
        },
        "can": can, "has_incidents": has_incidents, "has_work_orders": has_work_orders,
        "has_sla": has_sla, "has_audit": has_audit,
        "client_portal_gap": client_no_assets,
        "as_of": now,
    }


@login_required
def dashboard(request):
    if request.tenant_error or request.membership is None:
        raise PermissionDenied
    return render(request, "incidents/dashboard.html", _dashboard_context(request))


@login_required
def platform_dashboard(request):
    if not request.user.is_platform_admin:
        raise PermissionDenied
    return render(request, "incidents/dashboard.html", _dashboard_context(request, platform=True))
