"""M02/M03 HTML views. Same chain as M01: login -> membership -> permission gate -> organization + site-scope
restricted lookup (404) -> permission for the asset's site (403) -> service (validation, transaction, audit)."""
from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View

from apps.core.exceptions import DomainError, NotFound
from apps.rbac import services as rbac
from apps.sites import selectors as site_selectors
from apps.sites.views import need, or404
from apps.ui.mixins import TenantPermissionMixin

from . import attributes, hierarchy, selectors, services
from .forms import (
    AssetForm,
    CategoryForm,
    ComponentAddForm,
    ComponentEditForm,
    ComponentMoveForm,
    DocumentForm,
    MeterForm,
    NewChildForm,
    ReadingForm,
)
from .models import Asset, AssetComponent
from .workflow import ASSET_STATUS, BUTTON_STYLES, TERMINAL_STATES

TABS = [("overview", "Overview"), ("documents", "Documents"), ("meters", "Meters"), ("history", "History"),
        ("hierarchy", "Hierarchy"), ("coverage", "Coverage"), ("labels", "Labels")]


def _asset(request, pk, code=None) -> Asset:
    asset = or404(selectors.get_asset, request.membership, request.organization, pk)
    if code:
        need(request, code, asset.site_id)
    return asset


def _back(asset, tab):
    return redirect(f"/app/assets/{asset.pk}/?tab={tab}")


class AssetBase(TenantPermissionMixin, View):
    pass


FORM_KEYS = ("asset_tag", "name", "category", "site", "zone", "manufacturer", "model", "serial_number",
             "purchase_date", "commission_date", "owner", "warranty_ref", "description")


def _prefill(request) -> dict:
    """Form values carried in the query string when the category changes (the page reloads so the new category's
    custom attributes appear without losing what was typed)."""
    initial = {k: request.GET[k] for k in request.GET if k in FORM_KEYS}
    attrs = {k[5:]: v for k, v in request.GET.items() if k.startswith("attr_")}
    if attrs:
        initial["attributes"] = attrs
    return initial


def _category_for(request, source):
    """The (active) category named in ``source`` ('category' key) inside the caller's organization, or None."""
    raw = str(source.get("category") or "").strip() if hasattr(source, "get") else ""
    pk = selectors._uuid_or_none(raw)
    if pk is None:
        return None
    return selectors.categories_for(request.organization).filter(pk=pk).first()


# --- list / create / edit --------------------------------------------------------------------------------------


class AssetListView(AssetBase):
    required_permission = "asset.view"

    def get(self, request):
        m, org = request.membership, request.organization
        qs = selectors.filter_assets(selectors.assets_for(m, org).select_related("parent_link__parent"), request.GET)
        page = Paginator(qs, 20).get_page(request.GET.get("page"))
        return render(request, "assets/list.html", {
            "page": page, "filters": request.GET,
            "sites": site_selectors.sites_for(m, org, "asset.view").order_by("code"),
            "categories": selectors.categories_for(org), "statuses": Asset.Status.choices,
            "can_create": rbac.has_permission_anywhere(m, "asset.create"),
            "can_categories": rbac.has_permission(m, "asset.category.manage")})


class AssetCreateView(AssetBase):
    required_permission = "asset.create"

    def _sites(self, request):
        return site_selectors.sites_for(request.membership, request.organization, "asset.create")

    def _parent(self, request):
        pid = request.GET.get("parent") or request.POST.get("parent")
        if not pid:
            return None
        parent = _asset(request, pid, "asset.hierarchy.manage")
        return parent

    def _page(self, request, form, parent, rel_form=None, status=200):
        title = f"New component of {parent.asset_tag}" if parent else "Register asset"
        return render(request, "assets/form.html", {
            "title": title, "form": form, "rel_form": rel_form, "parent": parent, "submit": "Register asset",
            "cancel_url": f"/app/assets/{parent.pk}/?tab=hierarchy" if parent else "/app/assets/",
            "crumbs": [("Assets", "/app/assets/"), (title, None)]}, status=status)

    def get(self, request):
        parent = self._parent(request)
        initial = {}
        if parent:
            initial = {"site": parent.site_id, "zone": parent.zone_id, "category": parent.category_id}
        elif request.GET.get("site"):
            initial["site"] = request.GET["site"]
        initial.update(_prefill(request))
        form = AssetForm(initial=initial, sites=self._sites(request), org=request.organization,
                         category=_category_for(request, initial))
        return self._page(request, form, parent, NewChildForm() if parent else None)

    def post(self, request):
        parent = self._parent(request)
        form = AssetForm(request.POST, sites=self._sites(request), org=request.organization,
                         category=_category_for(request, request.POST))
        rel_form = NewChildForm(request.POST) if parent else None
        if form.is_valid() and (rel_form is None or rel_form.is_valid()):
            d = dict(form.cleaned_data)
            refs = {k: d.pop(k) for k in ("site", "zone", "category", "owner")}
            try:
                with transaction.atomic():  # the child is registered and attached, or nothing is
                    asset = services.create_asset(request.organization, actor=request.user, request=request,
                                                  **refs, **d)
                    if parent:
                        hierarchy.add_component(parent, asset, actor=request.user, request=request,
                                                **rel_form.cleaned_data)
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(request, f"Asset {asset.asset_tag} registered.")
                return redirect("assets:detail", pk=asset.pk)
        return self._page(request, form, parent, rel_form, status=400)


class AssetEditView(AssetBase):
    required_permission = "asset.update"

    def _form(self, request, asset, data=None):
        sites = site_selectors.sites_for(request.membership, request.organization, "asset.update")
        reload = data is None and "category" in request.GET
        category = _category_for(request, data if data is not None else (request.GET if reload else {})) \
            or asset.category
        initial = None if data else {"attributes": asset.attributes if category.pk == asset.category_id else {},
            "asset_tag": asset.asset_tag, "name": asset.name, "category": asset.category_id, "site": asset.site_id,
            "zone": asset.zone_id, "manufacturer": asset.manufacturer, "model": asset.model,
            "serial_number": asset.serial_number, "purchase_date": asset.purchase_date,
            "commission_date": asset.commission_date, "owner": asset.owner_id, "warranty_ref": asset.warranty_ref,
            "description": asset.description}
        if reload:
            initial.update(_prefill(request))
        return AssetForm(data, initial=initial, sites=sites, org=request.organization, creating=False,
                         category=category)

    def _page(self, request, asset, form, status=200):
        return render(request, "assets/form.html", {
            "title": f"Edit {asset.asset_tag}", "form": form, "submit": "Save", "cancel_url": f"/app/assets/{asset.pk}/",
            "crumbs": [("Assets", "/app/assets/"), (asset.asset_tag, f"/app/assets/{asset.pk}/"), ("Edit", None)]},
            status=status)

    def get(self, request, pk):
        asset = _asset(request, pk, "asset.update")
        return self._page(request, asset, self._form(request, asset))

    def post(self, request, pk):
        asset = _asset(request, pk, "asset.update")
        form = self._form(request, asset, request.POST)
        if form.is_valid():
            d = dict(form.cleaned_data)
            refs = {k: d.pop(k) for k in ("site", "zone", "category", "owner")}
            try:
                services.update_asset(asset, actor=request.user, request=request, **refs, **d)
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(request, "Asset saved.")
                return redirect("assets:detail", pk=asset.pk)
        return self._page(request, asset, form, status=400)


# --- detail ----------------------------------------------------------------------------------------------------


class AssetDetailView(AssetBase):
    required_permission = "asset.view"

    def get(self, request, pk):
        asset = _asset(request, pk)
        m, org = request.membership, request.organization
        tab = request.GET.get("tab", "overview")
        if tab not in dict(TABS):
            tab = "overview"
        can = {c.replace(".", "_"): rbac.has_permission(m, c, asset.site_id) for c in (
            "asset.update", "asset.change_status", "asset.history.view", "asset.document.manage",
            "asset.meter.record", "asset.hierarchy.manage", "incident.create")}
        transitions = []
        if can["asset_change_status"]:
            transitions = [{"action": t.action, "label": t.label, "reason": True,
                            "url": f"/app/assets/{asset.pk}/transition/{t.action}/",
                            "style": BUTTON_STYLES.get(t.action, "primary"),
                            "confirm": f"{t.label}: the asset will become {t.target.replace('_', ' ').title()}."
                            if t.target in TERMINAL_STATES else ""}
                           for t in ASSET_STATUS.available(asset.status)]
        ctx = {"asset": asset, "tab": tab, "tabs": TABS, "can": can, "transitions": transitions,
               "custom_attributes": attributes.display(asset.category.attribute_definitions, asset.attributes),
               "terminal": asset.status in TERMINAL_STATES, "parent_link": getattr(asset, "parent_link", None)}
        if tab == "overview" and rbac.has_permission(m, "contract.view", asset.site_id):
            from apps.contracts import services as contract_services

            # M10: the live coverage (agreements in force) next to the free-text warranty reference
            ctx["coverage_now"] = [e for e in contract_services.evaluate(org, asset).entries if e.status == "COVERING"]
        if tab == "documents":
            ctx["documents"] = selectors.documents_for(org, asset)
            ctx["doc_form"] = DocumentForm()
        elif tab == "meters":
            ctx["meters"] = selectors.meters_for(m, org).filter(asset=asset)
            ctx["meter_form"], ctx["reading_form"] = MeterForm(), ReadingForm()
        elif tab == "history":
            if can["asset_history_view"]:
                ctx["status_history"] = selectors.status_history_for(m, org, asset)
                ctx["location_history"] = selectors.location_history_for(org, asset)
                ctx["change_log"] = selectors.change_log(org, asset)
        elif tab == "hierarchy":
            ctx.update(self._hierarchy_ctx(request, asset, can))
        return render(request, "assets/detail.html", ctx)

    def _hierarchy_ctx(self, request, asset, can):
        ctx = {}
        above = hierarchy.ancestors(asset)
        ctx["ancestors"] = list(reversed(above))
        if can["asset_hierarchy_manage"] and asset.status not in TERMINAL_STATES:
            exclude_ids = {asset.pk, *(a.pk for a in above)}
            base = selectors.asset_choices(request.membership, request.organization, "asset.hierarchy.manage",
                                           site=asset.site)
            ctx["add_form"] = ComponentAddForm(candidates=base.filter(parent_link__isnull=True).exclude(
                pk__in=exclude_ids), initial={"relationship_type": "COMPONENT", "quantity": 1})
            if getattr(asset, "parent_link", None) is not None:
                link = asset.parent_link
                ctx["edit_link_form"] = ComponentEditForm(initial={
                    "relationship_type": link.relationship_type, "quantity": link.quantity,
                    "part_number": link.part_number, "notes": link.notes})
                below = {n["asset"].pk for n in hierarchy.flatten(hierarchy.build_tree(asset))}
                ctx["move_form"] = ComponentMoveForm(candidates=base.exclude(pk__in=below))
        return ctx


class AssetTreeView(AssetBase):
    """HTMX partial: the whole tree this asset belongs to (from its root), current asset highlighted."""

    required_permission = "asset.view"

    def get(self, request, pk):
        asset = _asset(request, pk)
        root = hierarchy.root_of(asset)
        tree = hierarchy.build_tree(root)
        can = rbac.has_permission(request.membership, "asset.hierarchy.manage", asset.site_id)
        return render(request, "assets/_tree.html", {"node": tree, "current": asset, "can_manage": can})


# --- state-changing actions (POST only) -------------------------------------------------------------------------


class AssetStatusView(AssetBase):
    required_permission = "asset.change_status"
    http_method_names = ["post"]

    def post(self, request, pk, action):
        asset = _asset(request, pk, "asset.change_status")
        try:
            services.change_status(asset, action=action, reason=request.POST.get("reason", ""),
                                   actor=request.user, request=request)
            messages.success(request, "Status updated.")
        except DomainError as exc:
            messages.error(request, exc.message)
        return redirect("assets:detail", pk=asset.pk)


class DocumentUploadView(AssetBase):
    required_permission = "asset.document.manage"
    http_method_names = ["post"]

    def post(self, request, pk):
        asset = _asset(request, pk, "asset.document.manage")
        form = DocumentForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Choose a file to upload.")
            return _back(asset, "documents")
        d = form.cleaned_data
        try:
            services.add_document(asset, d["file"], title=d["title"], doc_type=d["doc_type"], actor=request.user,
                                  request=request)
            messages.success(request, "Document uploaded.")
        except DomainError as exc:
            messages.error(request, exc.message)
        return _back(asset, "documents")


class DocumentRemoveView(AssetBase):
    required_permission = "asset.document.manage"
    http_method_names = ["post"]

    def post(self, request, pk):
        doc = or404(selectors.get_document, request.membership, request.organization, pk)
        need(request, "asset.document.manage", doc.asset.site_id)
        try:
            services.remove_document(doc, reason=request.POST.get("reason", ""), actor=request.user, request=request)
            messages.success(request, "Document removed.")
        except DomainError as exc:
            messages.error(request, exc.message)
        return _back(doc.asset, "documents")


class MeterCreateView(AssetBase):
    required_permission = "asset.update"
    http_method_names = ["post"]

    def post(self, request, pk):
        asset = _asset(request, pk, "asset.update")
        form = MeterForm(request.POST)
        try:
            if not form.is_valid():
                raise DomainError("Enter a meter name and unit.")
            services.create_meter(asset, name=form.cleaned_data["name"], unit=form.cleaned_data["unit"],
                                  actor=request.user, request=request)
            messages.success(request, "Meter added.")
        except DomainError as exc:
            messages.error(request, exc.message)
        return _back(asset, "meters")


class MeterReadingView(AssetBase):
    required_permission = "asset.meter.record"
    http_method_names = ["post"]

    def post(self, request, pk):
        meter = or404(selectors.get_meter, request.membership, request.organization, pk)
        need(request, "asset.meter.record", meter.asset.site_id)
        form = ReadingForm(request.POST)
        try:
            if not form.is_valid():
                raise DomainError("Enter a valid, non-negative reading value.")
            services.record_reading(meter, value=form.cleaned_data["value"], read_at=form.cleaned_data["read_at"],
                                    notes=form.cleaned_data["notes"], actor=request.user, request=request)
            messages.success(request, f"Reading recorded for {meter.name}.")
        except DomainError as exc:
            messages.error(request, exc.message)
        return _back(meter.asset, "meters")


class MeterToggleView(AssetBase):
    required_permission = "asset.update"
    http_method_names = ["post"]

    def post(self, request, pk):
        meter = or404(selectors.get_meter, request.membership, request.organization, pk)
        need(request, "asset.update", meter.asset.site_id)
        try:
            services.set_meter_active(meter, active=request.POST.get("action") == "activate", actor=request.user,
                                      request=request)
            messages.success(request, "Meter updated.")
        except DomainError as exc:
            messages.error(request, exc.message)
        return _back(meter.asset, "meters")


class ComponentAddView(AssetBase):
    required_permission = "asset.hierarchy.manage"
    http_method_names = ["post"]

    def post(self, request, pk):
        asset = _asset(request, pk, "asset.hierarchy.manage")
        base = selectors.asset_choices(request.membership, request.organization, "asset.hierarchy.manage")
        form = ComponentAddForm(request.POST, candidates=base)
        try:
            if not form.is_valid():
                raise DomainError("Choose an asset and a valid relationship.")
            d = dict(form.cleaned_data)
            child = d.pop("child")
            hierarchy.add_component(asset, child, actor=request.user, request=request, **d)
            messages.success(request, f"{child.asset_tag} added as a {d['relationship_type'].lower().replace('_', ' ')}.")
        except DomainError as exc:
            messages.error(request, exc.message)
        return _back(asset, "hierarchy")


def _link(request, pk) -> AssetComponent:
    link = or404(selectors.get_component, request.membership, request.organization, pk)
    need(request, "asset.hierarchy.manage", link.parent.site_id)
    return link


class ComponentEditView(AssetBase):
    required_permission = "asset.hierarchy.manage"
    http_method_names = ["post"]

    def post(self, request, pk):
        link = _link(request, pk)
        form = ComponentEditForm(request.POST)
        try:
            if not form.is_valid():
                raise DomainError("Check the relationship type and quantity.")
            hierarchy.update_component(link, actor=request.user, request=request, **form.cleaned_data)
            messages.success(request, "Relationship updated.")
        except DomainError as exc:
            messages.error(request, exc.message)
        return _back(link.child, "hierarchy")


class ComponentMoveView(AssetBase):
    required_permission = "asset.hierarchy.manage"
    http_method_names = ["post"]

    def post(self, request, pk):
        link = _link(request, pk)
        child = link.child
        base = selectors.asset_choices(request.membership, request.organization, "asset.hierarchy.manage")
        form = ComponentMoveForm(request.POST, candidates=base)
        try:
            if not form.is_valid():
                raise DomainError("Choose a valid new parent.")
            hierarchy.move_component(link, form.cleaned_data["parent"], actor=request.user, request=request)
            messages.success(request, f"{child.asset_tag} moved.")
        except DomainError as exc:
            messages.error(request, exc.message)
        return _back(child, "hierarchy")


class ComponentRemoveView(AssetBase):
    required_permission = "asset.hierarchy.manage"
    http_method_names = ["post"]

    def post(self, request, pk):
        link = _link(request, pk)
        child, parent = link.child, link.parent
        try:
            hierarchy.remove_component(link, actor=request.user, request=request)
            messages.success(request, f"{child.asset_tag} detached from {parent.asset_tag}.")
        except DomainError as exc:
            messages.error(request, exc.message)
        back = request.POST.get("next_asset")
        return _back(child if back == "child" else parent, "hierarchy")


# --- categories ------------------------------------------------------------------------------------------------


class CategoryView(AssetBase):
    required_permission = "asset.view"

    def get(self, request, form=None):
        return render(request, "assets/categories.html", {
            "categories": selectors.categories_for(request.organization), "form": form or CategoryForm(),
            "can_manage": rbac.has_permission(request.membership, "asset.category.manage")})

    def post(self, request):
        need(request, "asset.category.manage")
        org = request.organization
        form = CategoryForm(request.POST)
        if not form.is_valid():
            messages.error(request, next(iter(form.errors.values()))[0] if form.errors else "Enter a category name.")
            return redirect("assets:categories")
        d = form.cleaned_data
        try:
            if request.POST.get("category"):
                try:
                    cat = site_selectors.scoped_get(selectors.categories_for(org), request.POST["category"],
                                                    "Category")
                except NotFound as exc:
                    raise Http404 from exc
                services.update_category(cat, actor=request.user, request=request, name=d["name"],
                                         description=d["description"], is_active=d["is_active"],
                                         attribute_definitions=d["attribute_text"])
                messages.success(request, "Category saved.")
            else:
                services.create_category(org, name=d["name"], description=d["description"],
                                         attribute_definitions=d["attribute_text"], actor=request.user,
                                         request=request)
                messages.success(request, "Category created.")
        except DomainError as exc:
            messages.error(request, exc.message)
        return redirect("assets:categories")
