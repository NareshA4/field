"""SLA & Escalation HTML views. Chain: login -> ACTIVE membership -> permission gate -> organization + site-scoped lookup (404) ->
permission for the tracked item's site (403) -> service (validation, row locks, audit). Every button posts to an
endpoint that calls a service; nothing is simulated in the browser."""
from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views import View

from apps.core.exceptions import DomainError
from apps.rbac import services as rbac
from apps.rbac.models import Role
from apps.rbac.models import MembershipRole
from apps.assets.models import Asset, AssetCategory
from apps.contracts.models import CoverageAgreement
from apps.tenancy.models import Membership
from apps.sites import selectors as site_selectors
from apps.sites.models import Site
from apps.sites.views import need, or404
from apps.ui.mixins import TenantPermissionMixin

from . import selectors, services
from .forms import (EscalationRuleFormSetFactory, PriorityTargetFormSetFactory, ProfileForm, RuleForm,
                    TargetForm)
from .models import EscalationRule, SLATarget


class SlaBase(TenantPermissionMixin, View):
    required_permission = "sla.view"


def _paged(request, qs, size=25, parameter="page"):
    return Paginator(qs, size).get_page(request.GET.get(parameter))


def _fail(request, exc):
    messages.error(request, exc.message if isinstance(exc, DomainError) else str(exc))


def _safe_next(request, default):
    nxt = request.POST.get("next") or ""
    return nxt if nxt.startswith("/app/") and not nxt.startswith("//") else default


def _profile_form_page(request, *, title, form, submit, cancel_url, crumbs, subtitle="", status=200,
                       target_formset=None, rule_formset=None):
    return render(request, "sla/profile_form.html", {
        "title": title, "form": form, "submit": submit, "cancel_url": cancel_url,
        "crumbs": crumbs, "subtitle": subtitle, "target_formset": target_formset,
        "rule_formset": rule_formset,
    }, status=status)


# --- trackings / breaches ----------------------------------------------------------------------------------------------


class TrackingListView(SlaBase):
    def get(self, request):
        m, org = request.membership, request.organization
        qs = selectors.filter_trackings(selectors.trackings_for(m, org), request.GET)
        can_manage = rbac.has_permission(m, "sla.manage")
        return render(request, "sla/trackings.html", {
            "page": _paged(request, qs), "filters": request.GET,
            "sites": site_selectors.sites_for(m, org, "sla.view").order_by("code"),
            "can_process": rbac.has_permission(m, "sla.process"),
            "can_manage": can_manage,
            "summary": selectors.metrics(m, org)})


class TrackingDetailView(SlaBase):
    def get(self, request, pk):
        m, org = request.membership, request.organization
        t = or404(selectors.get_tracking, m, org, pk)
        return render(request, "sla/tracking_detail.html", {
            "t": t, "events": selectors.events_for(org, t), "breaches": t.breaches.all(),
            "pauses": t.pause_events.select_related("paused_by", "approved_by").all(),
            "can_ack": rbac.has_permission(m, "sla.acknowledge", t.site_id)})


class BreachListView(SlaBase):
    def get(self, request):
        m, org = request.membership, request.organization
        base_breaches = selectors.breaches_for(m, org)
        total_breaches = base_breaches.count()
        open_breaches = base_breaches.filter(acknowledged_at__isnull=True).count()
        ack_breaches = total_breaches - open_breaches
        page = _paged(request, selectors.filter_breaches(base_breaches, request.GET))
        for breach in page:
            breach.can_ack = rbac.has_permission(m, "sla.acknowledge", breach.site_id)
        return render(request, "sla/breaches.html", {
            "page": page, "filters": request.GET,
            "sites": site_selectors.sites_for(m, org, "sla.view").order_by("code"),
            "can_manage": rbac.has_permission(m, "sla.manage"),
            "total_breaches": total_breaches,
            "open_breaches": open_breaches,
            "ack_breaches": ack_breaches,
        })


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
        is_ajax = (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in request.headers.get("Accept", "")
            or request.POST.get("format") == "json"
        )
        if is_ajax:
            m, org = request.membership, request.organization
            metrics = selectors.metrics(m, org)
            return JsonResponse({
                "ok": True,
                "result": res,
                "metrics": metrics,
                "message": (
                    f"Checked {res['checked']} running SLA timer(s): "
                    f"{res['warnings']} warning alert(s), "
                    f"{res['breaches']} SLA breach(es), "
                    f"{res['escalations']} escalation(s), "
                    f"{res.get('notifications', 0)} notification(s) handled."
                ),
            })

        messages.success(
            request,
            f"SLA Check completed: {res['checked']} running SLA timer(s) evaluated. "
            f"{res['warnings']} warning alert(s), {res['breaches']} SLA breach(es), "
            f"{res['escalations']} escalation(s), {res.get('notifications', 0)} notification(s) handled."
        )
        next_url = _safe_next(request, request.POST.get("next") or "/app/sla/trackings/")
        return redirect(next_url)


# --- profiles -----------------------------------------------------------------------------------------------------------


class ProfileListView(SlaBase):
    required_permission = "sla.manage"

    def get(self, request):
        base_profiles = selectors.profiles_for(request.organization)
        total_profiles = base_profiles.count()
        active_profiles = base_profiles.filter(is_active=True).count()
        inactive_profiles = total_profiles - active_profiles
        page = _paged(request, base_profiles.prefetch_related("targets"))
        return render(request, "sla/profiles.html", {
            "page": page,
            "can_manage": True,
            "total_profiles": total_profiles,
            "active_profiles": active_profiles,
            "inactive_profiles": inactive_profiles,
        })


class ProfileCreateView(SlaBase):
    required_permission = "sla.manage"
    crumbs = [("SLA profiles", "/app/sla/profiles/"), ("New", None)]

    def _form(self, request, data=None):
        raw_applies = (data.get("applies_to") if data else None) or request.POST.get("applies_to", "REQUEST")
        return ProfileForm(data, applies_to=raw_applies, **_profile_choices(request))

    def _target_formset(self, request, applies_to, data=None, is_draft=False):
        prefix = "targets"
        if data is not None and "targets-TOTAL_FORMS" not in data and "form-TOTAL_FORMS" in data:
            prefix = "form"
        prios = list(services.priorities_for(applies_to))
        prios.reverse()
        initial = [{"priority": priority, "warning_percent": 80} for priority in prios]
        return PriorityTargetFormSetFactory(data, initial=initial, applies_to=applies_to, prefix=prefix,
                                           is_draft=is_draft)

    def _rule_formset(self, request, data=None):
        prefix = "rules"
        roles = Role.objects.for_organization(request.organization).order_by("name")
        return EscalationRuleFormSetFactory(data, roles=roles, prefix=prefix)

    def _render(self, request, form, status=200, target_formset=None, rule_formset=None):
        return _profile_form_page(
            request, title="New SLA Profile", form=form, submit="Create profile",
            cancel_url="/app/sla/profiles/", crumbs=self.crumbs,
            subtitle="Add targets and escalation rules after creating the profile.",
            status=status, target_formset=target_formset, rule_formset=rule_formset)

    def get(self, request):
        form = self._form(request)
        return self._render(request, form)

    def post(self, request):
        post_data = request.POST.copy()
        raw_applies = post_data.get("applies_to", "REQUEST")
        applies_to = "WORK_ORDER" if str(raw_applies).strip().upper() in ("WORK_ORDER", "WORK ORDER") or "WORK" in str(raw_applies).upper() else "REQUEST"
        post_data["applies_to"] = applies_to

        action = post_data.get("action", "").strip().lower()
        if action == "draft":
            is_active = False
            post_data["is_active"] = ""
            is_draft = True
        else:
            is_active = post_data.get("is_active") in ("on", "true", "1", "True") or action in ("activate", "save")
            if is_active:
                post_data["is_active"] = "on"
            is_draft = False

        has_target_inputs = any(k in post_data for k in ("priority", "response_target", "response_minutes", "targets-0-priority", "targets-TOTAL_FORMS"))

        if has_target_inputs and "targets-TOTAL_FORMS" not in post_data and "form-TOTAL_FORMS" not in post_data:
            post_data["targets-TOTAL_FORMS"] = "1"
            post_data["targets-INITIAL_FORMS"] = "0"
            post_data["targets-MIN_NUM_FORMS"] = "0"
            post_data["targets-MAX_NUM_FORMS"] = "4"
            default_p = "CRITICAL" if applies_to == "REQUEST" else "URGENT"
            post_data["targets-0-priority"] = str(post_data.get("priority") or default_p).strip().upper()
            post_data["targets-0-response_minutes"] = post_data.get("response_target") or post_data.get("response_minutes", "")
            post_data["targets-0-resolution_minutes"] = post_data.get("resolution_target") or post_data.get("resolution_minutes", "")
            post_data["targets-0-warning_percent"] = post_data.get("warning_alert") or post_data.get("warning_percent", "80")

        if "rules-TOTAL_FORMS" not in post_data and "form-TOTAL_FORMS" not in post_data:
            post_data["rules-TOTAL_FORMS"] = "0"
            post_data["rules-INITIAL_FORMS"] = "0"
            post_data["rules-MIN_NUM_FORMS"] = "0"
            post_data["rules-MAX_NUM_FORMS"] = "6"

        form = self._form(request, post_data)
        valid = form.is_valid()
        target_formset = None
        rule_formset = None

        if has_target_inputs:
            target_formset = self._target_formset(request, applies_to, post_data, is_draft=is_draft)
            rule_formset = self._rule_formset(request, post_data)
            valid = target_formset.is_valid() and rule_formset.is_valid() and valid

        if valid:
            d = dict(form.cleaned_data)
            d["is_active"] = is_active
            try:
                with transaction.atomic():
                    profile = services.create_profile(
                        request.organization, actor=request.user, request=request,
                        name=d["name"], applies_to=d["applies_to"], description=d["description"],
                        site=d.get("site"), client=d.get("client"), contract=d.get("contract"),
                        asset=d.get("asset"), asset_category=d.get("asset_category"),
                        service_type=d.get("service_type", ""), work_type=d.get("work_type", ""),
                        pause_states=d["pause_states"], coverage_only=d.get("coverage_only", False),
                        is_active=d["is_active"])
                    if target_formset:
                        for target_form in target_formset:
                            target_data = target_form.cleaned_data
                            if not target_data or not target_data.get("priority"):
                                continue
                            services.set_target(profile, actor=request.user, request=request,
                                                **target_data)
                    if rule_formset:
                        for rule_form in rule_formset:
                            rule_data = rule_form.cleaned_data
                            if not rule_data or rule_data.get("DELETE") or not rule_data.get("target_kind"):
                                continue
                            rule_data = {key: value for key, value in rule_data.items()
                                         if key != "DELETE" and value not in (None, "")}
                            rule_data["after_minutes"] = rule_data.get("after_minutes") or 0
                            services.create_rule(profile, actor=request.user, request=request, **rule_data)
                messages.success(request, f"SLA Profile '{profile.name}' was created.")
                return redirect("sla:profile", pk=profile.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self._render(request, form, status=400, target_formset=target_formset, rule_formset=rule_formset)


class ProfileBase(SlaBase):
    required_permission = "sla.manage"

    def profile(self, request, pk):
        return or404(selectors.get_profile, request.organization, pk)


def _profile_choices(request):
    org = request.organization
    clients = Membership.objects.for_organization(org).filter(status=Membership.Status.ACTIVE,
        membership_roles__role__system_key="client_requester").select_related("user").distinct()
    return {"sites": Site.objects.for_organization(org).order_by("code"), "clients": clients,
            "contracts": CoverageAgreement.objects.for_organization(org).filter(is_active=True)
                .select_related("site").order_by("reference"),
            "assets": Asset.objects.for_organization(org).select_related("site").order_by("asset_tag"),
            "categories": AssetCategory.objects.for_organization(org).order_by("name")}


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
        return _profile_form_page(request, title=f"Edit SLA profile: {p.name}", form=form, submit="Save", status=status,
                         cancel_url=f"/app/sla/profiles/{p.pk}/",
                         crumbs=[("SLA profiles", "/app/sla/profiles/"), (p.name, f"/app/sla/profiles/{p.pk}/"),
                                 ("Edit", None)])

    def get(self, request, pk):
        p = self.profile(request, pk)
        return self._page(request, p, ProfileForm(editing=True, applies_to=p.applies_to,
            initial={"name": p.name, "description": p.description, "pause_states": p.pause_states,
                     "applies_to": p.applies_to, "site": p.site_id, "client": p.client_id,
                     "contract": p.contract_id, "asset": p.asset_id, "asset_category": p.asset_category_id,
                     "service_type": p.service_type or p.work_type, "work_type": p.work_type,
                     "coverage_only": p.coverage_only}, **_profile_choices(request)))

    def post(self, request, pk):
        p = self.profile(request, pk)
        form = ProfileForm(request.POST, editing=True, applies_to=request.POST.get("applies_to", p.applies_to),
                           **_profile_choices(request))
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
            messages.success(request, "SLA Profile activated." if active else
                             "SLA Profile deactivated: new Incident / Breakdown or Work Order records will not be tracked by it.")
        except DomainError as exc:
            _fail(request, exc)
        return redirect("sla:profile", pk=p.pk)


class TargetSetView(ProfileBase):
    http_method_names = ["post"]

    def post(self, request, pk):
        p = self.profile(request, pk)
        form = TargetForm(request.POST, applies_to=p.applies_to)
        if form.is_valid() and form.cleaned_data.get("priority"):
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
