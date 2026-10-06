"""M06 HTML views. Chain: login -> membership -> permission gate -> organization + site/assignment-scope restricted
lookup (404) -> permission for the order's site (403) -> service (state machine, validation, audit)."""
from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views import View

from apps.assets import selectors as asset_selectors
from apps.core.exceptions import DomainError
from apps.rbac import services as rbac
from apps.sites import selectors as site_selectors
from apps.sites.views import form_page, need, or404
from apps.tenancy.models import Membership
from apps.ui.mixins import TenantPermissionMixin

from . import selectors, services
from .forms import (
    CompleteForm,
    EvidenceForm,
    LaborForm,
    MaterialForm,
    PlanForm,
    TechnicianForm,
    WorkOrderForm,
)
from .models import WorkOrder
from .workflow import (
    ACTION_PERMISSIONS,
    BUTTON_STYLES,
    EXECUTION_ACTIONS,
    REASON_REQUIRED,
    TERMINAL_STATES,
    WORK_ORDER_STATUS,
)

TABS = [("overview", "Overview"), ("work", "Labor & material"), ("evidence", "Evidence"), ("history", "History")]
CONFIRMS = {"cancel": "Cancel this work order?", "close": "Close this work order?"}
FORM_KIND = {"plan": "plan", "assign": "assign", "complete": "complete"}


def _wo(request, pk, code=None) -> WorkOrder:
    wo = or404(selectors.get_work_order, request.membership, request.organization, pk)
    if code:
        need(request, code, wo.site_id)
    return wo


def _technicians(org, wo):
    """Active members who may execute work at the order's site."""
    members = Membership.objects.for_organization(org).filter(status=Membership.Status.ACTIVE).select_related("user")
    ids = [m.pk for m in members if rbac.has_permission(m, "work_order.start", wo.site_id)]
    return members.filter(pk__in=ids).order_by("user__full_name")


class WorkOrderBase(TenantPermissionMixin, View):
    required_permission = "work_order.view_assigned"


class WorkOrderListView(WorkOrderBase):
    def get(self, request):
        m, org = request.membership, request.organization
        qs = selectors.filter_work_orders(selectors.work_orders_for(m, org), request.GET, m)
        page = Paginator(qs, 20).get_page(request.GET.get("page"))
        return render(request, "workorders/list.html", {
            "page": page, "filters": request.GET, "statuses": WorkOrder.Status.choices,
            "priorities": WorkOrder.Priority.choices, "types": WorkOrder.WorkType.choices,
            "sites": site_selectors.sites_for(m, org, "work_order.view_assigned").order_by("code"),
            "can_create": rbac.has_permission_anywhere(m, "work_order.create")})


class WorkOrderCreateView(WorkOrderBase):
    required_permission = "work_order.create"
    crumbs = [("Work orders", "/app/work-orders/"), ("New", None)]

    def _assets(self, request):
        scope = rbac.site_scope(request.membership, "work_order.create")
        return scope.filter(asset_selectors.assets_for(request.membership, request.organization), "site_id")

    def get(self, request):
        form = WorkOrderForm(assets=self._assets(request), initial={"asset": request.GET.get("asset")})
        return form_page(request, title="New work order", form=form, submit="Create work order",
                         cancel_url="/app/work-orders/", crumbs=self.crumbs,
                         subtitle="Work from a breakdown request is created on the request page.")

    def post(self, request):
        form = WorkOrderForm(request.POST, assets=self._assets(request))
        if form.is_valid():
            d = dict(form.cleaned_data)
            asset = d.pop("asset")
            try:
                wo = services.create_work_order(request.organization, asset=asset, actor=request.user,
                                                request=request, **{k: v for k, v in d.items() if v not in (None, "")})
                messages.success(request, f"{wo.number} created.")
                return redirect("workorders:detail", pk=wo.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return form_page(request, title="New work order", form=form, submit="Create work order",
                         cancel_url="/app/work-orders/", crumbs=self.crumbs, status=400)


class WorkOrderEditView(WorkOrderBase):
    required_permission = "work_order.update"

    def _page(self, request, wo, form, status=200):
        return form_page(request, title=f"Edit {wo.number}", form=form, submit="Save", status=status,
                         cancel_url=f"/app/work-orders/{wo.pk}/",
                         crumbs=[("Work orders", "/app/work-orders/"), (wo.number, f"/app/work-orders/{wo.pk}/"),
                                 ("Edit", None)])

    def get(self, request, pk):
        wo = _wo(request, pk, "work_order.update")
        initial = {f: getattr(wo, f) for f in ("title", "description", "work_type", "priority", "planned_start",
                                                "planned_end", "estimated_hours")}
        return self._page(request, wo, WorkOrderForm(initial=initial, editing=True))

    def post(self, request, pk):
        wo = _wo(request, pk, "work_order.update")
        form = WorkOrderForm(request.POST, editing=True)
        if form.is_valid():
            try:
                services.update_work_order(wo, actor=request.user, request=request, **form.cleaned_data)
                messages.success(request, "Saved.")
                return redirect("workorders:detail", pk=wo.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self._page(request, wo, form, status=400)


def build_actions(request, wo) -> list[dict]:
    """The lifecycle buttons this caller may press right now (the server re-checks every one on POST)."""
    m = request.membership
    dispatcher = rbac.has_permission(m, "work_order.dispatch", wo.site_id)
    out = []
    for t in WORK_ORDER_STATUS.available(wo.status):
        if not rbac.has_permission(m, ACTION_PERMISSIONS[t.action], wo.site_id):
            continue
        if t.action in EXECUTION_ACTIONS and wo.assigned_to_id != m.pk and not dispatcher:
            continue
        out.append({"action": t.action, "label": t.label, "style": BUTTON_STYLES.get(t.action, "primary"),
                    "kind": FORM_KIND.get(t.action, "reason" if t.action in REASON_REQUIRED else "simple"),
                    "confirm": CONFIRMS.get(t.action), "url": f"/app/work-orders/{wo.pk}/transition/{t.action}/"})
    return out


class WorkOrderDetailView(WorkOrderBase):
    def get(self, request, pk):
        m, org = request.membership, request.organization
        wo = _wo(request, pk)
        tab = request.GET.get("tab", "overview")
        if tab not in dict(TABS):
            tab = "overview"
        can = {k: rbac.has_permission(m, code, wo.site_id) for k, code in (
            ("update", "work_order.update"), ("assign", "work_order.assign"), ("record", "work_order.record"),
            ("attach", "work_order.attach"), ("dispatch", "work_order.dispatch"),
            ("view_request", "incident.view"), ("view_pm", "maintenance.view"),
            ("export_evidence", "audit.export"))}
        actions = build_actions(request, wo)
        ctx = {"wo": wo, "tab": tab, "tabs": TABS, "can": can, "actions": actions,
               "editable": wo.status in ("DRAFT", "PLANNED"), "terminal": wo.status in TERMINAL_STATES}
        techs = _technicians(org, wo) if any(a["kind"] == "assign" for a in actions) or (
            can["assign"] and wo.status in ("ASSIGNED", "DISPATCHED")) else None
        for a in actions:
            if a["kind"] == "plan":
                a["form"] = PlanForm(initial={"planned_start": wo.planned_start, "planned_end": wo.planned_end,
                                              "estimated_hours": wo.estimated_hours, "priority": wo.priority})
            elif a["kind"] == "assign":
                a["form"] = TechnicianForm(technicians=techs)
            elif a["kind"] == "complete":
                a["form"] = CompleteForm(initial={"resolution_notes": wo.resolution_notes})
        if can["assign"] and wo.status in ("ASSIGNED", "DISPATCHED"):
            ctx["reassign_form"] = TechnicianForm(technicians=techs)
        if tab == "overview":
            from apps.sla import selectors as sla_selectors

            ctx["sla"] = sla_selectors.tracking_for_work_order(m, org, wo)
            if wo.source_type == "PREVENTIVE_MAINTENANCE":
                from apps.maintenance.models import MaintenanceCycle

                ctx["pm_cycle"] = MaintenanceCycle.objects.for_organization(org).filter(work_order=wo).select_related(
                    "schedule__plan").first()
            if wo.status in ("COMPLETED", "SUPERVISOR_REVIEW"):
                ctx["blockers"] = services.closure_blockers(wo)
            ctx["total_hours"] = selectors.total_hours(org, wo)
        elif tab == "work":
            dispatcher_or_reviewer = can["dispatch"] or rbac.has_permission(m, "work_order.review", wo.site_id)
            ctx["labor"] = selectors.labor_for(org, wo)
            ctx["materials"] = selectors.materials_for(org, wo)
            ctx["total_hours"] = selectors.total_hours(org, wo)
            ctx["labor_form"] = LaborForm(
                technicians=_technicians(org, wo) if dispatcher_or_reviewer else None)
            ctx["material_form"] = MaterialForm()
            ctx["recordable"] = can["record"] and wo.status in ("IN_PROGRESS", "ON_HOLD", "COMPLETED",
                                                                 "SUPERVISOR_REVIEW")
        elif tab == "evidence":
            ctx["evidence"] = services.evidence_for(wo).select_related("uploaded_by")
            ctx["evidence_form"] = EvidenceForm()
        elif tab == "history":
            ctx["events"] = selectors.events_for(org, wo)
        return render(request, "workorders/detail.html", ctx)


class _Post(WorkOrderBase):
    http_method_names = ["post"]
    tab = "overview"

    def done(self, request, wo, ok: str | None = None):
        if ok:
            messages.success(request, ok)
        return redirect(f"/app/work-orders/{wo.pk}/?tab={self.tab}")

    def fail(self, request, wo, exc):
        messages.error(request, exc.message if isinstance(exc, DomainError) else str(exc))
        return self.done(request, wo)


class WorkOrderTransitionView(_Post):
    def post(self, request, pk, action):
        wo = _wo(request, pk)
        if action not in ACTION_PERMISSIONS:
            messages.error(request, "Unknown action.")
            return self.done(request, wo)
        need(request, ACTION_PERMISSIONS[action], wo.site_id)
        data = {}
        try:
            if action == "plan":
                form = PlanForm(request.POST)
                if not form.is_valid():
                    messages.error(request, "Enter valid planned start / end and hours.")
                    return self.done(request, wo)
                data = {k: v for k, v in form.cleaned_data.items() if v not in (None, "")}
            elif action == "assign":
                form = TechnicianForm(request.POST, technicians=Membership.objects.for_organization(
                    request.organization).select_related("user"))
                if not form.is_valid():
                    messages.error(request, "Choose a technician.")
                    return self.done(request, wo)
                data = {"technician": form.cleaned_data["technician"]}
            elif action == "complete":
                form = CompleteForm(request.POST)
                if not form.is_valid():
                    messages.error(request, "Resolution notes (at least 10 characters) are required.")
                    return self.done(request, wo)
                data = {"resolution_notes": form.cleaned_data["resolution_notes"]}
            services.transition(wo, action=action, reason=request.POST.get("reason", ""), actor=request.user,
                                membership=request.membership, request=request, **data)
            return self.done(request, wo, "Work order updated.")
        except DomainError as exc:
            return self.fail(request, wo, exc)


class WorkOrderReassignView(_Post):
    def post(self, request, pk):
        wo = _wo(request, pk, "work_order.assign")
        form = TechnicianForm(request.POST, technicians=Membership.objects.for_organization(
            request.organization).select_related("user"))
        if not form.is_valid():
            messages.error(request, "Choose a technician and give a reason.")
            return self.done(request, wo)
        try:
            services.reassign(wo, technician=form.cleaned_data["technician"], reason=form.cleaned_data["reason"],
                              actor=request.user, request=request)
            return self.done(request, wo, "Technician changed.")
        except DomainError as exc:
            return self.fail(request, wo, exc)


class WorkOrderLaborView(_Post):
    tab = "work"

    def post(self, request, pk):
        wo = _wo(request, pk, "work_order.record")
        m = request.membership
        dispatcher_or_reviewer = rbac.has_permission(m, "work_order.dispatch", wo.site_id) or rbac.has_permission(
            m, "work_order.review", wo.site_id)
        form = LaborForm(request.POST, technicians=Membership.objects.for_organization(
            request.organization).select_related("user") if dispatcher_or_reviewer else None)
        if not form.is_valid():
            messages.error(request, "Enter a valid date and hours (0.01 - 24).")
            return self.done(request, wo)
        d = form.cleaned_data
        try:
            services.record_labor(wo, technician=d.get("technician") or m, work_date=d["work_date"],
                                  hours=d["hours"], notes=d["notes"], actor=request.user, membership=m,
                                  request=request)
            return self.done(request, wo, "Labor recorded.")
        except DomainError as exc:
            return self.fail(request, wo, exc)


class WorkOrderMaterialView(_Post):
    tab = "work"

    def post(self, request, pk):
        wo = _wo(request, pk, "work_order.record")
        form = MaterialForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Describe the material and enter a quantity above zero.")
            return self.done(request, wo)
        d = form.cleaned_data
        try:
            services.record_material(wo, description=d["description"], quantity=d["quantity"], unit=d["unit"],
                                     part_number=d["part_number"], actor=request.user, membership=request.membership,
                                     request=request)
            return self.done(request, wo, "Material recorded.")
        except DomainError as exc:
            return self.fail(request, wo, exc)


class WorkOrderEvidenceView(_Post):
    tab = "evidence"

    def post(self, request, pk):
        wo = _wo(request, pk, "work_order.attach")
        form = EvidenceForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Choose a file to upload.")
            return self.done(request, wo)
        try:
            services.add_evidence(wo, form.cleaned_data["file"], description=form.cleaned_data["description"],
                                  actor=request.user, membership=request.membership, request=request)
            return self.done(request, wo, "Evidence uploaded.")
        except DomainError as exc:
            return self.fail(request, wo, exc)
