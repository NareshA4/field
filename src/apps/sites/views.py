"""M01 HTML views (server-rendered, HTMX-free CRUD). Each view: login -> active membership -> permission gate
(anywhere) -> object lookup restricted to the caller's organization AND site scope (404) -> permission for that
site (403) -> service call (validation, transaction, audit) -> redirect with a message only after success."""
from __future__ import annotations

import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views import View

def htmx_success(trigger_data: dict) -> HttpResponse:
    response = HttpResponse(status=204)
    response["HX-Trigger"] = json.dumps(trigger_data)
    return response

from apps.core.exceptions import DomainError, NotFound
from apps.rbac import services as rbac
from apps.ui.forms import ReasonForm
from apps.ui.mixins import TenantPermissionMixin

from . import selectors, services
from .forms import CalendarForm, ContactForm, HolidayForm, SiteForm, ZoneForm
from .models import CalendarHoliday

TABS = [("overview", "Overview"), ("locations", "Locations"), ("calendars", "Calendars"),
        ("contacts", "Contacts"), ("assets", "Assets")]


def or404(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except NotFound as exc:  # outside the tenant or the caller's site scope: indistinguishable from absent
        raise Http404 from exc


def need(request, code: str, site=None):
    if not rbac.has_permission(request.membership, code, site):
        raise PermissionDenied


def form_page(request, *, title, form, cancel_url, crumbs, submit="Save", subtitle="", status=200):
    return render(request, "ui/form_page.html", {
        "title": title, "form": form, "cancel_url": cancel_url, "crumbs": crumbs, "submit": submit,
        "subtitle": subtitle}, status=status)


class SiteBase(TenantPermissionMixin, View):
    def site(self, request, pk, code=None):
        site = or404(selectors.get_site, request.membership, request.organization, pk)
        if code:
            need(request, code, site)
        return site


# --- sites -------------------------------------------------------------------------------------------------


class SiteListView(SiteBase):
    required_permission = "site.view"

    def get(self, request):
        base_sites = selectors.sites_for(request.membership, request.organization)
        total_sites = base_sites.count()
        active_sites = base_sites.filter(status="ACTIVE").count()
        inactive_sites = base_sites.filter(status="INACTIVE").count()
        qs = selectors.site_list(request.membership, request.organization, request.GET)
        page = Paginator(qs, 20).get_page(request.GET.get("page"))
        return render(request, "sites/list.html", {
            "page": page, "q": request.GET.get("q", ""), "status": request.GET.get("status", ""),
            "can_create": rbac.has_permission(request.membership, "site.create"),
            "total_sites": total_sites,
            "active_sites": active_sites,
            "inactive_sites": inactive_sites})


class SiteCreateView(SiteBase):
    required_permission = "site.create"

    def get(self, request):
        initial = {
            "timezone": request.organization.timezone,
            "country": getattr(request.organization, "country", "IN") or "IN",
        }
        form = SiteForm(initial=initial)
        if request.headers.get("HX-Request"):
            return render(request, "sites/_site_modal.html", {
                "title": "New site", "form": form, "submit": "Create site",
                "action_url": request.path,
            })
        return form_page(request, title="New site", form=form,
                         cancel_url="/app/sites/", crumbs=[("Sites", "/app/sites/"), ("New site", None)],
                         submit="Create site")

    def post(self, request):
        form = SiteForm(request.POST)
        if form.is_valid():
            try:
                site = services.create_site(request.organization, actor=request.user, request=request,
                                            **form.cleaned_data)
            except DomainError as exc:
                if getattr(exc, "code", "") == "duplicate_site_code":
                    form.add_error("code", exc.message)
                else:
                    form.add_error(None, exc.message)
            else:
                messages.success(request, f"Site {site.code} created.")
                if request.headers.get("HX-Request"):
                    return htmx_success({"closeSiteModal": True, "siteSaved": True})
                return redirect("sites:detail", pk=site.pk)
        if request.headers.get("HX-Request"):
            return render(request, "sites/_site_modal.html", {
                "title": "New site", "form": form, "submit": "Create site",
                "action_url": request.path,
            }, status=200)
        return form_page(request, title="New site", form=form, cancel_url="/app/sites/",
                         crumbs=[("Sites", "/app/sites/"), ("New site", None)], submit="Create site", status=400)


class SiteEditView(SiteBase):
    required_permission = "site.update"

    def get(self, request, pk):
        site = self.site(request, pk, "site.update")
        initial = {f: getattr(site, f) for f in services.SITE_FIELDS}
        form = SiteForm(initial=initial)
        if request.headers.get("HX-Request"):
            return render(request, "sites/_site_modal.html", {
                "title": f"Edit {site.code}", "site": site, "form": form, "submit": "Save changes",
                "action_url": request.path,
            })
        return self._page(request, site, form)

    def post(self, request, pk):
        site = self.site(request, pk, "site.update")
        form = SiteForm(request.POST)
        if form.is_valid():
            try:
                services.update_site(site, actor=request.user, request=request, **form.cleaned_data)
            except DomainError as exc:
                if getattr(exc, "code", "") == "duplicate_site_code":
                    form.add_error("code", exc.message)
                else:
                    form.add_error(None, exc.message)
            else:
                messages.success(request, "Site saved.")
                if request.headers.get("HX-Request"):
                    return htmx_success({"closeSiteModal": True, "siteSaved": True})
                return redirect("sites:detail", pk=site.pk)
        if request.headers.get("HX-Request"):
            return render(request, "sites/_site_modal.html", {
                "title": f"Edit {site.code}", "site": site, "form": form, "submit": "Save changes",
                "action_url": request.path,
            }, status=200)
        return self._page(request, site, form, status=400)

    def _page(self, request, site, form, status=200):
        return form_page(request, title=f"Edit {site.code}", form=form, cancel_url=f"/app/sites/{site.pk}/",
                         crumbs=[("Sites", "/app/sites/"), (site.code, f"/app/sites/{site.pk}/"), ("Edit", None)],
                         status=status)


class SiteDetailView(SiteBase):
    required_permission = "site.view"

    def get(self, request, pk, *, error=None):
        site = self.site(request, pk)
        m = request.membership
        tab = request.GET.get("tab", "overview")
        if tab not in dict(TABS) or (tab == "assets" and not selectors.assets_installed()):
            tab = "overview"
        codes = ["site.update", "site.deactivate", "site.contact.manage", "zone.view", "zone.create", "zone.update",
                 "zone.deactivate", "calendar.view", "calendar.create", "calendar.update", "calendar.delete"]
        has_assets = selectors.assets_installed()
        if has_assets:  # asset.* permissions only exist once the Assets module is installed
            codes += ["asset.view", "asset.create"]
        perms = {code.replace(".", "_"): rbac.has_permission(m, code, site) for code in codes}
        perms.setdefault("asset_view", False)
        perms.setdefault("asset_create", False)
        ctx = {"site": site, "tab": tab, "tabs": TABS if has_assets else [t for t in TABS if t[0] != "assets"],
               "perms": perms, "reason_form": ReasonForm(), "asset_total": 0}
        if tab == "locations" and perms["zone_view"]:
            ctx["tree"] = selectors.flatten_tree(selectors.zone_tree(site))
        elif tab == "calendars" and perms["calendar_view"]:
            ctx["calendars"] = site.calendars.prefetch_related("holidays")
        elif tab == "contacts":
            ctx["contacts"] = site.contacts.all()
        elif tab == "assets" and perms["asset_view"] and selectors.assets_installed():
            from apps.assets.selectors import assets_for

            qs = assets_for(m, request.organization).filter(site=site)
            ctx["asset_total"] = qs.count()
            ctx["assets"] = qs.order_by("asset_tag")[:25]
        return render(request, "sites/detail.html", ctx)

    def post(self, request, pk):
        action = request.POST.get("action")
        needed = {"deactivate": "site.deactivate", "reactivate": "site.deactivate"}.get(action)
        if needed is None:
            raise PermissionDenied
        site = self.site(request, pk, needed)
        try:
            if action == "deactivate":
                services.deactivate_site(site, reason=request.POST.get("reason", ""), actor=request.user,
                                         request=request)
                messages.success(request, f"Site {site.code} deactivated.")
            else:
                services.reactivate_site(site, actor=request.user, request=request)
                messages.success(request, f"Site {site.code} reactivated.")
        except DomainError as exc:
            messages.error(request, exc.message)
        return redirect("sites:detail", pk=site.pk)


# --- zones -------------------------------------------------------------------------------------------------


def _site_crumbs(site, *extra):
    return [("Sites", "/app/sites/"), (site.code, f"/app/sites/{site.pk}/?tab=locations"), *extra]


class ZoneCreateView(SiteBase):
    required_permission = "zone.create"

    def _form(self, request, site, data=None):
        initial = {}
        if request.GET.get("parent"):
            initial["parent"] = request.GET["parent"]
        return ZoneForm(data, site=site, initial=initial)

    def get(self, request, pk):
        site = self.site(request, pk, "zone.create")
        form = self._form(request, site)
        parent_zone = None
        if request.GET.get("parent"):
            try:
                parent_zone = site.zones.filter(pk=request.GET["parent"]).first()
            except Exception:
                pass
        if request.headers.get("HX-Request"):
            return render(request, "sites/_zone_modal.html", {
                "title": f"New location in {site.code}",
                "site": site,
                "parent_zone": parent_zone,
                "form": form,
                "submit": "Create location",
                "action_url": request.get_full_path(),
            })
        return form_page(request, title=f"New location in {site.code}", form=form,
                         cancel_url=f"/app/sites/{site.pk}/?tab=locations",
                         crumbs=_site_crumbs(site, ("New location", None)), submit="Create location")

    def post(self, request, pk):
        site = self.site(request, pk, "zone.create")
        form = self._form(request, site, request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                services.create_zone(site, actor=request.user, request=request, parent=d.pop("parent"), **d)
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(request, "Location created.")
                if request.headers.get("HX-Request"):
                    return htmx_success({"closeZoneModal": True, "zoneSaved": True})
                return redirect(f"/app/sites/{site.pk}/?tab=locations")
        if request.headers.get("HX-Request"):
            parent_zone = None
            if request.GET.get("parent"):
                try:
                    parent_zone = site.zones.filter(pk=request.GET["parent"]).first()
                except Exception:
                    pass
            return render(request, "sites/_zone_modal.html", {
                "title": f"New location in {site.code}",
                "site": site,
                "parent_zone": parent_zone,
                "form": form,
                "submit": "Create location",
                "action_url": request.get_full_path(),
            }, status=200)
        return form_page(request, title=f"New location in {site.code}", form=form,
                         cancel_url=f"/app/sites/{site.pk}/?tab=locations",
                         crumbs=_site_crumbs(site, ("New location", None)), submit="Create location", status=400)


class ZoneEditView(SiteBase):
    required_permission = "zone.update"

    def _zone(self, request, pk):
        zone = or404(selectors.get_zone, request.membership, request.organization, pk)
        need(request, "zone.update", zone.site)
        return zone

    def get(self, request, pk):
        zone = self._zone(request, pk)
        form = ZoneForm(site=zone.site, zone=zone, initial={
            "name": zone.name, "zone_type": zone.zone_type, "code": zone.code, "parent": zone.parent_id,
            "description": zone.description})
        if request.headers.get("HX-Request"):
            return render(request, "sites/_zone_modal.html", {
                "title": f"Edit {zone.name}",
                "site": zone.site,
                "zone": zone,
                "form": form,
                "submit": "Save location",
                "action_url": request.path,
            })
        return self._page(request, zone, form)

    def post(self, request, pk):
        zone = self._zone(request, pk)
        form = ZoneForm(request.POST, site=zone.site, zone=zone)
        if form.is_valid():
            d = form.cleaned_data
            try:
                services.update_zone(zone, actor=request.user, request=request, parent=d.pop("parent"), **d)
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(request, "Location saved.")
                if request.headers.get("HX-Request"):
                    return htmx_success({"closeZoneModal": True, "zoneSaved": True})
                return redirect(f"/app/sites/{zone.site_id}/?tab=locations")
        if request.headers.get("HX-Request"):
            return render(request, "sites/_zone_modal.html", {
                "title": f"Edit {zone.name}",
                "site": zone.site,
                "zone": zone,
                "form": form,
                "submit": "Save location",
                "action_url": request.path,
            }, status=200)
        return self._page(request, zone, form, status=400)

    def _page(self, request, zone, form, status=200):
        return form_page(request, title=f"Edit {zone.name}", form=form,
                         cancel_url=f"/app/sites/{zone.site_id}/?tab=locations",
                         crumbs=_site_crumbs(zone.site, (zone.name, None)), status=status)


class ZoneStatusView(SiteBase):
    required_permission = "zone.deactivate"
    http_method_names = ["post"]

    def post(self, request, pk):
        zone = or404(selectors.get_zone, request.membership, request.organization, pk)
        need(request, "zone.deactivate", zone.site)
        try:
            if request.POST.get("action") == "reactivate":
                services.reactivate_zone(zone, actor=request.user, request=request)
                messages.success(request, f"{zone.name} reactivated.")
            else:
                services.deactivate_zone(zone, reason=request.POST.get("reason", ""), actor=request.user,
                                         request=request)
                messages.success(request, f"{zone.name} deactivated.")
        except DomainError as exc:
            messages.error(request, exc.message)
        return redirect(f"/app/sites/{zone.site_id}/?tab=locations")


# --- calendars ---------------------------------------------------------------------------------------------


class CalendarCreateView(SiteBase):
    required_permission = "calendar.create"

    def _page(self, request, site, form, status=200):
        return form_page(request, title=f"New operating calendar for {site.code}", form=form,
                         cancel_url=f"/app/sites/{site.pk}/?tab=calendars",
                         crumbs=_site_crumbs(site, ("New calendar", None)), submit="Create calendar", status=status)

    def get(self, request, pk):
        site = self.site(request, pk, "calendar.create")
        return self._page(request, site, CalendarForm(initial={"working_days": [1, 2, 3, 4, 5]}))

    def post(self, request, pk):
        site = self.site(request, pk, "calendar.create")
        form = CalendarForm(request.POST)
        if form.is_valid():
            try:
                services.create_calendar(site, actor=request.user, request=request, **form.cleaned_data)
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(request, "Calendar created.")
                return redirect(f"/app/sites/{site.pk}/?tab=calendars")
        return self._page(request, site, form, status=400)


class CalendarEditView(SiteBase):
    required_permission = "calendar.update"

    def _cal(self, request, pk):
        cal = or404(selectors.get_calendar, request.membership, request.organization, pk)
        need(request, "calendar.update", cal.site)
        return cal

    def _page(self, request, cal, form, status=200):
        return form_page(request, title=f"Edit calendar {cal.name}", form=form,
                         cancel_url=f"/app/sites/{cal.site_id}/?tab=calendars",
                         crumbs=_site_crumbs(cal.site, (cal.name, None)), status=status)

    def get(self, request, pk):
        cal = self._cal(request, pk)
        return self._page(request, cal, CalendarForm(initial={
            "name": cal.name, "is_24x7": cal.is_24x7, "working_days": cal.working_days, "start_time": cal.start_time,
            "end_time": cal.end_time, "is_default": cal.is_default, "notes": cal.notes}))

    def post(self, request, pk):
        cal = self._cal(request, pk)
        form = CalendarForm(request.POST)
        if form.is_valid():
            try:
                services.update_calendar(cal, actor=request.user, request=request, **form.cleaned_data)
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(request, "Calendar saved.")
                return redirect(f"/app/sites/{cal.site_id}/?tab=calendars")
        return self._page(request, cal, form, status=400)


class CalendarDeleteView(SiteBase):
    required_permission = "calendar.delete"
    http_method_names = ["post"]

    def post(self, request, pk):
        cal = or404(selectors.get_calendar, request.membership, request.organization, pk)
        need(request, "calendar.delete", cal.site)
        try:
            services.delete_calendar(cal, actor=request.user, request=request)
            messages.success(request, "Calendar deleted.")
        except DomainError as exc:
            messages.error(request, exc.message)
        return redirect(f"/app/sites/{cal.site_id}/?tab=calendars")


class HolidayView(SiteBase):
    """POST add (action=add) or remove (action=remove, holiday=<id>) of a calendar holiday."""

    required_permission = "calendar.update"
    http_method_names = ["post"]

    def post(self, request, pk):
        cal = or404(selectors.get_calendar, request.membership, request.organization, pk)
        need(request, "calendar.update", cal.site)
        back = redirect(f"/app/sites/{cal.site_id}/?tab=calendars")
        try:
            if request.POST.get("action") == "remove":
                holiday = or404(selectors.scoped_get, CalendarHoliday.objects.filter(calendar=cal),
                                request.POST.get("holiday"), "Holiday")
                services.remove_holiday(holiday, actor=request.user, request=request)
                messages.success(request, "Holiday removed.")
            else:
                form = HolidayForm(request.POST)
                if not form.is_valid():
                    messages.error(request, "Enter a valid date and a name for the holiday.")
                    return back
                services.add_holiday(cal, date=form.cleaned_data["date"], name=form.cleaned_data["name"],
                                     actor=request.user, request=request)
                messages.success(request, "Holiday added.")
        except DomainError as exc:
            messages.error(request, exc.message)
        return back


# --- contacts ----------------------------------------------------------------------------------------------


class ContactCreateView(SiteBase):
    required_permission = "site.contact.manage"

    def _page(self, request, site, form, status=200):
        return form_page(request, title=f"New contact for {site.code}", form=form,
                         cancel_url=f"/app/sites/{site.pk}/?tab=contacts",
                         crumbs=_site_crumbs(site, ("New contact", None)), submit="Add contact", status=status)

    def get(self, request, pk):
        site = self.site(request, pk, "site.contact.manage")
        return self._page(request, site, ContactForm())

    def post(self, request, pk):
        site = self.site(request, pk, "site.contact.manage")
        form = ContactForm(request.POST)
        if form.is_valid():
            try:
                services.add_contact(site, actor=request.user, request=request, **form.cleaned_data)
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(request, "Contact added.")
                return redirect(f"/app/sites/{site.pk}/?tab=contacts")
        return self._page(request, site, form, status=400)


class ContactEditView(SiteBase):
    required_permission = "site.contact.manage"

    def _contact(self, request, pk):
        c = or404(selectors.scoped_get, selectors.contacts_for(request.membership, request.organization), pk,
                  "Contact")
        need(request, "site.contact.manage", c.site)
        return c

    def _page(self, request, c, form, status=200):
        return form_page(request, title=f"Edit contact {c.name}", form=form,
                         cancel_url=f"/app/sites/{c.site_id}/?tab=contacts",
                         crumbs=_site_crumbs(c.site, (c.name, None)), status=status)

    def get(self, request, pk):
        c = self._contact(request, pk)
        return self._page(request, c, ContactForm(initial={f: getattr(c, f) for f in services.CONTACT_FIELDS}))

    def post(self, request, pk):
        c = self._contact(request, pk)
        if request.POST.get("action") == "delete":
            services.remove_contact(c, actor=request.user, request=request)
            messages.success(request, "Contact removed.")
            return redirect(f"/app/sites/{c.site_id}/?tab=contacts")
        form = ContactForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            if d.get("escalation_order") is None:
                d["escalation_order"] = c.escalation_order
            try:
                services.update_contact(c, actor=request.user, request=request, **d)
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(request, "Contact saved.")
                return redirect(f"/app/sites/{c.site_id}/?tab=contacts")
        return self._page(request, c, form, status=400)

