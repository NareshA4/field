"""M11 HTML views. Chain: login -> ACTIVE membership -> permission gate -> organization + site-scoped lookup (404) ->
permission for the tracked item's site (403) -> service (validation, row locks, audit). Every button posts to an
endpoint that calls a service; nothing is simulated in the browser."""
from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View

from apps.core.exceptions import DomainError
from apps.rbac import services as rbac
from apps.rbac.models import Role
from apps.sites import selectors as site_selectors
from apps.sites.models import Site
from apps.sites.views import form_page, need, or404
from apps.ui.mixins import TenantPermissionMixin

from . import selectors, services
from .forms import ProfileForm, RuleForm, TargetForm
from .models import EscalationRule, SLATarget


class SlaBase(TenantPermissionMixin, View):
    required_permission = "sla.view"


def _paged(request, qs, size=25):
    return Paginator(qs, size).get_page(request.GET.get("page"))


def _fail(request, exc):
    messages.error(request, exc.message if isinstance(exc, DomainError) else str(exc))


def _safe_next(request, default):
    nxt = request.POST.get("next") or ""
    return nxt if nxt.startswith("/app/") and not nxt.startswith("//") else default


# --- trackings / breaches ----------------------------------------------------------------------------------------------


class TrackingListView(SlaBase):
    def get(self, request):
        m, org = request.membership, request.organization
        qs = selectors.filter_trackings(selectors.trackings_for(m, org), request.GET)
        return render(request, "sla/trackings.html", {
            "page": _paged(request, qs), "filters": request.GET,
            "sites": site_selectors.sites_for(m, org, "sla.view").order_by("code"),
            "can_process": rbac.has_permission(m, "sla.process"),
            "summary": selectors.metrics(m, org)})


class TrackingDetailView(SlaBase):
    def get(self, request, pk):
        m, org = request.membership, request.organization
        t = or404(selectors.get_tracking, m, org, pk)
        return render(request, "sla/tracking_detail.html", {
            "t": t, "events": selectors.events_for(org, t), "breaches": t.breaches.all(),
            "can_ack": rbac.has_permission(m, "sla.acknowledge", t.site_id)})


class BreachListView(SlaBase):
    def get(self, request):
        m, org = request.membership, request.organization
        qs = selectors.filter_breaches(selectors.breaches_for(m, org), request.GET)
        page = _paged(request, qs)
        ack_sites = {b.site_id: rbac.has_permission(m, "sla.acknowledge", b.site_id) for b in page}
        for b in page:
            b.can_ack = ack_sites[b.site_id]
        return render(request, "sla/breaches.html", {
            "page": page, "filters": request.GET,
            "sites": site_selectors.sites_for(m, org, "sla.view").order_by("code")})


class BreachAcknowledgeView(SlaBase):
    required_permission = "sla.view"
    http_method_names = ["post"]

    def post(self, request, pk):
        breach = or404(selectors.get_breach, request.membership, request.organization, pk)
        need(request, "sla.acknowledge", breach.site_id)
        try:
            services.acknowledge_breach(breach, actor=request.user, request=request)
            messages.success(request, "Breach acknowledged.")
        except DomainError as exc:
            _fail(request, exc)
        return redirect(_safe_next(request, "/app/sla/breaches/"))


class ProcessNowView(SlaBase):
    required_permission = "sla.process"
    http_method_names = ["post"]

    def post(self, request):
        res = services.process_organization(request.organization)
        messages.success(request, f"Checked {res['checked']} running SLA timers: {res['warnings']} warning(s), "
                                  f"{res['breaches']} breach(es), {res['escalations']} escalation(s).")
        return redirect(_safe_next(request, "/app/sla/trackings/"))


# --- profiles -----------------------------------------------------------------------------------------------------------


class ProfileListView(SlaBase):
    required_permission = "sla.manage"

    def get(self, request):
        qs = selectors.profiles_for(request.organization).prefetch_related("targets")
        return render(request, "sla/profiles.html", {"page": _paged(request, qs)})


class ProfileCreateView(SlaBase):
    required_permission = "sla.manage"
    crumbs = [("SLA profiles", "/app/sla/profiles/"), ("New", None)]

    def _form(self, request, data=None):
        return ProfileForm(data, sites=Site.objects.for_organization(request.organization).order_by("code"))

    def get(self, request):
        return form_page(request, title="New SLA profile", form=self._form(request), submit="Create profile",
                         cancel_url="/app/sla/profiles/", crumbs=self.crumbs,
                         subtitle="Add targets and escalation rules after creating the profile.")

    def post(self, request):
        form = self._form(request, request.POST)
        if form.is_valid():
            d = dict(form.cleaned_data)
            try:
                profile = services.create_profile(request.organization, actor=request.user, request=request,
                                                  name=d["name"], applies_to=d["applies_to"],
                                                  description=d["description"], site=d.get("site"),
                                                  work_type=d.get("work_type", ""), pause_states=d["pause_states"],
                                                  coverage_only=d.get("coverage_only", False))
                messages.success(request, f"Profile {profile.name} created. Add its targets and rules.")
                return redirect("sla:profile", pk=profile.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return form_page(request, title="New SLA profile", form=form, submit="Create profile",
                         cancel_url="/app/sla/profiles/", crumbs=self.crumbs, status=400)


class ProfileBase(SlaBase):
    required_permission = "sla.manage"

    def profile(self, request, pk):
        return or404(selectors.get_profile, request.organization, pk)


class ProfileDetailView(ProfileBase):
    def get(self, request, pk):
        p = self.profile(request, pk)
        org = request.organization
        return render(request, "sla/profile_detail.html", {
            "p": p, "targets": p.targets.order_by("priority"), "rules": p.rules.select_related("notify_role"),
            "target_form": TargetForm(applies_to=p.applies_to),
            "rule_form": RuleForm(roles=Role.objects.for_organization(org).order_by("name")),
            "max_rules": services.MAX_RULES_PER_PROFILE})


class ProfileEditView(ProfileBase):
    def _page(self, request, p, form, status=200):
        return form_page(request, title=f"Edit {p.name}", form=form, submit="Save", status=status,
                         cancel_url=f"/app/sla/profiles/{p.pk}/",
                         crumbs=[("SLA profiles", "/app/sla/profiles/"), (p.name, f"/app/sla/profiles/{p.pk}/"),
                                 ("Edit", None)])

    def get(self, request, pk):
        p = self.profile(request, pk)
        return self._page(request, p, ProfileForm(editing=True, initial={
            "name": p.name, "description": p.description, "pause_states": p.pause_states}))

    def post(self, request, pk):
        p = self.profile(request, pk)
        form = ProfileForm(request.POST, editing=True)
        if form.is_valid():
            try:
                services.update_profile(p, actor=request.user, request=request, **form.cleaned_data)
                messages.success(request, "Saved.")
                return redirect("sla:profile", pk=p.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self._page(request, p, form, status=400)


class ProfileActiveView(ProfileBase):
    http_method_names = ["post"]

    def post(self, request, pk):
        p = self.profile(request, pk)
        active = request.POST.get("active") == "1"
        try:
            services.set_profile_active(p, active, actor=request.user, request=request)
            messages.success(request, "Profile activated." if active else
                             "Profile deactivated: new requests / work orders will not be tracked by it.")
        except DomainError as exc:
            _fail(request, exc)
        return redirect("sla:profile", pk=p.pk)


class TargetSetView(ProfileBase):
    http_method_names = ["post"]

    def post(self, request, pk):
        p = self.profile(request, pk)
        form = TargetForm(request.POST, applies_to=p.applies_to)
        if form.is_valid():
            try:
                services.set_target(p, actor=request.user, request=request, **form.cleaned_data)
                messages.success(request, "Target saved. Items already being tracked keep their due times.")
            except DomainError as exc:
                _fail(request, exc)
        else:
            messages.error(request, "Check the target values: " + "; ".join(
                e for errs in form.errors.values() for e in errs))
        return redirect("sla:profile", pk=p.pk)


class TargetRemoveView(ProfileBase):
    http_method_names = ["post"]

    def post(self, request, pk, target_pk):
        p = self.profile(request, pk)
        target = SLATarget.objects.for_organization(request.organization).filter(pk=target_pk, profile=p).first()
        if target is None:
            raise Http404
        services.remove_target(target, actor=request.user, request=request)
        messages.success(request, "Target removed.")
        return redirect("sla:profile", pk=p.pk)


class RuleCreateView(ProfileBase):
    http_method_names = ["post"]

    def post(self, request, pk):
        p = self.profile(request, pk)
        form = RuleForm(request.POST, roles=Role.objects.for_organization(request.organization))
        if form.is_valid():
            d = {k: v for k, v in form.cleaned_data.items() if v not in (None, "")}
            try:
                services.create_rule(p, actor=request.user, request=request, **d)
                messages.success(request, "Escalation rule added.")
            except DomainError as exc:
                _fail(request, exc)
        else:
            messages.error(request, "Check the rule values: " + "; ".join(
                e for errs in form.errors.values() for e in errs))
        return redirect("sla:profile", pk=p.pk)


class RuleDeleteView(ProfileBase):
    http_method_names = ["post"]

    def post(self, request, pk, rule_pk):
        p = self.profile(request, pk)
        rule = EscalationRule.objects.for_organization(request.organization).filter(pk=rule_pk, profile=p).first()
        if rule is None:
            raise Http404
        services.delete_rule(rule, actor=request.user, request=request)
        messages.success(request, "Rule removed.")
        return redirect("sla:profile", pk=p.pk)
