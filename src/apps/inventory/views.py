"""M09 HTML views (Django templates). Chain: login -> ACTIVE membership -> permission gate -> organization + site
scoped lookup (404) -> permission for the object's site (403) -> service (locks, ledger, audit). Every state-changing
control is a POST to one of these views; the services re-check everything."""
from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from apps.core.exceptions import DomainError
from apps.rbac import services as rbac
from apps.sites import selectors as site_selectors
from apps.sites.views import form_page, need, or404
from apps.ui.mixins import TenantPermissionMixin
from apps.workorders import selectors as wo_selectors

from . import selectors, services
from .forms import (
    AdjustForm,
    IssueForm,
    LevelsForm,
    PartForm,
    QuantityForm,
    ReceiveForm,
    RequestPartForm,
    ReserveForm,
    TransferForm,
    WarehouseForm,
)
from .models import PartReservation, StockMovement
from .workflow import TERMINAL

LINE_ACTIONS = {  # action -> permission (cancel is decided by the service: stores staff or the requester)
    "reserve": "inventory.reserve", "release": "inventory.reserve", "issue": "inventory.issue",
    "consume": "inventory.consume", "return": "inventory.return", "reconcile": "inventory.reconcile",
    "cancel": None,
}


def safe_next(request, default: str) -> str:
    target = request.POST.get("next") or ""
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts=None) and target.startswith("/app/"):
        return target
    return default


class InvBase(TenantPermissionMixin, View):
    required_permission = "inventory.view"


def _paged(request, qs, size=25):
    return Paginator(qs, size).get_page(request.GET.get("page"))


def _fail(request, exc):
    messages.error(request, exc.message if isinstance(exc, DomainError) else str(exc))


# --- parts -------------------------------------------------------------------------------------------------------


class PartListView(InvBase):
    required_permission = "inventory.part.view"

    def get(self, request):
        qs = selectors.filter_parts(selectors.parts_for(request.organization), request.GET)
        return render(request, "inventory/parts.html", {
            "page": _paged(request, qs), "filters": request.GET,
            "can_manage": rbac.has_permission(request.membership, "inventory.part.manage")})


class PartCreateView(InvBase):
    required_permission = "inventory.part.manage"
    crumbs = [("Parts", "/app/inventory/parts/"), ("New", None)]

    def get(self, request):
        return form_page(request, title="New part", form=PartForm(), submit="Create part",
                         cancel_url="/app/inventory/parts/", crumbs=self.crumbs)

    def post(self, request):
        form = PartForm(request.POST)
        if form.is_valid():
            try:
                part = services.create_part(request.organization, actor=request.user, request=request,
                                            **form.cleaned_data)
                messages.success(request, f"Part {part.part_number} created.")
                return redirect("inventory:part", pk=part.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return form_page(request, title="New part", form=form, submit="Create part",
                         cancel_url="/app/inventory/parts/", crumbs=self.crumbs, status=400)


class PartEditView(InvBase):
    required_permission = "inventory.part.manage"

    def _page(self, request, part, form, status=200):
        return form_page(request, title=f"Edit {part.part_number}", form=form, submit="Save", status=status,
                         cancel_url=f"/app/inventory/parts/{part.pk}/",
                         crumbs=[("Parts", "/app/inventory/parts/"), (part.part_number,
                                 f"/app/inventory/parts/{part.pk}/"), ("Edit", None)])

    def get(self, request, pk):
        part = or404(selectors.get_part, request.organization, pk)
        initial = {f: getattr(part, f) for f in PartForm.base_fields}
        return self._page(request, part, PartForm(initial=initial))

    def post(self, request, pk):
        part = or404(selectors.get_part, request.organization, pk)
        form = PartForm(request.POST)
        if form.is_valid():
            try:
                services.update_part(part, actor=request.user, request=request, **form.cleaned_data)
                messages.success(request, "Saved.")
                return redirect("inventory:part", pk=part.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self._page(request, part, form, status=400)


class PartDetailView(InvBase):
    required_permission = "inventory.part.view"

    def get(self, request, pk):
        m, org = request.membership, request.organization
        part = or404(selectors.get_part, org, pk)
        can_view_stock = rbac.has_permission_anywhere(m, "inventory.view")
        ctx = {"part": part, "can_manage": rbac.has_permission(m, "inventory.part.manage"),
               "can_view_stock": can_view_stock}
        if can_view_stock:
            ctx["balances"] = selectors.balances_for(m, org).filter(part=part)
            ctx["totals"] = selectors.part_totals(m, org, part)
            ctx["movements"] = selectors.movements_for(m, org).filter(part=part)[:15]
        return render(request, "inventory/part_detail.html", ctx)


class PartActiveView(InvBase):
    required_permission = "inventory.part.manage"
    http_method_names = ["post"]

    def post(self, request, pk):
        part = or404(selectors.get_part, request.organization, pk)
        try:
            active = request.POST.get("active") == "1"
            services.set_part_active(part, active, actor=request.user, request=request)
            messages.success(request, "Part activated." if active else "Part deactivated.")
        except DomainError as exc:
            _fail(request, exc)
        return redirect("inventory:part", pk=part.pk)


# --- warehouses --------------------------------------------------------------------------------------------------


class WarehouseListView(InvBase):
    def get(self, request):
        m, org = request.membership, request.organization
        qs = selectors.filter_warehouses(selectors.warehouses_for(m, org), request.GET)
        return render(request, "inventory/warehouses.html", {
            "page": _paged(request, qs), "filters": request.GET,
            "sites": site_selectors.sites_for(m, org, "inventory.view").order_by("code"),
            "can_manage": rbac.has_permission_anywhere(m, "inventory.warehouse.manage")})


class WarehouseCreateView(InvBase):
    required_permission = "inventory.warehouse.manage"
    crumbs = [("Warehouses", "/app/inventory/warehouses/"), ("New", None)]

    def _sites(self, request):
        return site_selectors.sites_for(request.membership, request.organization,
                                        "inventory.warehouse.manage").filter(status="ACTIVE").order_by("code")

    def get(self, request):
        return form_page(request, title="New warehouse", form=WarehouseForm(sites=self._sites(request)),
                         submit="Create warehouse", cancel_url="/app/inventory/warehouses/", crumbs=self.crumbs)

    def post(self, request):
        form = WarehouseForm(request.POST, sites=self._sites(request))
        if form.is_valid():
            d = dict(form.cleaned_data)
            try:
                wh = services.create_warehouse(request.organization, site=d.pop("site"), actor=request.user,
                                               request=request, **d)
                messages.success(request, f"Warehouse {wh.code} created.")
                return redirect("inventory:warehouse", pk=wh.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return form_page(request, title="New warehouse", form=form, submit="Create warehouse",
                         cancel_url="/app/inventory/warehouses/", crumbs=self.crumbs, status=400)


class WarehouseEditView(InvBase):
    required_permission = "inventory.warehouse.manage"

    def _wh(self, request, pk):
        wh = or404(selectors.get_warehouse, request.membership, request.organization, pk)
        need(request, "inventory.warehouse.manage", wh.site_id)
        return wh

    def _page(self, request, wh, form, status=200):
        return form_page(request, title=f"Edit {wh.code}", form=form, submit="Save", status=status,
                         cancel_url=f"/app/inventory/warehouses/{wh.pk}/",
                         crumbs=[("Warehouses", "/app/inventory/warehouses/"),
                                 (wh.code, f"/app/inventory/warehouses/{wh.pk}/"), ("Edit", None)])

    def get(self, request, pk):
        wh = self._wh(request, pk)
        return self._page(request, wh, WarehouseForm(editing=True, initial={
            "code": wh.code, "name": wh.name, "description": wh.description}))

    def post(self, request, pk):
        wh = self._wh(request, pk)
        form = WarehouseForm(request.POST, editing=True)
        if form.is_valid():
            try:
                services.update_warehouse(wh, actor=request.user, request=request, **form.cleaned_data)
                messages.success(request, "Saved.")
                return redirect("inventory:warehouse", pk=wh.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self._page(request, wh, form, status=400)


class WarehouseDetailView(InvBase):
    def get(self, request, pk):
        m, org = request.membership, request.organization
        wh = or404(selectors.get_warehouse, m, org, pk)
        return render(request, "inventory/warehouse_detail.html", {
            "wh": wh, "balances": selectors.balances_for(m, org).filter(warehouse=wh),
            "movements": selectors.movements_for(m, org).filter(warehouse=wh)[:15],
            "can_manage": rbac.has_permission(m, "inventory.warehouse.manage", wh.site_id),
            "can_receive": rbac.has_permission(m, "inventory.receive", wh.site_id)})


class WarehouseActiveView(InvBase):
    required_permission = "inventory.warehouse.manage"
    http_method_names = ["post"]

    def post(self, request, pk):
        wh = or404(selectors.get_warehouse, request.membership, request.organization, pk)
        need(request, "inventory.warehouse.manage", wh.site_id)
        try:
            active = request.POST.get("active") == "1"
            services.set_warehouse_active(wh, active, actor=request.user, request=request)
            messages.success(request, "Warehouse activated." if active else "Warehouse deactivated.")
        except DomainError as exc:
            _fail(request, exc)
        return redirect("inventory:warehouse", pk=wh.pk)


# --- stock -------------------------------------------------------------------------------------------------------


class StockListView(InvBase):
    def get(self, request):
        m, org = request.membership, request.organization
        qs = selectors.filter_balances(selectors.balances_for(m, org), request.GET)
        return render(request, "inventory/stock.html", {
            "page": _paged(request, qs), "filters": request.GET,
            "warehouses": selectors.warehouses_for(m, org).order_by("code"),
            "can_receive": rbac.has_permission_anywhere(m, "inventory.receive"),
            "can_transfer": rbac.has_permission_anywhere(m, "inventory.transfer")})


class StockDetailView(InvBase):
    def get(self, request, pk):
        m, org = request.membership, request.organization
        bal = or404(selectors.get_balance, m, org, pk)
        site = bal.warehouse.site_id
        can_adjust = rbac.has_permission(m, "inventory.adjust", site)
        return render(request, "inventory/stock_detail.html", {
            "bal": bal, "movements": _paged(request, selectors.movements_for(m, org).filter(balance=bal), 15),
            "can_adjust": can_adjust, "can_receive": rbac.has_permission(m, "inventory.receive", site),
            "can_transfer": rbac.has_permission(m, "inventory.transfer", site),
            "adjust_form": AdjustForm() if can_adjust else None,
            "levels_form": LevelsForm(initial={"min_level": bal.min_level, "max_level": bal.max_level,
                                               "reorder_quantity": bal.reorder_quantity}) if can_adjust else None,
            "reservations": selectors.reservations_for(m, org).filter(
                warehouse=bal.warehouse, part=bal.part, status="ACTIVE")})


class StockAdjustView(InvBase):
    required_permission = "inventory.adjust"
    http_method_names = ["post"]

    def post(self, request, pk):
        bal = or404(selectors.get_balance, request.membership, request.organization, pk)
        need(request, "inventory.adjust", bal.warehouse.site_id)
        form = AdjustForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Enter a non-zero adjustment and a reason.")
        else:
            try:
                services.adjust(bal.warehouse, bal.part, form.cleaned_data["delta"],
                                reason=form.cleaned_data["reason"], actor=request.user, request=request)
                messages.success(request, "Stock adjusted.")
            except DomainError as exc:
                _fail(request, exc)
        return redirect("inventory:stock_detail", pk=bal.pk)


class StockLevelsView(InvBase):
    required_permission = "inventory.adjust"
    http_method_names = ["post"]

    def post(self, request, pk):
        bal = or404(selectors.get_balance, request.membership, request.organization, pk)
        need(request, "inventory.adjust", bal.warehouse.site_id)
        form = LevelsForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Levels must be zero or more.")
        else:
            try:
                services.set_levels(bal.warehouse, bal.part, actor=request.user, request=request,
                                    **form.cleaned_data)
                messages.success(request, "Levels saved.")
            except DomainError as exc:
                _fail(request, exc)
        return redirect("inventory:stock_detail", pk=bal.pk)


class ReceiveView(InvBase):
    required_permission = "inventory.receive"
    crumbs = [("Stock", "/app/inventory/stock/"), ("Receive", None)]

    def _form(self, request, data=None):
        return ReceiveForm(data, warehouses=selectors.warehouses_for(
            request.membership, request.organization, "inventory.receive").filter(is_active=True).order_by("code"),
            parts=selectors.parts_for(request.organization).filter(is_active=True).order_by("part_number"),
            initial={"warehouse": request.GET.get("warehouse"), "part": request.GET.get("part")} if not data else None)

    def get(self, request):
        return form_page(request, title="Receive stock", form=self._form(request), submit="Receive",
                         cancel_url="/app/inventory/stock/", crumbs=self.crumbs,
                         subtitle="Books goods into a warehouse and writes a RECEIPT movement.")

    def post(self, request):
        form = self._form(request, request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                need(request, "inventory.receive", d["warehouse"].site_id)
                mv = services.receive(d["warehouse"], d["part"], d["quantity"], actor=request.user,
                                      reference=d["reference"], reason=d["reason"], request=request)
                messages.success(request, f"Received {mv.quantity:g} x {d['part'].part_number}.")
                return redirect("inventory:stock_detail", pk=mv.balance_id)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return form_page(request, title="Receive stock", form=form, submit="Receive",
                         cancel_url="/app/inventory/stock/", crumbs=self.crumbs, status=400)


class TransferView(InvBase):
    required_permission = "inventory.transfer"
    crumbs = [("Stock", "/app/inventory/stock/"), ("Transfer", None)]

    def _form(self, request, data=None):
        return TransferForm(data, warehouses=selectors.warehouses_for(
            request.membership, request.organization, "inventory.transfer").filter(is_active=True).order_by("code"),
            parts=selectors.parts_for(request.organization).filter(is_active=True).order_by("part_number"))

    def get(self, request):
        return form_page(request, title="Transfer stock", form=self._form(request), submit="Transfer",
                         cancel_url="/app/inventory/stock/", crumbs=self.crumbs,
                         subtitle="Moves available (not reserved) stock; writes a TRANSFER_OUT and a TRANSFER_IN movement.")

    def post(self, request):
        form = self._form(request, request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                need(request, "inventory.transfer", d["source"].site_id)
                need(request, "inventory.transfer", d["target"].site_id)
                services.transfer(d["source"], d["target"], d["part"], d["quantity"], actor=request.user,
                                  reason=d["reason"], request=request)
                messages.success(request, f"Transferred {d['quantity']:g} x {d['part'].part_number}.")
                return redirect("inventory:movements")
            except DomainError as exc:
                form.add_error(None, exc.message)
        return form_page(request, title="Transfer stock", form=form, submit="Transfer",
                         cancel_url="/app/inventory/stock/", crumbs=self.crumbs, status=400)


# --- reservations / movements -------------------------------------------------------------------------------------


class ReservationListView(InvBase):
    def get(self, request):
        m, org = request.membership, request.organization
        params = request.GET.copy()
        if "status" not in params:
            params["status"] = "ACTIVE"
        qs = selectors.filter_reservations(selectors.reservations_for(m, org), params)
        return render(request, "inventory/reservations.html", {
            "page": _paged(request, qs), "filters": params, "statuses": PartReservation.Status.choices})


class MovementListView(InvBase):
    def get(self, request):
        m, org = request.membership, request.organization
        qs = selectors.filter_movements(selectors.movements_for(m, org), request.GET)
        return render(request, "inventory/movements.html", {
            "page": _paged(request, qs), "filters": request.GET, "types": StockMovement.Type.choices,
            "warehouses": selectors.warehouses_for(m, org).order_by("code")})


# --- work-order material view (M06 integration) ----------------------------------------------------------------------


def _wo(request, pk):
    return or404(wo_selectors.get_work_order, request.membership, request.organization, pk)


def line_ops(membership, wo, line) -> dict:
    """What this caller may press on a line right now (the POST re-checks permission and state)."""
    site = wo.site_id
    has = lambda code: rbac.has_permission(membership, code, site)  # noqa: E731
    open_ = line.status not in TERMINAL
    held = services.held_quantity(line) if open_ else 0
    return {
        "reserve": open_ and has("inventory.reserve") and line.remaining_to_issue - held > 0
        and wo.status in ("PLANNED", "ASSIGNED", "DISPATCHED", "IN_PROGRESS", "ON_HOLD"),
        "release": open_ and has("inventory.reserve") and held > 0,
        "issue": open_ and has("inventory.issue") and line.remaining_to_issue > 0
        and wo.status in ("PLANNED", "ASSIGNED", "DISPATCHED", "IN_PROGRESS", "ON_HOLD"),
        "consume": open_ and has("inventory.consume") and line.outstanding > 0
        and wo.status in ("IN_PROGRESS", "ON_HOLD", "COMPLETED", "SUPERVISOR_REVIEW"),
        "return": open_ and has("inventory.return") and line.outstanding > 0,
        "reconcile": open_ and has("inventory.reconcile") and line.status in ("CONSUMED", "RETURNED"),
        "cancel": (has("inventory.reconcile") or (line.status == "REQUESTED" and has("inventory.request")))
        and line.status in ("REQUESTED", "RESERVED", "RETURNED"),
        "held": held,
    }


class WorkOrderPartsView(TenantPermissionMixin, View):
    required_permission = "work_order.view_assigned"

    def get(self, request, pk):
        m, org = request.membership, request.organization
        wo = _wo(request, pk)
        lines = list(selectors.lines_for_work_order(org, wo))
        for ln in lines:
            ln.ops = line_ops(m, wo, ln)
        can_request = (rbac.has_permission(m, "inventory.request", wo.site_id) and services.can_request(wo, m)
                       and wo.status in ("DRAFT", "PLANNED", "ASSIGNED", "DISPATCHED", "IN_PROGRESS", "ON_HOLD"))
        can_view_stock = rbac.has_permission(m, "inventory.view", wo.site_id)
        return render(request, "inventory/work_order_parts.html", {
            "wo": wo, "lines": lines, "can_request": can_request,
            "request_form": RequestPartForm(parts=selectors.parts_for(org).filter(is_active=True).order_by(
                "part_number")) if can_request else None,
            "reserve_whs": selectors.usable_warehouses(m, org, wo.site_id, "inventory.reserve"),
            "issue_whs": selectors.usable_warehouses(m, org, wo.site_id, "inventory.issue"),
            "movements": selectors.movements_for(m, org).filter(work_order=wo)[:30] if can_view_stock else [],
            "can_view_stock": can_view_stock,
            "legacy_materials": wo_selectors.materials_for(org, wo).filter(part_line__isnull=True)})


class PartRequestView(TenantPermissionMixin, View):
    required_permission = "inventory.request"
    http_method_names = ["post"]

    def post(self, request, pk):
        m, org = request.membership, request.organization
        wo = _wo(request, pk)
        need(request, "inventory.request", wo.site_id)
        form = RequestPartForm(request.POST, parts=selectors.parts_for(org).order_by("part_number"))
        if not form.is_valid():
            messages.error(request, "Choose a part and enter a quantity above zero.")
        else:
            d = form.cleaned_data
            try:
                services.request_part(wo, d["part"], d["quantity"], actor=request.user, membership=m,
                                      notes=d["notes"], request=request)
                messages.success(request, "Part requested.")
            except DomainError as exc:
                _fail(request, exc)
        return redirect(safe_next(request, f"/app/work-orders/{wo.pk}/parts/"))


class LineActionView(TenantPermissionMixin, View):
    required_permission = "work_order.view_assigned"
    http_method_names = ["post"]

    def post(self, request, pk, action):
        m, org = request.membership, request.organization
        if action not in LINE_ACTIONS:
            messages.error(request, "Unknown action.")
            return redirect("inventory:movements")
        line = or404(selectors.get_line, m, org, pk)
        wo = line.work_order
        if LINE_ACTIONS[action]:
            need(request, LINE_ACTIONS[action], wo.site_id)
        back = safe_next(request, f"/app/work-orders/{wo.pk}/parts/")
        try:
            self._apply(request, action, line, wo)
        except DomainError as exc:
            _fail(request, exc)
        return redirect(back)

    def _apply(self, request, action, line, wo):
        m, org, kw = request.membership, request.organization, {"actor": request.user, "request": request}
        if action in ("reserve", "issue"):
            form_cls = ReserveForm if action == "reserve" else IssueForm
            code = LINE_ACTIONS[action]
            form = form_cls(request.POST, warehouses=selectors.usable_warehouses(m, org, wo.site_id, code))
            if not form.is_valid():
                messages.error(request, "Choose a warehouse and enter a valid quantity.")
                return
            d = form.cleaned_data
            fn = services.reserve if action == "reserve" else services.issue
            fn(line, d["warehouse"], d.get("quantity"), **kw)
            messages.success(request, "Stock reserved." if action == "reserve" else "Stock issued.")
        elif action in ("release", "consume", "return"):
            form = QuantityForm(request.POST)
            if not form.is_valid():
                messages.error(request, "Enter a valid quantity.")
                return
            q = form.cleaned_data.get("quantity")
            if action == "release":
                services.release(line, q, **kw)
                messages.success(request, "Reservation released.")
            elif action == "consume":
                if q is None:
                    messages.error(request, "Enter the quantity used.")
                    return
                services.consume(line, q, membership=m, **kw)
                messages.success(request, "Consumption recorded.")
            else:
                if q is None:
                    messages.error(request, "Enter the quantity to return.")
                    return
                services.return_stock(line, q, reason=form.cleaned_data["reason"], **kw)
                messages.success(request, "Stock returned.")
        elif action == "reconcile":
            services.reconcile(line, **kw)
            messages.success(request, "Line reconciled.")
        elif action == "cancel":
            services.cancel_line(line, membership=m, **kw)
            messages.success(request, "Line cancelled.")
