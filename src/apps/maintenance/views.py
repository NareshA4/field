"""M04 HTML views. Chain: login -> ACTIVE membership -> permission gate -> organization + site-scoped lookup (404) ->
permission for the plan's site (403) -> service (validation, row locks, audit). Pages show real database state; the
generate / enable / disable buttons post to endpoints that call the services (no client-side scheduling)."""
from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views import View

from apps.assets import selectors as asset_selectors
from apps.assets.models import AssetMeter
from apps.checklists.models import ChecklistTemplate
from apps.core.exceptions import DomainError
from apps.rbac import services as rbac
from apps.sites import selectors as site_selectors
from apps.sites.views import form_page, need, or404
from apps.ui.mixins import TenantPermissionMixin

from . import selectors, services
from .forms import PlanForm, ScheduleForm
from .models import MaintenanceSchedule


class PmBase(TenantPermissionMixin, View):
    required_permission = "maintenance.view"


def _checklist_keys(org):
    """[(key, label)] one entry per checklist family, named after its latest version."""
    seen = {}
    for t in ChecklistTemplate.objects.for_organization(org).order_by("-version"):
        seen.setdefault(str(t.key), t.name)
    return sorted(seen.items(), key=lambda kv: kv[1].lower())


def _meters(plan):
    return AssetMeter.objects.for_organization(plan.organization).filter(asset=plan.asset, is_active=True)


def _paged(request, qs, size=20):
    return Paginator(qs, size).get_page(request.GET.get("page"))


def _with_state(items):
    """Attaches the derived PM state (``pm_state``) to cycles so templates need no function call."""
    items = list(items)
    for c in items:
        c.pm_state = selectors.cycle_state(c)
    return items


def _fail(request, exc):
    messages.error(request, exc.message if isinstance(exc, DomainError) else str(exc))


# --- plans -----------------------------------------------------------------------------------------------------------


class PlanListView(PmBase):
    def get(self, request):
        m, org = request.membership, request.organization
        qs = selectors.filter_plans(selectors.plans_for(m, org), request.GET)
        return render(request, "maintenance/plans.html", {
            "page": _paged(request, qs), "filters": request.GET,
            "sites": site_selectors.sites_for(m, org, "maintenance.view").order_by("code"),
            "can_create": rbac.has_permission_anywhere(m, "maintenance.create")})


class PlanCreateView(PmBase):
    required_permission = "maintenance.create"
    crumbs = [("Maintenance plans", "/app/maintenance/plans/"), ("New", None)]

    def _form(self, request, data=None):
        scope = rbac.site_scope(request.membership, "maintenance.create")
        assets = scope.filter(asset_selectors.assets_for(request.membership, request.organization), "site_id")
        return PlanForm(data, assets=assets, checklist_keys=_checklist_keys(request.organization),
                        initial={"asset": request.GET.get("asset")} if data is None else None)

    def get(self, request):
        return form_page(request, title="New maintenance plan", form=self._form(request), submit="Create plan",
                         cancel_url="/app/maintenance/plans/", crumbs=self.crumbs)

    def post(self, request):
        form = self._form(request, request.POST)
        if form.is_valid():
            d = dict(form.cleaned_data)
            asset = d.pop("asset")
            try:
                need(request, "maintenance.create", asset.site_id)
                plan = services.create_plan(request.organization, asset=asset, actor=request.user, request=request,
                                            **{k: v for k, v in d.items() if v not in (None, "")})
                messages.success(request, f"Plan {plan.name} created. Add a schedule so it generates work.")
                return redirect("maintenance:plan", pk=plan.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return form_page(request, title="New maintenance plan", form=form, submit="Create plan",
                         cancel_url="/app/maintenance/plans/", crumbs=self.crumbs, status=400)


class PlanEditView(PmBase):
    required_permission = "maintenance.update"

    def _plan(self, request, pk):
        plan = or404(selectors.get_plan, request.membership, request.organization, pk)
        need(request, "maintenance.update", plan.site_id)
        return plan

    def _page(self, request, plan, form, status=200):
        return form_page(request, title=f"Edit {plan.name}", form=form, submit="Save", status=status,
                         cancel_url=f"/app/maintenance/plans/{plan.pk}/",
                         crumbs=[("Maintenance plans", "/app/maintenance/plans/"),
                                 (plan.name, f"/app/maintenance/plans/{plan.pk}/"), ("Edit", None)])

    def get(self, request, pk):
        plan = self._plan(request, pk)
        initial = {f: getattr(plan, f) for f in ("name", "description", "priority", "estimated_hours",
                                                  "checklist_key")}
        return self._page(request, plan, PlanForm(editing=True, initial=initial,
                                                  checklist_keys=_checklist_keys(request.organization)))

    def post(self, request, pk):
        plan = self._plan(request, pk)
        form = PlanForm(request.POST, editing=True, checklist_keys=_checklist_keys(request.organization))
        if form.is_valid():
            try:
                services.update_plan(plan, actor=request.user, request=request, **form.cleaned_data)
                messages.success(request, "Saved.")
                return redirect("maintenance:plan", pk=plan.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self._page(request, plan, form, status=400)


class PlanDetailView(PmBase):
    def get(self, request, pk):
        m, org = request.membership, request.organization
        plan = or404(selectors.get_plan, m, org, pk)
        schedules = selectors.schedules_for(m, org).filter(plan=plan)
        cycles = _with_state(selectors.cycles_for(m, org).filter(schedule__plan=plan)[:15])
        names = dict(_checklist_keys(org))
        return render(request, "maintenance/plan_detail.html", {
            "plan": plan, "checklist_name": names.get(plan.checklist_key, plan.checklist_key), "rows": selectors.describe_schedule_rows(schedules), "cycles": cycles,
            "can": {k: rbac.has_permission(m, code, plan.site_id) for k, code in (
                ("create", "maintenance.create"), ("update", "maintenance.update"),
                ("generate", "maintenance.generate"))}})


class PlanActiveView(PmBase):
    required_permission = "maintenance.update"
    http_method_names = ["post"]

    def post(self, request, pk):
        plan = or404(selectors.get_plan, request.membership, request.organization, pk)
        need(request, "maintenance.update", plan.site_id)
        active = request.POST.get("active") == "1"
        try:
            services.set_plan_active(plan, active, actor=request.user, request=request)
            messages.success(request, "Plan enabled." if active else "Plan disabled: it will not generate work.")
        except DomainError as exc:
            _fail(request, exc)
        return redirect("maintenance:plan", pk=plan.pk)


# --- schedules ---------------------------------------------------------------------------------------------------------


class ScheduleCreateView(PmBase):
    required_permission = "maintenance.create"

    def _plan(self, request, pk):
        plan = or404(selectors.get_plan, request.membership, request.organization, pk)
        need(request, "maintenance.create", plan.site_id)
        return plan

    def _page(self, request, plan, form, status=200):
        return form_page(request, title=f"New schedule for {plan.name}", form=form, submit="Create schedule",
                         status=status, cancel_url=f"/app/maintenance/plans/{plan.pk}/",
                         crumbs=[("Maintenance plans", "/app/maintenance/plans/"),
                                 (plan.name, f"/app/maintenance/plans/{plan.pk}/"), ("New schedule", None)],
                         subtitle="Time-based schedules fill the frequency, interval and first due date; "
                                  "meter-based schedules pick one of the asset's meters and an interval.")

    def get(self, request, pk):
        plan = self._plan(request, pk)
        return self._page(request, plan, ScheduleForm(meters=_meters(plan)))

    def post(self, request, pk):
        plan = self._plan(request, pk)
        form = ScheduleForm(request.POST, meters=_meters(plan))
        if form.is_valid():
            try:
                sch = services.create_schedule(plan, actor=request.user, request=request, **form.cleaned_data)
                messages.success(request, f"Schedule created: {sch.describe()}.")
                return redirect("maintenance:plan", pk=plan.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self._page(request, plan, form, status=400)


class ScheduleBase(PmBase):
    def sch(self, request, pk, code=None) -> MaintenanceSchedule:
        sch = or404(selectors.get_schedule, request.membership, request.organization, pk)
        if code:
            need(request, code, sch.plan.site_id)
        return sch


class ScheduleDetailView(ScheduleBase):
    def get(self, request, pk):
        m, org = request.membership, request.organization
        sch = self.sch(request, pk)
        page = _paged(request, selectors.cycles_for(m, org).filter(schedule=sch), 15)
        page.object_list = _with_state(page.object_list)
        return render(request, "maintenance/schedule_detail.html", {
            "sch": sch, "state": selectors.schedule_state(sch), "blocked": services.blocked_reason(sch),
            "cycles": page,
            "can": {k: rbac.has_permission(m, code, sch.plan.site_id) for k, code in (
                ("update", "maintenance.update"), ("generate", "maintenance.generate"))}})


class ScheduleEditView(ScheduleBase):
    required_permission = "maintenance.update"

    def _page(self, request, sch, form, status=200):
        return form_page(request, title=f"Edit schedule: {sch.describe()}", form=form, submit="Save", status=status,
                         cancel_url=f"/app/maintenance/schedules/{sch.pk}/",
                         crumbs=[("Maintenance plans", "/app/maintenance/plans/"),
                                 (sch.plan.name, f"/app/maintenance/plans/{sch.plan_id}/"), ("Edit schedule", None)],
                         subtitle="Changing the recurrence restarts it at the next occurrence that is not in the past.")

    def get(self, request, pk):
        sch = self.sch(request, pk, "maintenance.update")
        initial = {f: getattr(sch, f) for f in ScheduleForm.base_fields if f != "trigger_type"}
        return self._page(request, sch, ScheduleForm(editing=True, meters=_meters(sch.plan), initial=initial))

    def post(self, request, pk):
        sch = self.sch(request, pk, "maintenance.update")
        form = ScheduleForm(request.POST, editing=True, meters=_meters(sch.plan))
        if form.is_valid():
            try:
                services.update_schedule(sch, actor=request.user, request=request, **form.cleaned_data)
                messages.success(request, "Saved.")
                return redirect("maintenance:schedule", pk=sch.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self._page(request, sch, form, status=400)


class ScheduleActionView(ScheduleBase):
    http_method_names = ["post"]

    def post(self, request, pk, action):
        if action not in ("enable", "disable", "generate"):
            messages.error(request, "Unknown action.")
            return redirect("maintenance:plans")
        code = "maintenance.generate" if action == "generate" else "maintenance.update"
        sch = self.sch(request, pk, code)
        try:
            if action == "generate":
                cycle = services.generate_cycle(sch, actor=request.user, manual=True, request=request)
                if cycle is None:
                    messages.info(request, "Nothing was generated (that occurrence already has its work order).")
                else:
                    messages.success(request, f"Work order {cycle.work_order.number} generated.")
            else:
                services.set_schedule_active(sch, action == "enable", actor=request.user, request=request)
                messages.success(request, "Schedule enabled." if action == "enable" else "Schedule disabled.")
        except DomainError as exc:
            _fail(request, exc)
        return redirect(request.POST.get("next") if (request.POST.get("next") or "").startswith("/app/maintenance/")
                        else f"/app/maintenance/schedules/{sch.pk}/")


# --- due / upcoming / history -------------------------------------------------------------------------------------------


class DueView(PmBase):
    def get(self, request):
        m, org = request.membership, request.organization
        qs = selectors.schedules_for(m, org).filter(is_active=True, plan__is_active=True)
        site = (request.GET.get("site") or "").strip()
        if site:
            parsed = selectors._uuid_or_none(site)
            qs = qs.filter(plan__site_id=parsed) if parsed else qs.none()
        rows = selectors.describe_schedule_rows(qs.order_by("next_due_date", "plan__name"))
        state = (request.GET.get("state") or "").strip()
        if state in ("DUE", "SCHEDULED"):
            rows = [r for r in rows if r["state"] == state]
        page = Paginator(rows, 25).get_page(request.GET.get("page"))
        return render(request, "maintenance/due.html", {
            "page": page, "filters": request.GET,
            "sites": site_selectors.sites_for(m, org, "maintenance.view").order_by("code"),
            "can_generate": rbac.has_permission_anywhere(m, "maintenance.generate")})


class HistoryView(PmBase):
    def get(self, request):
        m, org = request.membership, request.organization
        qs = selectors.filter_cycles(selectors.cycles_for(m, org), request.GET)
        page = _paged(request, qs, 25)
        page.object_list = _with_state(page.object_list)
        return render(request, "maintenance/history.html", {
            "page": page, "filters": request.GET,
            "plans": selectors.plans_for(m, org).order_by("name")})
