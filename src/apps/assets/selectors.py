"""M02/M03 read side: organization + site-scope restricted querysets (see ``sites.selectors``)."""
from __future__ import annotations

import uuid

from django.db.models import OuterRef, Q, Subquery

from apps.core.exceptions import NotFound
from apps.rbac import services as rbac
from apps.sites.selectors import scoped_get

from .models import (
    Asset,
    AssetCategory,
    AssetComponent,
    AssetDocument,
    AssetLocationHistory,
    AssetMeter,
    AssetMeterReading,
    AssetStatusHistory,
)

ORDERINGS = {"asset_tag": "asset_tag", "-asset_tag": "-asset_tag", "name": "name", "-name": "-name",
             "status": "status", "-created_at": "-created_at", "created_at": "created_at"}


def assets_for(membership, org, code: str = "asset.view"):
    scope = rbac.site_scope(membership, code)
    qs = Asset.objects.for_organization(org).select_related("site", "zone", "category", "owner__user")
    return scope.filter(qs, "site_id")


def get_asset(membership, org, pk, code: str = "asset.view") -> Asset:
    return scoped_get(assets_for(membership, org, code), pk, "Asset")


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def filter_assets(qs, params):
    for param, field in (("site", "site_id"), ("zone", "zone_id"), ("category", "category_id"),
                         ("owner", "owner_id")):
        value = (params.get(param) or "").strip()
        if value:
            parsed = _uuid_or_none(value)
            qs = qs.filter(**{field: parsed}) if parsed else qs.none()
    status = (params.get("status") or "").strip()
    if status:
        qs = qs.filter(status=status) if status in Asset.Status.values else qs.none()
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(asset_tag__icontains=q) | Q(name__icontains=q) | Q(serial_number__icontains=q)
                       | Q(model__icontains=q) | Q(manufacturer__icontains=q))
    return qs.order_by(ORDERINGS.get(params.get("ordering") or "", "asset_tag"))


def categories_for(org):
    return AssetCategory.objects.for_organization(org)


def status_history_for(membership, org, asset: Asset):
    return AssetStatusHistory.objects.for_organization(org).filter(asset=asset).select_related("changed_by")


def location_history_for(org, asset: Asset):
    return AssetLocationHistory.objects.for_organization(org).filter(asset=asset).select_related(
        "from_site", "from_zone", "to_site", "to_zone", "moved_by")


def documents_for(org, asset: Asset, *, include_removed: bool = False):
    qs = AssetDocument.objects.for_organization(org).filter(asset=asset).select_related("attachment", "uploaded_by")
    return qs if include_removed else qs.filter(is_active=True)


def get_document(membership, org, pk, code: str = "asset.document.manage") -> AssetDocument:
    """A document of an asset the caller may act on (404 for other tenants / sites)."""
    doc = AssetDocument.objects.for_organization(org).filter(pk=_uuid_or_none(pk)).select_related(
        "asset", "attachment").first()
    if doc is None or not assets_for(membership, org, code).filter(pk=doc.asset_id).exists():
        raise NotFound("Document not found.")
    return doc


def meters_for(membership, org, code: str = "asset.view"):
    scope = rbac.site_scope(membership, code)
    last = AssetMeterReading.objects.filter(meter=OuterRef("pk")).order_by("-read_at", "-created_at")
    qs = AssetMeter.objects.for_organization(org).select_related("asset").annotate(
        last_value=Subquery(last.values("value")[:1]), last_read_at=Subquery(last.values("read_at")[:1]))
    return scope.filter(qs, "asset__site_id")


def get_meter(membership, org, pk, code: str = "asset.view") -> AssetMeter:
    return scoped_get(meters_for(membership, org, code), pk, "Meter")


def readings_for(org, meter: AssetMeter):
    return AssetMeterReading.objects.for_organization(org).filter(meter=meter).select_related("recorded_by")


def components_for(membership, org, code: str = "asset.view"):
    scope = rbac.site_scope(membership, code)
    qs = AssetComponent.objects.for_organization(org).select_related(
        "parent", "child", "parent__site", "child__category")
    return scope.filter(qs, "parent__site_id")


def get_component(membership, org, pk, code: str = "asset.view") -> AssetComponent:
    return scoped_get(components_for(membership, org, code), pk, "Component relationship")


def asset_choices(membership, org, code: str, *, exclude=None, site=None):
    """Assets selectable in forms (always scope + organization restricted)."""
    qs = assets_for(membership, org, code).exclude(status__in=("RETIRED", "DISPOSED"))
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    if site is not None:
        qs = qs.filter(site=site)
    return qs.order_by("asset_tag")


def _names(org, ids_by_field: dict) -> dict:
    """{field: {id: readable label}} for the foreign keys that appear in an asset change log."""
    from apps.sites.models import Site, Zone
    from apps.tenancy.models import Membership

    out: dict = {}
    if ids_by_field.get("site_id"):
        out["site_id"] = {str(x.pk): x.code for x in Site.objects.for_organization(org).filter(
            pk__in=ids_by_field["site_id"])}
    if ids_by_field.get("zone_id"):
        out["zone_id"] = {str(x.pk): x.name for x in Zone.objects.for_organization(org).filter(
            pk__in=ids_by_field["zone_id"])}
    if ids_by_field.get("category_id"):
        out["category_id"] = {str(x.pk): x.name for x in AssetCategory.objects.for_organization(org).filter(
            pk__in=ids_by_field["category_id"])}
    if ids_by_field.get("owner_id"):
        out["owner_id"] = {str(x.pk): x.user.display_name for x in Membership.objects.for_organization(org)
                           .filter(pk__in=ids_by_field["owner_id"]).select_related("user")}
    return out


def change_log(org, asset: Asset) -> list[dict]:
    """Field-level change history of one asset, from the append-only audit trail (who / when / before -> after).
    Includes status, location, hierarchy, document and meter events recorded against the asset. Foreign keys
    are shown by name (site code, location, category, owner)."""
    import uuid

    from apps.audit.models import AuditLog

    rows = list(AuditLog.objects.for_organization(org).filter(
        target_type="assets.asset", target_id=str(asset.pk)).select_related("actor").order_by("-occurred_at")[:200])
    wanted: dict = {}
    for r in rows:
        for snap in (r.before or {}, r.after or {}):
            for key in ("site_id", "zone_id", "category_id", "owner_id"):
                try:
                    if snap.get(key):
                        wanted.setdefault(key, set()).add(uuid.UUID(str(snap[key])))
                except ValueError:
                    continue
    names = _names(org, wanted)

    def label(key, value):
        return names.get(key, {}).get(str(value), value) if value else value

    out = []
    for r in rows:
        changes = []
        before, after = r.before or {}, r.after or {}
        for key in sorted(set(before) | set(after)):
            if before.get(key) != after.get(key):
                changes.append({"field": key.removesuffix("_id"), "old": label(key, before.get(key)),
                                "new": label(key, after.get(key))})
        out.append({"id": str(r.pk), "action": r.action, "actor": r.actor_email or "system",
                    "occurred_at": r.occurred_at, "changes": changes, "metadata": r.metadata})
    return out
