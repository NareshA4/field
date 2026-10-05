"""M01 read side. Every function restricts data to the organization AND to the sites the membership may reach
for the given permission (site scope), so list endpoints cannot leak and detail lookups return 404 for
out-of-scope ids (same answer as for absent ids)."""
from __future__ import annotations

import uuid

from django.db.models import Count, IntegerField, Q, Value

from apps.core.exceptions import NotFound, PermissionDenied
from apps.rbac import services as rbac

from .models import CalendarHoliday, OperatingCalendar, Site, SiteContact, Zone


def _uuid(pk, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(pk))
    except ValueError as exc:
        raise NotFound(f"{what} not found.") from exc


def scoped_get(qs, pk, what: str):
    try:
        return qs.get(pk=_uuid(pk, what))
    except qs.model.DoesNotExist as exc:
        raise NotFound(f"{what} not found.") from exc


# --- sites -------------------------------------------------------------------------------------------


def sites_for(membership, org, code: str = "site.view"):
    scope = rbac.site_scope(membership, code)
    return scope.filter(Site.objects.for_organization(org), "pk")


def get_site(membership, org, pk, code: str = "site.view") -> Site:
    return scoped_get(sites_for(membership, org, code), pk, "Site")


def get_site_for(membership, org, pk, code: str) -> Site:
    """Site the caller may use for ``code``. Out of tenant/scope entirely -> 404; visible but not permitted for
    ``code`` -> 403."""
    try:
        return get_site(membership, org, pk, code)
    except NotFound:
        get_site(membership, org, pk)  # raises NotFound (404) when the site is not even visible
        raise PermissionDenied("You do not have permission to perform this action.") from None


def filter_sites(qs, params):
    q = (params.get("q") or "").strip()
    status = (params.get("status") or "").strip()
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(city__icontains=q))
    if status in Site.Status.values:
        qs = qs.filter(status=status)
    return qs


def assets_installed() -> bool:
    """The Assets module (M02) is optional here: asset counts are 0 until it is added to INSTALLED_APPS."""
    from django.apps import apps

    return apps.is_installed("apps.assets")


def site_list(membership, org, params):
    qs = filter_sites(sites_for(membership, org), params).annotate(zone_count=Count("zones", distinct=True))
    if assets_installed():
        from apps.assets.workflow import TERMINAL_STATES

        qs = qs.annotate(asset_count=Count("assets", filter=~Q(assets__status__in=TERMINAL_STATES), distinct=True))
    else:
        qs = qs.annotate(asset_count=Value(0, output_field=IntegerField()))
    return qs.order_by("code")


# --- zones -------------------------------------------------------------------------------------------


def zones_for(membership, org, code: str = "zone.view"):
    scope = rbac.site_scope(membership, code)
    return scope.filter(Zone.objects.for_organization(org).select_related("site", "parent"), "site_id")


def get_zone(membership, org, pk, code: str = "zone.view") -> Zone:
    return scoped_get(zones_for(membership, org, code), pk, "Location")


def filter_zones(qs, params):
    site = (params.get("site") or "").strip()
    parent = (params.get("parent") or "").strip()
    q = (params.get("q") or "").strip()
    ztype = (params.get("zone_type") or "").strip()
    status = (params.get("status") or "").strip()
    if site:
        try:
            qs = qs.filter(site_id=uuid.UUID(site))
        except ValueError:
            return qs.none()
    if parent == "root":
        qs = qs.filter(parent__isnull=True)
    elif parent:
        try:
            qs = qs.filter(parent_id=uuid.UUID(parent))
        except ValueError:
            return qs.none()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
    if ztype in Zone.ZoneType.values:
        qs = qs.filter(zone_type=ztype)
    if status in Zone.Status.values:
        qs = qs.filter(status=status)
    return qs


def zone_tree(site: Site) -> list[dict]:
    """Nested location tree of one site from a single query (+ active asset counts). Nodes:
    {"zone": Zone, "children": [...], "asset_count": int, "depth": int}."""
    qs = Zone.objects.filter(site=site)
    if assets_installed():
        from apps.assets.workflow import TERMINAL_STATES

        qs = qs.annotate(asset_count=Count("assets", filter=~Q(assets__status__in=TERMINAL_STATES), distinct=True))
    else:
        qs = qs.annotate(asset_count=Value(0, output_field=IntegerField()))
    zones = list(qs.order_by("zone_type", "name"))
    nodes = {z.pk: {"zone": z, "children": [], "asset_count": z.asset_count, "depth": 0} for z in zones}
    roots = []
    for z in zones:
        node = nodes[z.pk]
        parent = nodes.get(z.parent_id)
        (parent["children"] if parent else roots).append(node)

    def set_depth(items, depth):
        for n in items:
            n["depth"] = depth
            set_depth(n["children"], depth + 1)

    set_depth(roots, 0)
    return roots


def flatten_tree(roots: list[dict]) -> list[dict]:
    out = []
    for n in roots:
        out.append(n)
        out.extend(flatten_tree(n["children"]))
    return out


# --- calendars / contacts ------------------------------------------------------------------------------


def calendars_for(membership, org, code: str = "calendar.view"):
    scope = rbac.site_scope(membership, code)
    return scope.filter(OperatingCalendar.objects.for_organization(org).select_related("site")
                        .prefetch_related("holidays"), "site_id")


def get_calendar(membership, org, pk, code: str = "calendar.view") -> OperatingCalendar:
    return scoped_get(calendars_for(membership, org, code), pk, "Calendar")


def holidays_for(membership, org):
    scope = rbac.site_scope(membership, "calendar.view")
    return scope.filter(CalendarHoliday.objects.for_organization(org).select_related("calendar__site"),
                        "calendar__site_id")


def contacts_for(membership, org):
    scope = rbac.site_scope(membership, "site.view")
    return scope.filter(SiteContact.objects.for_organization(org).select_related("site"), "site_id")
