"""M05 HTML views. Same chain as M01/M02: login -> membership -> permission gate -> organization + site-scope
restricted lookup (404) -> permission for the request's site (403) -> service (validation, transaction, audit)."""
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
from apps.ui.mixins import TenantPermissionMixin

from . import selectors, services
from .forms import CreateWorkOrderForm, DowntimeForm, EvidenceForm, RequestForm
from .models import ServiceRequest
from .workflow import ACTION_PERMISSIONS, BUTTON_STYLES, REASON_REQUIRED, REQUEST_STATUS, SYSTEM_ACTIONS

TABS = [("overview", "Overview"), ("evidence", "Evidence"), ("history", "History")]
CONFIRMS = {"reject": "Reject this request?", "close": "Close this request?"}


def _req(request, pk, code=None) -> ServiceRequest:
    sr = or404(selectors.get_request, request.membership, request.organization, pk)
    if code:
        need(request, code, sr.site_id)
    return sr


class IncidentBase(TenantPermissionMixin, View):
    pass


class RequestListView(IncidentBase):
    required_permission = "incident.view"

    def get(self, request):
        m, org = request.membership, request.organization
        qs = selectors.filter_requests(selectors.requests_for(m, org), request.GET)
        page = Paginator(qs, 20).get_page(request.GET.get("page"))
        return render(request, "incidents/list.html", {
            "page": page, "filters": request.GET, "statuses": ServiceRequest.Status.choices,
            "severities": ServiceRequest.Severity.choices, "kinds": ServiceRequest.Kind.choices,
            "sites": site_selectors.sites_for(m, org, "incident.view").order_by("code"),
            "can_create": rbac.has_permission_anywhere(m, "incident.create")})


class RequestCreateView(IncidentBase):
    required_permission = "incident.create"
    crumbs = [("Incidents & requests", "/app/incidents/"), ("Report", None)]

    def _assets(self, request):
        scope = rbac.site_scope(request.membership, "incident.create")
        return scope.filter(asset_selectors.assets_for(request.membership, request.organization), "site_id")

    def get(self, request):
        form = RequestForm(assets=self._assets(request), initial={"asset": request.GET.get("asset")})
        return form_page(request, title="Report an incident / request", form=form, submit="Submit report",
                         cancel_url="/app/incidents/", crumbs=self.crumbs,
                         subtitle="Describe what failed and its impact. A supervisor will triage it.")

    def post(self, request):
        form = RequestForm(request.POST, assets=self._assets(request))
        if form.is_valid():
            d = dict(form.cleaned_data)
            asset = d.pop("asset")
            try:
                sr = services.create_request(request.organization, asset=asset, reporter=request.membership,
                                             actor=request.user, request=request,
                                             **{k: v for k, v in d.items() if v not in (None, "")})
                messages.success(request, f"{sr.number} reported.")
                return redirect("incidents:detail", pk=sr.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return form_page(request, title="Report an incident / request", form=form, submit="Submit report",
                         cancel_url="/app/incidents/", crumbs=self.crumbs, status=400)


class RequestEditView(IncidentBase):
    required_permission = "incident.update"

    def _page(self, request, sr, form, status=200):
        return form_page(request, title=f"Edit {sr.number}", form=form, submit="Save", status=status,
                         cancel_url=f"/app/incidents/{sr.pk}/",
                         crumbs=[("Incidents & requests", "/app/incidents/"), (sr.number, f"/app/incidents/{sr.pk}/"),
                                 ("Edit", None)])

    def get(self, request, pk):
        sr = _req(request, pk, "incident.update")
        initial = {f: getattr(sr, f) for f in ("title", "description", "severity", "service_impact", "impact_notes",
                                                "occurred_at")}
        return self._page(request, sr, RequestForm(initial=initial, editing=True))

    def post(self, request, pk):
        sr = _req(request, pk, "incident.update")
        form = RequestForm(request.POST, editing=True)
        if form.is_valid():
            changes = {k: v for k, v in form.cleaned_data.items() if not (k == "occurred_at" and v is None)}
            try:
                services.update_request(sr, actor=request.user, request=request, **changes)
                messages.success(request, "Saved.")
                return redirect("incidents:detail", pk=sr.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self._page(request, sr, form, status=400)


class RequestDetailView(IncidentBase):
    required_permission = "incident.view"

    def get(self, request, pk):
        m, org = request.membership, request.organization
        sr = _req(request, pk)
        tab = request.GET.get("tab", "overview")
        if tab not in dict(TABS):
            tab = "overview"
        can = {k: rbac.has_permission(m, code, sr.site_id) for k, code in (
            ("update", "incident.update"), ("attach", "incident.attach"), ("downtime", "incident.downtime.manage"),
            ("create_wo", "work_order.create"), ("view_wo", "work_order.view_assigned"))}
        transitions = [
            {"label": t.label, "url": f"/app/incidents/{sr.pk}/transition/{t.action}/", "style": BUTTON_STYLES.get(
                t.action, "primary"), "reason": t.action in REASON_REQUIRED, "confirm": CONFIRMS.get(t.action)}
            for t in REQUEST_STATUS.available(sr.status)
            if t.action not in SYSTEM_ACTIONS and rbac.has_permission(m, ACTION_PERMISSIONS[t.action], sr.site_id)]
        ctx = {"sr": sr, "tab": tab, "tabs": TABS, "can": can, "transitions": transitions,
               "work_orders": sr.work_orders.select_related("assigned_to__user").order_by("created_at"),
               "downtime": selectors.downtime_for(org, sr), "editable": sr.status in ("NEW", "TRIAGED")}
        if tab == "overview":
            from apps.sla import selectors as sla_selectors

            ctx["sla"] = sla_selectors.tracking_for_request(m, org, sr)
        if can["create_wo"] and sr.status == "APPROVED":
            ctx["wo_form"] = CreateWorkOrderForm()
        if tab == "evidence":
            ctx["evidence"] = selectors.evidence_for(org, sr)
            ctx["evidence_form"] = EvidenceForm()
        elif tab == "history":
            ctx["history"] = selectors.history_for(org, sr)
        elif tab == "overview":
            dt = ctx["downtime"]
            ctx["downtime_form"] = DowntimeForm(initial={"started_at": dt.started_at if dt else None,
                                                         "ended_at": dt.ended_at if dt else None})
        return render(request, "incidents/detail.html", ctx)


class _Post(IncidentBase):
    http_method_names = ["post"]
    required_permission = "incident.view"
    tab = "overview"

    def done(self, request, sr, ok: str | None = None):
        if ok:
            messages.success(request, ok)
        return redirect(f"/app/incidents/{sr.pk}/?tab={self.tab}")


class RequestTransitionView(_Post):
    def post(self, request, pk, action):
        sr = _req(request, pk)
        if action not in ACTION_PERMISSIONS:
            messages.error(request, "That step is performed by the work order, not by hand.")
            return self.done(request, sr)
        need(request, ACTION_PERMISSIONS[action], sr.site_id)
        try:
            services.transition(sr, action=action, reason=request.POST.get("reason", ""), actor=request.user,
                                request=request)
            return self.done(request, sr, "Status updated.")
        except DomainError as exc:
            messages.error(request, exc.message)
            return self.done(request, sr)


class RequestCreateWorkOrderView(_Post):
    def post(self, request, pk):
        sr = _req(request, pk, "work_order.create")
        form = CreateWorkOrderForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Check the work order details.")
            return self.done(request, sr)
        try:
            wo = services.create_work_order_for_request(
                sr, actor=request.user, membership=request.membership, request=request,
                **{k: v for k, v in form.cleaned_data.items() if v})
            messages.success(request, f"Work order {wo.number} created.")
            return redirect("workorders:detail", pk=wo.pk) if rbac.has_permission(
                request.membership, "work_order.view_assigned", wo.site_id) else self.done(request, sr)
        except DomainError as exc:
            messages.error(request, exc.message)
            return self.done(request, sr)


class RequestDowntimeView(_Post):
    def post(self, request, pk):
        sr = _req(request, pk, "incident.downtime.manage")
        form = DowntimeForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Enter a valid downtime start (and optional end).")
            return self.done(request, sr)
        try:
            services.set_downtime(sr, started_at=form.cleaned_data["started_at"],
                                  ended_at=form.cleaned_data["ended_at"], actor=request.user, request=request)
            return self.done(request, sr, "Downtime saved.")
        except DomainError as exc:
            messages.error(request, exc.message)
            return self.done(request, sr)


class RequestEvidenceView(_Post):
    tab = "evidence"

    def post(self, request, pk):
        sr = _req(request, pk, "incident.attach")
        form = EvidenceForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Choose a file to upload.")
            return self.done(request, sr)
        try:
            services.add_evidence(sr, form.cleaned_data["file"], description=form.cleaned_data["description"],
                                  actor=request.user, request=request)
            return self.done(request, sr, "Evidence uploaded.")
        except DomainError as exc:
            messages.error(request, exc.message)
            return self.done(request, sr)
