"""M02 service layer (asset registry, status lifecycle, location moves, documents, meters).

Callers (API/UI) resolve referenced objects through organization- and site-scoped selectors and check
permissions (with the target site) first; services enforce integrity: tenant ownership of every reference,
active site/zone/category, lifecycle rules, append-only history and an audit record per change, all in one
transaction. Other modules change asset status ONLY through ``change_status`` (never by setting the field)."""
from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import Conflict, ValidationFailed
from apps.files import services as files

from . import attributes as attrs
from .models import (
    Asset,
    AssetCategory,
    AssetDocument,
    AssetLocationHistory,
    AssetMeter,
    AssetMeterReading,
    AssetStatusHistory,
)
from .workflow import ACTIVE, ASSET_STATUS, TERMINAL_STATES, UNDER_MAINTENANCE

ASSET_FIELDS = ["asset_tag", "name", "description", "category_id", "manufacturer", "model", "serial_number",
                "purchase_date", "commission_date", "site_id", "zone_id", "owner_id", "warranty_ref", "attributes"]
CATEGORY_FIELDS = ["name", "description", "is_active", "attribute_definitions"]
_EDITABLE = {"asset_tag", "name", "description", "manufacturer", "model", "serial_number", "purchase_date",
             "commission_date", "warranty_ref"}
_TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,39}")
UNSET = ...


def _save(obj, message: str, code: str):
    try:
        with transaction.atomic():
            obj.save()
    except IntegrityError as exc:
        raise Conflict(message, code=code) from exc


def assert_editable(asset: Asset):
    if asset.status in TERMINAL_STATES:
        raise Conflict(f"The asset is {asset.status.lower()} and can no longer be changed.", code="asset_terminal")


def _check_refs(org, *, site=None, zone=None, category=None, owner=None):
    if site is not None:
        if site.organization_id != org.pk:
            raise ValidationFailed("Site belongs to a different organization.", code="cross_tenant_site")
        if site.status != "ACTIVE":
            raise Conflict("The site is inactive.", code="site_inactive")
    if zone is not None:
        if zone.organization_id != org.pk:
            raise ValidationFailed("Location belongs to a different organization.", code="cross_tenant_zone")
        if site is not None and zone.site_id != site.pk:
            raise ValidationFailed("The location does not belong to the selected site.", code="zone_site_mismatch")
        if zone.status != "ACTIVE":
            raise Conflict("The location is inactive.", code="zone_inactive")
    if category is not None:
        if category.organization_id != org.pk:
            raise ValidationFailed("Category belongs to a different organization.", code="cross_tenant_category")
        if not category.is_active:
            raise Conflict("The asset category is inactive.", code="category_inactive")
    if owner is not None:
        if owner.organization_id != org.pk:
            raise ValidationFailed("Owner belongs to a different organization.", code="cross_tenant_owner")
        if not owner.is_active:
            raise ValidationFailed("The owner must be an active member.", code="owner_inactive")


def _clean_text(data: dict) -> dict:
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items() if k in _EDITABLE}


def _tag(value: str) -> str:
    tag = (value or "").strip()
    if not _TAG_RE.fullmatch(tag):
        raise ValidationFailed("Asset tag must be 1-40 characters: letters, digits, '.', '_', '/' or '-'.",
                               code="invalid_asset_tag")
    return tag


def _check_dates(purchase, commission):
    if purchase and commission and commission < purchase:
        raise ValidationFailed("Commissioning date cannot be before the purchase date.", code="invalid_dates")


def _check_serial(org, asset: Asset | None, manufacturer: str, serial: str):
    if not serial:
        return
    dup = Asset.objects.for_organization(org).filter(manufacturer__iexact=manufacturer, serial_number__iexact=serial)
    if asset is not None:
        dup = dup.exclude(pk=asset.pk)
    if dup.exists():
        raise Conflict("An asset with this manufacturer and serial number already exists.",
                       code="duplicate_serial_number")


def _location_row(asset, actor, *, from_site, from_zone, reason=""):
    AssetLocationHistory(
        organization=asset.organization, asset=asset, from_site=from_site, from_zone=from_zone,
        to_site=asset.site, to_zone=asset.zone, reason=reason[:500], moved_by=actor).save()


# --- assets ------------------------------------------------------------------------------------------


@transaction.atomic
def create_asset(org, *, site, category, zone=None, owner=None, actor, request=None, **data) -> Asset:
    _check_refs(org, site=site, zone=zone, category=category, owner=owner)
    values = data.pop("attributes", None)
    data = _clean_text(data)
    data["attributes"] = attrs.clean_values(category.attribute_definitions, values)
    data["asset_tag"] = _tag(data.get("asset_tag", ""))
    if not data.get("name"):
        raise ValidationFailed("Asset name is required.")
    _check_dates(data.get("purchase_date"), data.get("commission_date"))
    _check_serial(org, None, data.get("manufacturer", ""), data.get("serial_number", ""))
    if Asset.objects.for_organization(org).filter(asset_tag__iexact=data["asset_tag"]).exists():
        raise Conflict("An asset with this tag already exists.", code="duplicate_asset_tag")
    asset = Asset(organization=org, site=site, zone=zone, category=category, owner=owner, status=ACTIVE, **data)
    _save(asset, "An asset with this tag or serial number already exists.", "duplicate_asset")
    AssetStatusHistory(organization=org, asset=asset, from_status="", to_status=ACTIVE, action="register",
                       reason="Asset registered", changed_by=actor).save()
    _location_row(asset, actor, from_site=None, from_zone=None, reason="Asset registered")
    audit.record("asset.created", actor=actor, organization=org, target=asset,
                 after=audit.snapshot(asset, ASSET_FIELDS + ["status"]), request=request)
    return asset


@transaction.atomic
def update_asset(asset: Asset, *, actor, request=None, category=UNSET, owner=UNSET, site=UNSET, zone=UNSET,
                 attributes=UNSET, reason: str = "", **changes) -> Asset:
    """Edits descriptive fields. ``site``/``zone`` (when given and different) perform a tracked move; ``status``
    can never be edited here (use ``change_status``)."""
    org = asset.organization
    asset = Asset.objects.select_for_update().get(pk=asset.pk)
    assert_editable(asset)
    changes = _clean_text(changes)
    before = audit.snapshot(asset, ASSET_FIELDS + ["status"])
    if "asset_tag" in changes:
        changes["asset_tag"] = _tag(changes["asset_tag"])
        dup = Asset.objects.for_organization(org).filter(asset_tag__iexact=changes["asset_tag"]).exclude(pk=asset.pk)
        if dup.exists():
            raise Conflict("An asset with this tag already exists.", code="duplicate_asset_tag")
    if "name" in changes and not changes["name"]:
        raise ValidationFailed("Asset name is required.")
    if category is not UNSET:
        _check_refs(org, category=category)
        if category is None:
            raise ValidationFailed("Category is required.")
        if category.pk != asset.category_id:
            asset.category = category
            if attributes is UNSET:
                attributes = {}  # values of the old category's attributes do not carry over
    if attributes is not UNSET:
        asset.attributes = attrs.clean_values(asset.category.attribute_definitions, attributes)
    if owner is not UNSET:
        _check_refs(org, owner=owner)
        asset.owner = owner
    for k, v in changes.items():
        setattr(asset, k, v)
    _check_dates(asset.purchase_date, asset.commission_date)
    _check_serial(org, asset, asset.manufacturer, asset.serial_number)
    new_site = asset.site if site is UNSET else site
    new_zone = asset.zone if zone is UNSET else zone
    if site is not UNSET and zone is UNSET and new_site.pk != asset.site_id:
        new_zone = None  # the old location does not exist at the new site
    moved = (new_site.pk, getattr(new_zone, "pk", None)) != (asset.site_id, asset.zone_id)
    if moved:
        _move(asset, new_site, new_zone, actor=actor, reason=reason)
    _save(asset, "An asset with this tag or serial number already exists.", "duplicate_asset")
    after = audit.snapshot(asset, ASSET_FIELDS + ["status"])
    if before != after:
        audit.record("asset.updated", actor=actor, organization=org, target=asset, before=before, after=after,
                     request=request)
    if moved:
        audit.record("asset.location_changed", actor=actor, organization=org, target=asset,
                     before={"site_id": before["site_id"], "zone_id": before["zone_id"]},
                     after={"site_id": after["site_id"], "zone_id": after["zone_id"]},
                     metadata={"reason": reason}, request=request)
    return asset


def _move(asset: Asset, site, zone, *, actor, reason=""):
    _check_refs(asset.organization, site=site, zone=zone)
    if site.pk != asset.site_id and (
        hasattr(asset, "parent_link") or asset.child_links.exists()
    ):
        raise Conflict("Detach the asset from its hierarchy before moving it to another site.",
                       code="asset_in_hierarchy")
    from_site, from_zone = asset.site, asset.zone
    asset.site, asset.zone = site, zone
    asset.save()
    _location_row(asset, actor, from_site=from_site, from_zone=from_zone, reason=reason)


@transaction.atomic
def change_status(asset: Asset, *, action: str, reason: str, actor, request=None, source: str = "manual") -> Asset:
    """The ONLY path that changes ``Asset.status``: validated transition + history row + audit, atomically."""
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValidationFailed("A reason is required for a status change.", code="reason_required")
    asset = Asset.objects.select_for_update().get(pk=asset.pk)
    previous, new = ASSET_STATUS.apply(asset, action)
    asset.save(update_fields=["status", "updated_at"])
    AssetStatusHistory(organization=asset.organization, asset=asset, from_status=previous, to_status=new,
                       action=action, reason=reason[:500], changed_by=actor, source=source).save()
    audit.record("asset.status_changed", actor=actor, organization=asset.organization, target=asset,
                 before={"status": previous}, after={"status": new},
                 metadata={"action": action, "reason": reason, "source": source}, request=request)
    return asset


# --- work-order driven status (M06 -> M02, D-058) ---------------------------------------------------------------------

# Work types that take the asset out of normal service while they run. An INSPECTION does not.
MAINTENANCE_WORK_TYPES = ("CORRECTIVE", "PREVENTIVE", "INSTALLATION", "OTHER")


def _set_by_work_order(asset: Asset) -> bool:
    """True when the asset's current UNDER_MAINTENANCE was set by a work order (never undo a manual decision)."""
    last = asset.status_history.order_by("-created_at", "-id").first()
    return bool(last and last.to_status == UNDER_MAINTENANCE and last.source == "work_order")


@transaction.atomic
def on_work_order_started(wo, *, actor, request=None):
    """HPE 8.4 controlled transition: work starts -> ACTIVE asset becomes UNDER_MAINTENANCE through the asset state
    machine (history + audit). Idempotent; any other asset state (already under maintenance, out of service,
    retired, disposed) is left alone."""
    if wo.work_type not in MAINTENANCE_WORK_TYPES:
        return None
    asset = Asset.objects.select_for_update().get(pk=wo.asset_id)
    if asset.status != ACTIVE:
        return None
    return change_status(asset, action="start_maintenance", reason=f"{wo.number} work started", actor=actor,
                         request=request, source="work_order")


@transaction.atomic
def on_work_order_closed(wo, *, actor, request=None):
    """Closure -> UNDER_MAINTENANCE asset returns to ACTIVE, but only when this work order's lifecycle put it there
    and no other work order on the asset is still being executed. OUT_OF_SERVICE (a real fault state) and terminal
    states are never touched."""
    from apps.workorders.models import WorkOrder
    from apps.workorders.workflow import EXECUTION_STATES

    asset = Asset.objects.select_for_update().get(pk=wo.asset_id)
    if asset.status != UNDER_MAINTENANCE or not _set_by_work_order(asset):
        return None
    busy = WorkOrder.objects.for_organization(wo.organization).filter(
        asset=asset, status__in=EXECUTION_STATES).exclude(pk=wo.pk).exists()
    if busy:
        return None
    return change_status(asset, action="complete_maintenance", reason=f"{wo.number} closed", actor=actor,
                         request=request, source="work_order")


@transaction.atomic
def remove_document(document: AssetDocument, *, reason: str, actor, request=None) -> AssetDocument:
    """Soft-removes a document (hidden, download blocked; file and audit trail kept). Needs a reason."""
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValidationFailed("A reason is required to remove a document.", code="reason_required")
    document = AssetDocument.objects.select_for_update().select_related("asset").get(pk=document.pk)
    if not document.is_active:
        raise Conflict("The document has already been removed.", code="document_removed")
    assert_editable(document.asset)
    document.is_active, document.removed_at, document.removed_by = False, timezone.now(), actor
    document.removed_reason = reason[:300]
    document.save(update_fields=["is_active", "removed_at", "removed_by", "removed_reason", "updated_at"])
    audit.record("asset.document_removed", actor=actor, organization=document.organization, target=document.asset,
                 before={"title": document.title, "active": True}, after={"active": False, "reason": reason[:300]},
                 metadata={"document": str(document.pk)}, request=request)
    return document


# --- categories --------------------------------------------------------------------------------------


@transaction.atomic
def create_category(org, *, name: str, description: str = "", attribute_definitions=None, actor,
                    request=None) -> AssetCategory:
    name = (name or "").strip()
    if not name:
        raise ValidationFailed("Category name is required.")
    if AssetCategory.objects.for_organization(org).filter(name__iexact=name).exists():
        raise Conflict("A category with this name already exists.", code="duplicate_category")
    cat = AssetCategory(organization=org, name=name, description=(description or "").strip(),
                        attribute_definitions=attrs.normalize_definitions(attribute_definitions))
    _save(cat, "A category with this name already exists.", "duplicate_category")
    audit.record("asset.category_created", actor=actor, organization=org, target=cat,
                 after=audit.snapshot(cat, CATEGORY_FIELDS), request=request)
    return cat


@transaction.atomic
def update_category(cat: AssetCategory, *, actor, request=None, name=None, description=None, is_active=None,
                    attribute_definitions=UNSET):
    before = audit.snapshot(cat, CATEGORY_FIELDS)
    if name is not None:
        name = name.strip()
        if not name:
            raise ValidationFailed("Category name is required.")
        if AssetCategory.objects.for_organization(cat.organization).filter(name__iexact=name).exclude(
                pk=cat.pk).exists():
            raise Conflict("A category with this name already exists.", code="duplicate_category")
        cat.name = name
    if description is not None:
        cat.description = description.strip()
    if is_active is not None:
        cat.is_active = is_active
    if attribute_definitions is not UNSET:
        cat.attribute_definitions = attrs.normalize_definitions(attribute_definitions)
    _save(cat, "A category with this name already exists.", "duplicate_category")
    after = audit.snapshot(cat, CATEGORY_FIELDS)
    if before != after:
        audit.record("asset.category_updated", actor=actor, organization=cat.organization, target=cat,
                     before=before, after=after, request=request)
    return cat


# --- documents ---------------------------------------------------------------------------------------


@transaction.atomic
def add_document(asset: Asset, uploaded, *, title: str = "", doc_type: str = "OTHER", actor, request=None):
    """Stores the file through the Phase 0 secure path (validation, tenant folder, hash, audit). Download is
    authorised by ``asset.view`` for the asset's site (see ``assets.access``)."""
    if doc_type not in AssetDocument.DocType.values:
        raise ValidationFailed("Unknown document type.", code="invalid_doc_type")
    title = (title or "").strip() or getattr(uploaded, "name", "Document")
    doc = AssetDocument(organization=asset.organization, asset=asset, title=title[:150], doc_type=doc_type,
                        uploaded_by=actor)
    doc.save()
    doc.attachment = files.attach(uploaded, target=doc, organization=asset.organization, user=actor,
                                  description=title, read_permission="asset.view", request=request)
    doc.save(update_fields=["attachment", "updated_at"])
    audit.record("asset.document_added", actor=actor, organization=asset.organization, target=asset,
                 after={"document_id": doc.pk, "title": doc.title, "doc_type": doc_type,
                        "file": doc.attachment.original_name}, request=request)
    return doc


# --- meters ------------------------------------------------------------------------------------------


@transaction.atomic
def create_meter(asset: Asset, *, name: str, unit: str, actor, request=None) -> AssetMeter:
    asset = Asset.objects.select_for_update().get(pk=asset.pk)  # never trust a stale status
    assert_editable(asset)
    name, unit = (name or "").strip(), (unit or "").strip()
    if not name or not unit:
        raise ValidationFailed("Meter name and unit are required.")
    if asset.meters.filter(name__iexact=name).exists():
        raise Conflict("This asset already has a meter with that name.", code="duplicate_meter")
    meter = AssetMeter(organization=asset.organization, asset=asset, name=name, unit=unit)
    _save(meter, "This asset already has a meter with that name.", "duplicate_meter")
    audit.record("asset.meter_created", actor=actor, organization=asset.organization, target=asset,
                 after={"meter_id": meter.pk, "name": name, "unit": unit}, request=request)
    return meter


@transaction.atomic
def set_meter_active(meter: AssetMeter, *, active: bool, actor, request=None):
    if meter.is_active == active:
        raise Conflict("Meter is already in that state.", code="no_change")
    meter.is_active = active
    meter.save(update_fields=["is_active", "updated_at"])
    audit.record("asset.meter_activated" if active else "asset.meter_deactivated", actor=actor,
                 organization=meter.organization, target=meter.asset, after={"meter_id": meter.pk, "name": meter.name},
                 request=request)


@transaction.atomic
def record_reading(meter: AssetMeter, *, value, read_at=None, notes: str = "", actor, request=None,
                   source: str = "manual") -> AssetMeterReading:
    """Readings are monotonic: value and time may not go backwards (M04 consumes this for meter-based PM)."""
    meter = AssetMeter.objects.select_for_update().select_related("asset").get(pk=meter.pk)
    assert_editable(meter.asset)
    if not meter.is_active:
        raise Conflict("The meter is inactive.", code="meter_inactive")
    try:
        value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationFailed("Reading must be a number.", code="invalid_reading") from exc
    if not value.is_finite() or value < 0 or value >= Decimal("1e13"):
        raise ValidationFailed("Reading must be a non-negative number.", code="invalid_reading")
    now = timezone.now()
    read_at = read_at or now
    if timezone.is_naive(read_at):
        read_at = timezone.make_aware(read_at)
    if read_at > now + timedelta(minutes=5):
        raise ValidationFailed("Reading time cannot be in the future.", code="reading_in_future")
    last = meter.readings.order_by("-read_at", "-created_at").first()
    if last is not None:
        if value < last.value:
            raise ValidationFailed(
                f"Reading {value} is lower than the previous reading {last.value} {meter.unit}.",
                code="meter_not_monotonic", details={"previous": str(last.value)})
        if read_at < last.read_at:
            raise ValidationFailed("Reading time is earlier than the previous reading.",
                                   code="meter_time_not_monotonic")
    reading = AssetMeterReading(organization=meter.organization, meter=meter, value=value, read_at=read_at,
                                recorded_by=actor, source=source, notes=(notes or "").strip()[:300])
    reading.save()
    audit.record("asset.meter_reading_recorded", actor=actor, organization=meter.organization, target=meter.asset,
                 after={"meter": meter.name, "value": value, "unit": meter.unit, "read_at": read_at},
                 request=request)
    return reading
