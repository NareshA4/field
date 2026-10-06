"""M10 HTML views. Chain: login -> ACTIVE membership -> permission gate -> organization + site-scoped lookup (404) ->
permission for the agreement's site (403) -> service (validation, locks, audit). Pages show real database state;
every button posts to an endpoint that calls a service."""
from __future__ import annotations

import datetime

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views import View

from apps.assets import selectors as asset_selectors
from apps.core.exceptions import DomainError
from apps.rbac import services as rbac
from apps.sites import selectors as site_selectors
from apps.sites.views import form_page, need, or404
from apps.sla.models import SLAProfile
from apps.ui.mixins import TenantPermissionMixin
from apps.workorders import selectors as wo_selectors

from . import selectors, services
from .forms import AddAssetForm, AgreementForm, ProviderForm, ReasonForm, RenewForm
from .models import CoverageAgreement

CRUMB = ("Warranties & contracts", "/app/contracts/agreements/")


class CBase(TenantPermissionMixin, View):
    required_permission = "contract.view"


def _paged(request, qs, size=20):
    return Paginator(qs, size).get_page(request.GET.get("page"))


def _fail(request, exc):
    messages.error(request, exc.message if isinstance(exc, DomainError) else str(exc))


def _redirect_next(request, default):
    nxt = request.POST.get("next") or ""
    return redirect(nxt if nxt.startswith("/app/contracts/") else default)


# --- agreements ------------------------------------------------------------------------------------------------------


class AgreementListView(CBase):
    def get(self, request):
        m, org = request.membership, request.organization
        qs = selectors.filter_agreements(selectors.agreements_for(m, org), request.GET)
        today = datetime.date.today()
        page = _paged(request, qs)
        for a in page:
            a.current_state = a.state(today)
        return render(request, "contracts/agreements.html", {
            "page": page, "filters": request.GET, "kinds": CoverageAgreement.Kind.choices,
            "states": ("ACTIVE", "UPCOMING", "EXPIRED", "INACTIVE"),
            "sites": site_selectors.sites_for(m, org, "contract.view").order_by("code"),
            "providers": selectors.providers_for(org).order_by("name"),
            "can_create": rbac.has_permission_anywhere(m, "contract.create")})


class AgreementCreateView(CBase):
    required_permission = "contract.create"
    crumbs = [CRUMB, ("New", None)]

    def _form(self, request, data=None):
        m, org = request.membership, request.organization
        sites = site_selectors.sites_for(m, org, "contract.create").order_by("code")
        scope = rbac.site_scope(m, "contract.create")
        assets = scope.filter(asset_selectors.assets_for(m, org), "site_id").order_by("asset_tag")
        initial = None
        if data is None:
            initial = {k: request.GET.get(k) for k in ("assets", "site") if request.GET.get(k)}
            if request.GET.get("assets"):
                initial["assets"] = [request.GET["assets"]]
                a = assets.filter(pk=request.GET["assets"]).first() if selectors._uuid_or_none(
                    request.GET["assets"]) else None
                initial["site"] = a.site_id if a else None
        return AgreementForm(data, providers=selectors.providers_for(org).filter(is_active=True), sites=sites,
                             assets=assets, sla_profiles=SLAProfile.objects.for_organization(org).filter(
                                 is_active=True).order_by("name"), initial=initial)

    def get(self, request):
        return form_page(request, title="New warranty / AMC / contract", form=self._form(request),
                         submit="Create agreement", cancel_url="/app/contracts/agreements/", crumbs=self.crumbs)

    def post(self, request):
        form = self._form(request, request.POST)
        if form.is_valid():
            d = dict(form.cleaned_data)
            try:
                need(request, "contract.create", d["site"].pk)
                for a in d["assets"]:
                    need(request, "contract.create", a.site_id)
                ag = services.create_agreement(request.organization, actor=request.user, request=request,
                                               assets=list(d.pop("assets")), **d)
                messages.success(request, f"{ag.get_kind_display()} {ag.reference} created.")
                return redirect("contracts:agreement", pk=ag.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return form_page(request, title="New warranty / AMC / contract", form=form, submit="Create agreement",
                         cancel_url="/app/contracts/agreements/", crumbs=self.crumbs, status=400)


class AgreementBase(CBase):
    def ag(self, request, pk, code=None):
        ag = or404(selectors.get_agreement, request.membership, request.organization, pk)
        if code:
            need(request, code, ag.site_id)
        return ag


class AgreementDetailView(AgreementBase):
    def get(self, request, pk):
        ag = self.ag(request, pk)
        m, org = request.membership, request.organization
        can_update = rbac.has_permission(m, "contract.update", ag.site_id)
        add_form = None
        if can_update:
            add_form = AddAssetForm(assets=asset_selectors.assets_for(m, org).filter(site_id=ag.site_id).exclude(
                coverage_links__agreement=ag).order_by("asset_tag"))
        return render(request, "contracts/agreement.html", {
            "ag": ag, "state": ag.state(), "links": selectors.covered_assets_for(ag),
            "exclusions": sorted(e.get_work_type_display() if hasattr(e, "get_work_type_display") else e.work_type
                                 for e in ag.exclusions.all()),
            "renewal": CoverageAgreement.objects.for_organization(org).filter(renewed_from=ag).first(),
            "can_update": can_update, "add_form": add_form, "reason_form": ReasonForm(),
            "days_left": (ag.end_date - datetime.date.today()).days})


class AgreementEditView(AgreementBase):
    required_permission = "contract.update"

    def _form(self, request, ag, data=None):
        org = request.organization
        initial = None if data is not None else {
            "reference": ag.reference, "title": ag.title, "provider": ag.provider_id, "start_date": ag.start_date,
            "end_date": ag.end_date, "terms": ag.terms, "exclusion_notes": ag.exclusion_notes,
            "sla_terms": ag.sla_terms, "sla_profile": ag.sla_profile_id, "renewal_alert_days": ag.renewal_alert_days,
            "excluded_work_types": [e.work_type for e in ag.exclusions.all()]}
        providers = selectors.providers_for(org).filter(is_active=True) | selectors.providers_for(org).filter(
            pk=ag.provider_id)
        profiles = SLAProfile.objects.for_organization(org).filter(is_active=True) | SLAProfile.objects.for_organization(
            org).filter(pk=ag.sla_profile_id)
        return AgreementForm(data, providers=providers, sites=None, assets=None, sla_profiles=profiles.order_by("name"),
                             editing=True, initial=initial)

    def _page(self, request, ag, form, status=200):
        return form_page(request, title=f"Edit {ag.reference}", form=form, submit="Save", status=status,
                         cancel_url=f"/app/contracts/agreements/{ag.pk}/",
                         crumbs=[CRUMB, (ag.reference, f"/app/contracts/agreements/{ag.pk}/"), ("Edit", None)])

    def get(self, request, pk):
        ag = self.ag(request, pk, "contract.update")
        return self._page(request, ag, self._form(request, ag))

    def post(self, request, pk):
        ag = self.ag(request, pk, "contract.update")
        form = self._form(request, ag, request.POST)
        if form.is_valid():
            try:
                services.update_agreement(ag, actor=request.user, request=request, **form.cleaned_data)
                messages.success(request, "Agreement saved.")
                return redirect("contracts:agreement", pk=ag.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self._page(request, ag, form, 400)


class AgreementActionView(AgreementBase):
    http_method_names = ["post"]

    def post(self, request, pk, action):
        ag = self.ag(request, pk, "contract.update")
        try:
            if action == "deactivate":
                form = ReasonForm(request.POST)
                if not form.is_valid():
                    raise DomainError("A reason is required to deactivate an agreement.")
                services.set_agreement_active(ag, False, reason=form.cleaned_data["reason"], actor=request.user,
                                              request=request)
                messages.success(request, "Agreement deactivated.")
            elif action == "reactivate":
                services.set_agreement_active(ag, True, actor=request.user, request=request)
                messages.success(request, "Agreement reactivated.")
            elif action in ("add-asset", "remove-asset"):
                form = AddAssetForm(request.POST, assets=asset_selectors.assets_for(
                    request.membership, request.organization).filter(site_id=ag.site_id))
                if not form.is_valid():
                    raise DomainError("Choose an asset at this agreement's site.")
                asset = form.cleaned_data["asset"]
                if action == "add-asset":
                    services.add_asset(ag, asset, actor=request.user, request=request)
                    messages.success(request, f"{asset.asset_tag} is now covered.")
                else:
                    services.remove_asset(ag, asset, actor=request.user, request=request)
                    messages.success(request, f"{asset.asset_tag} removed from this agreement.")
            else:
                messages.error(request, "Unknown action.")
        except DomainError as exc:
            _fail(request, exc)
        return redirect("contracts:agreement", pk=ag.pk)


class AgreementRenewView(AgreementBase):
    required_permission = "contract.update"

    def _page(self, request, ag, form, status=200):
        return form_page(request, title=f"Renew {ag.reference}", form=form, submit="Create renewal", status=status,
                         subtitle=f"The renewal starts {ag.end_date + datetime.timedelta(days=1)} and keeps the "
                                  "same provider, terms, exclusions and assets.",
                         cancel_url=f"/app/contracts/agreements/{ag.pk}/",
                         crumbs=[CRUMB, (ag.reference, f"/app/contracts/agreements/{ag.pk}/"), ("Renew", None)])

    def get(self, request, pk):
        ag = self.ag(request, pk, "contract.update")
        return self._page(request, ag, RenewForm(initial={"reference": f"{ag.reference}-R"}))

    def post(self, request, pk):
        ag = self.ag(request, pk, "contract.update")
        form = RenewForm(request.POST)
        if form.is_valid():
            try:
                new = services.renew_agreement(ag, actor=request.user, request=request, **form.cleaned_data)
                messages.success(request, f"Renewal {new.reference} created.")
                return redirect("contracts:agreement", pk=new.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self._page(request, ag, form, 400)


class ExpiryView(CBase):
    def get(self, request):
        m, org = request.membership, request.organization
        try:
            days = max(1, min(int(request.GET.get("days", 90)), 365))
        except ValueError:
            days = 90
        today = datetime.date.today()
        rows = list(selectors.expiring(m, org, days, today).prefetch_related("covered_assets"))
        lapsed = list(selectors.agreements_for(m, org).filter(is_active=True, end_date__lt=today)
                      .order_by("-end_date")[:25])
        for a in rows:
            a.days_left = (a.end_date - today).days
            a.renewed = CoverageAgreement.objects.for_organization(org).filter(renewed_from=a).exists()
        return render(request, "contracts/expiry.html", {"rows": rows, "lapsed": lapsed, "days": days,
                                                         "windows": (30, 60, 90, 180, 365),
                                                         "can_update": rbac.has_permission_anywhere(
                                                             m, "contract.update")})


# --- providers -------------------------------------------------------------------------------------------------------


class ProviderListView(CBase):
    def get(self, request):
        org = request.organization
        qs = selectors.providers_for(org).order_by("name")
        q = (request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(name__icontains=q)
        return render(request, "contracts/providers.html", {
            "page": _paged(request, qs), "filters": request.GET,
            "can_create": rbac.has_permission_anywhere(request.membership, "contract.create"),
            "can_update": rbac.has_permission_anywhere(request.membership, "contract.update")})


class ProviderCreateView(CBase):
    required_permission = "contract.create"
    crumbs = [("Providers", "/app/contracts/providers/"), ("New", None)]

    def get(self, request):
        return form_page(request, title="New provider", form=ProviderForm(), submit="Create provider",
                         cancel_url="/app/contracts/providers/", crumbs=self.crumbs)

    def post(self, request):
        form = ProviderForm(request.POST)
        if form.is_valid():
            try:
                p = services.create_provider(request.organization, actor=request.user, request=request,
                                             **form.cleaned_data)
                messages.success(request, f"Provider {p.name} created.")
                return redirect("contracts:providers")
            except DomainError as exc:
                form.add_error(None, exc.message)
        return form_page(request, title="New provider", form=form, submit="Create provider",
                         cancel_url="/app/contracts/providers/", crumbs=self.crumbs, status=400)


class ProviderEditView(CBase):
    required_permission = "contract.update"

    def _page(self, request, p, form, status=200):
        return form_page(request, title=f"Edit {p.name}", form=form, submit="Save", status=status,
                         cancel_url="/app/contracts/providers/",
                         crumbs=[("Providers", "/app/contracts/providers/"), (p.name, None)])

    def get(self, request, pk):
        p = or404(selectors.get_provider, request.organization, pk)
        return self._page(request, p, ProviderForm(initial={
            f: getattr(p, f) for f in ("name", "contact_name", "email", "phone", "notes")}))

    def post(self, request, pk):
        p = or404(selectors.get_provider, request.organization, pk)
        form = ProviderForm(request.POST)
        if form.is_valid():
            try:
                services.update_provider(p, actor=request.user, request=request, **form.cleaned_data)
                messages.success(request, "Provider saved.")
                return redirect("contracts:providers")
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self._page(request, p, form, 400)


class ProviderActiveView(CBase):
    required_permission = "contract.update"
    http_method_names = ["post"]

    def post(self, request, pk):
        p = or404(selectors.get_provider, request.organization, pk)
        try:
            services.set_provider_active(p, request.POST.get("active") == "1", actor=request.user, request=request)
            messages.success(request, "Provider updated.")
        except DomainError as exc:
            _fail(request, exc)
        return redirect("contracts:providers")


# --- coverage panels (asset / work order) ----------------------------------------------------------------------------


class AssetCoveragePanelView(CBase):
    """HTMX fragment for the asset page. Live, backend-computed answer for a work type and date."""

    def get(self, request, asset_id):
        m, org = request.membership, request.organization
        asset = or404(asset_selectors.get_asset, m, org, asset_id)
        need(request, "contract.view", asset.site_id)
        work_type = (request.GET.get("work_type") or "").strip().upper()
        if work_type not in services.WORK_TYPES:
            work_type = ""
        try:
            on = datetime.date.fromisoformat(request.GET.get("date") or "")
        except ValueError:
            on = datetime.date.today()
        res = services.evaluate(org, asset, on, work_type)
        return render(request, "contracts/_asset_panel.html", {
            "asset": asset, "res": res, "work_types": services.WORK_TYPES, "work_type": work_type, "on": on,
            "can_create": rbac.has_permission(m, "contract.create", asset.site_id)})


class WorkOrderCoveragePanelView(CBase):
    """HTMX fragment for the work-order page: live evaluation + persisted check history."""

    def _render(self, request, wo, status=200):
        m = request.membership
        res = services.evaluate(request.organization, wo.asset, services.reference_date_for(wo), wo.work_type)
        checks = selectors.checks_for(m, request.organization).filter(work_order=wo)[:5]
        return render(request, "contracts/_wo_panel.html", {
            "wo": wo, "res": res, "checks": checks, "on": res.on,
            "can_check": rbac.has_permission(m, "contract.check", wo.site_id)}, status=status)

    def _wo(self, request, wo_id):
        wo = or404(wo_selectors.get_work_order, request.membership, request.organization, wo_id)
        need(request, "contract.view", wo.site_id)
        return wo

    def get(self, request, wo_id):
        return self._render(request, self._wo(request, wo_id))

    def post(self, request, wo_id):
        wo = self._wo(request, wo_id)
        need(request, "contract.check", wo.site_id)
        try:
            services.record_check(wo, actor=request.user, request=request)
        except DomainError as exc:
            return HttpResponse(exc.message, status=400)
        return self._render(request, wo)
