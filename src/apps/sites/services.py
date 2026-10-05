"""M01 service layer: every state change validates, runs in a transaction and writes an audit record.
Permission checks (including site scope) are done by the API/UI layer before calling in here; services
enforce data integrity (tenant ownership, hierarchy, lifecycle rules)."""
from __future__ import annotations

import re
import zoneinfo
from datetime import time

from django.db import IntegrityError, transaction

from apps.audit import services as audit
from apps.core.exceptions import Conflict, ValidationFailed

from .models import MAX_ESCALATION_ORDER, CalendarHoliday, OperatingCalendar, Site, SiteContact, Zone
from .workflow import ACTIVATION, INACTIVE

SITE_FIELDS = ["code", "name", "description", "address", "city", "state_region", "postal_code", "country",
               "timezone", "contact_name", "contact_email", "contact_phone"]
ZONE_FIELDS = ["name", "code", "zone_type", "description", "parent_id"]
CONTACT_FIELDS = ["name", "role_title", "phone", "email", "escalation_order", "notes"]
CALENDAR_FIELDS = ["name", "working_days", "is_24x7", "start_time", "end_time", "is_default", "notes"]
MAX_ZONE_DEPTH = 6
_CODE_RE = re.compile(r"[A-Z0-9][A-Z0-9_.-]{0,29}")


def _code(value: str, *, what: str, required: bool = True) -> str:
    code = (value or "").strip().upper()
    if not code and not required:
        return ""
    if not _CODE_RE.fullmatch(code):
        raise ValidationFailed(
            f"{what} must be 1-30 characters: letters, digits, '.', '_' or '-'.", details={"code": value})
    return code


def _timezone(name: str) -> str:
    try:
        zoneinfo.ZoneInfo(name)
    except Exception as exc:
        raise ValidationFailed(f"Unknown timezone '{name}'.") from exc
    return name


def _clean(data: dict, fields: list[str]) -> dict:
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items() if k in fields}


def _save(obj, message: str, code: str):
    try:
        with transaction.atomic():
            obj.save()
    except IntegrityError as exc:
        raise Conflict(message, code=code) from exc


# --- sites -----------------------------------------------------------------------------------------


@transaction.atomic
def create_site(org, *, actor, request=None, **data) -> Site:
    data = _clean(data, SITE_FIELDS)
    data["code"] = _code(data.get("code", ""), what="Site code")
    if not data.get("name"):
        raise ValidationFailed("Site name is required.")
    data["timezone"] = _timezone(data.get("timezone") or org.timezone)
    data["country"] = (data.get("country") or "").upper()[:2]
    site = Site(organization=org, **data)
    _save(site, "A site with this code already exists.", "duplicate_site_code")
    audit.record("site.created", actor=actor, organization=org, target=site, after=audit.snapshot(site, SITE_FIELDS),
                 request=request)
    return site


@transaction.atomic
def update_site(site: Site, *, actor, request=None, **changes) -> Site:
    changes = _clean(changes, SITE_FIELDS)
    if "code" in changes:
        changes["code"] = _code(changes["code"], what="Site code")
    if "name" in changes and not changes["name"]:
        raise ValidationFailed("Site name is required.")
    if "timezone" in changes:
        _timezone(changes["timezone"])
    if "country" in changes:
        changes["country"] = changes["country"].upper()[:2]
    before = audit.snapshot(site, SITE_FIELDS)
    for k, v in changes.items():
        setattr(site, k, v)
    _save(site, "A site with this code already exists.", "duplicate_site_code")
    after = audit.snapshot(site, SITE_FIELDS)
    if before != after:
        audit.record("site.updated", actor=actor, organization=site.organization, target=site, before=before,
                     after=after, request=request)
    return site


def active_asset_count(site: Site) -> int:
    from apps.sites.selectors import assets_installed

    if not assets_installed():
        return 0
    from apps.assets.workflow import TERMINAL_STATES

    return site.assets.exclude(status__in=TERMINAL_STATES).count()


@transaction.atomic
def deactivate_site(site: Site, *, reason: str, actor, request=None) -> Site:
    reason = (reason or "").strip()
    if not reason:
        raise ValidationFailed("A reason is required to deactivate a site.", code="reason_required")
    ACTIVATION.get(site.status, "deactivate")  # raises InvalidTransition (409) when already inactive
    n = active_asset_count(site)
    if n:
        raise Conflict(f"Site has {n} active asset(s); retire, dispose or move them first.",
                       code="site_has_active_assets", details={"active_assets": n})
    previous, _ = ACTIVATION.apply(site, "deactivate")
    site.status_reason = reason[:300]
    site.save(update_fields=["status", "status_reason", "updated_at"])
    audit.record("site.deactivated", actor=actor, organization=site.organization, target=site,
                 before={"status": previous}, after={"status": site.status, "reason": site.status_reason},
                 request=request)
    return site


@transaction.atomic
def reactivate_site(site: Site, *, actor, request=None) -> Site:
    previous, _ = ACTIVATION.apply(site, "reactivate")
    site.status_reason = ""
    site.save(update_fields=["status", "status_reason", "updated_at"])
    audit.record("site.reactivated", actor=actor, organization=site.organization, target=site,
                 before={"status": previous}, after={"status": site.status}, request=request)
    return site


def assert_site_active(site: Site):
    if site.status == INACTIVE:
        raise Conflict("This site is inactive.", code="site_inactive")


# --- zones -----------------------------------------------------------------------------------------


def _depth_above(zone: Zone | None) -> int:
    """Number of ancestors of ``zone`` including itself (walks up; bounded, detects corrupt cycles)."""
    depth = 0
    seen = set()
    while zone is not None:
        if zone.pk in seen:
            raise ValidationFailed("Zone hierarchy contains a cycle.", code="zone_cycle")
        seen.add(zone.pk)
        depth += 1
        zone = zone.parent
    return depth


def _height_below(zone: Zone) -> int:
    """Levels of descendants below ``zone`` (0 = leaf)."""
    level = [zone.pk]
    height = 0
    while True:
        level = list(Zone.objects.filter(parent_id__in=level).values_list("pk", flat=True))
        if not level:
            return height
        height += 1
        if height > MAX_ZONE_DEPTH:
            return height


def validate_zone_parent(site: Site, zone: Zone | None, parent: Zone | None, zone_type: str):
    if parent is None:
        return
    if parent.organization_id != site.organization_id or parent.site_id != site.pk:
        raise ValidationFailed("The parent location must belong to the same site.", code="invalid_parent")
    if zone_type == Zone.ZoneType.BUILDING:
        raise ValidationFailed("A building must be a top-level location of the site.", code="invalid_parent")
    if parent.status != "ACTIVE":
        raise ValidationFailed("The parent location is inactive.", code="invalid_parent")
    if zone is not None:
        node = parent
        while node is not None:  # parent chain must not reach the zone itself
            if node.pk == zone.pk:
                raise ValidationFailed("A location cannot be placed under itself or its own descendant.",
                                       code="zone_cycle")
            node = node.parent
    total = _depth_above(parent) + 1 + (_height_below(zone) if zone is not None else 0)
    if total > MAX_ZONE_DEPTH:
        raise ValidationFailed(f"Location hierarchy is limited to {MAX_ZONE_DEPTH} levels.", code="zone_too_deep")


def _check_zone_names(site: Site, zone: Zone | None, name: str, parent: Zone | None, code: str):
    siblings = Zone.objects.filter(site=site, parent=parent, name__iexact=name)
    if zone is not None:
        siblings = siblings.exclude(pk=zone.pk)
    if siblings.exists():
        raise Conflict("A location with this name already exists here.", code="duplicate_zone_name")
    if code:
        dup = Zone.objects.filter(site=site, code__iexact=code)
        if zone is not None:
            dup = dup.exclude(pk=zone.pk)
        if dup.exists():
            raise Conflict("A location with this code already exists in the site.", code="duplicate_zone_code")


@transaction.atomic
def create_zone(site: Site, *, actor, request=None, parent: Zone | None = None, **data) -> Zone:
    assert_site_active(site)
    data = _clean(data, ZONE_FIELDS)
    name = data.get("name", "")
    if not name:
        raise ValidationFailed("Location name is required.")
    zone_type = data.get("zone_type") or Zone.ZoneType.ZONE
    if zone_type not in Zone.ZoneType.values:
        raise ValidationFailed("Unknown location type.", code="invalid_zone_type")
    code = _code(data.get("code", ""), what="Location code", required=False)
    validate_zone_parent(site, None, parent, zone_type)
    _check_zone_names(site, None, name, parent, code)
    zone = Zone(organization=site.organization, site=site, parent=parent, zone_type=zone_type, name=name,
                code=code, description=data.get("description", ""))
    _save(zone, "A location with this name or code already exists.", "duplicate_zone")
    audit.record("zone.created", actor=actor, organization=site.organization, target=zone,
                 after=audit.snapshot(zone, ZONE_FIELDS), metadata={"site": site.code}, request=request)
    return zone


@transaction.atomic
def update_zone(zone: Zone, *, actor, request=None, parent=..., **changes) -> Zone:
    """``parent`` left as Ellipsis means unchanged; None moves the location to the top level of its site."""
    changes = _clean(changes, ZONE_FIELDS)
    site = zone.site
    name = changes.get("name", zone.name)
    if not name:
        raise ValidationFailed("Location name is required.")
    zone_type = changes.get("zone_type", zone.zone_type)
    if zone_type not in Zone.ZoneType.values:
        raise ValidationFailed("Unknown location type.", code="invalid_zone_type")
    code = _code(changes["code"], what="Location code", required=False) if "code" in changes else zone.code
    new_parent = zone.parent if parent is ... else parent
    if zone_type == Zone.ZoneType.BUILDING and new_parent is not None:
        raise ValidationFailed("A building must be a top-level location of the site.", code="invalid_parent")
    if new_parent != zone.parent or zone_type != zone.zone_type:
        validate_zone_parent(site, zone, new_parent, zone_type)
    _check_zone_names(site, zone, name, new_parent, code)
    before = audit.snapshot(zone, ZONE_FIELDS)
    zone.name, zone.zone_type, zone.code, zone.parent = name, zone_type, code, new_parent
    if "description" in changes:
        zone.description = changes["description"]
    _save(zone, "A location with this name or code already exists.", "duplicate_zone")
    after = audit.snapshot(zone, ZONE_FIELDS)
    if before != after:
        audit.record("zone.moved" if before["parent_id"] != after["parent_id"] else "zone.updated", actor=actor,
                     organization=site.organization, target=zone, before=before, after=after,
                     metadata={"site": site.code}, request=request)
    return zone


@transaction.atomic
def deactivate_zone(zone: Zone, *, reason: str, actor, request=None) -> Zone:
    from apps.sites.selectors import assets_installed

    reason = (reason or "").strip()
    if not reason:
        raise ValidationFailed("A reason is required to deactivate a location.", code="reason_required")
    ACTIVATION.get(zone.status, "deactivate")
    if zone.children.filter(status="ACTIVE").exists():
        raise Conflict("Deactivate the active child locations first.", code="zone_has_active_children")
    n = 0
    if assets_installed():
        from apps.assets.workflow import TERMINAL_STATES

        n = zone.assets.exclude(status__in=TERMINAL_STATES).count()
    if n:
        raise Conflict(f"Location has {n} active asset(s); move or retire them first.",
                       code="zone_has_active_assets", details={"active_assets": n})
    previous, _ = ACTIVATION.apply(zone, "deactivate")
    zone.status_reason = reason[:300]
    zone.save(update_fields=["status", "status_reason", "updated_at"])
    audit.record("zone.deactivated", actor=actor, organization=zone.organization, target=zone,
                 before={"status": previous}, after={"status": zone.status, "reason": zone.status_reason},
                 metadata={"site": zone.site.code}, request=request)
    return zone


@transaction.atomic
def reactivate_zone(zone: Zone, *, actor, request=None) -> Zone:
    ACTIVATION.get(zone.status, "reactivate")
    assert_site_active(zone.site)
    if zone.parent_id and zone.parent.status != "ACTIVE":
        raise Conflict("Reactivate the parent location first.", code="parent_inactive")
    previous, _ = ACTIVATION.apply(zone, "reactivate")
    zone.status_reason = ""
    zone.save(update_fields=["status", "status_reason", "updated_at"])
    audit.record("zone.reactivated", actor=actor, organization=zone.organization, target=zone,
                 before={"status": previous}, after={"status": zone.status}, metadata={"site": zone.site.code},
                 request=request)
    return zone


# --- operating calendars --------------------------------------------------------------------------


def _validate_calendar(data: dict) -> dict:
    days = data.get("working_days", [])
    if not isinstance(days, list) or any(not isinstance(d, int) or isinstance(d, bool) or not 1 <= d <= 7
                                         for d in days):
        raise ValidationFailed("Working days must be ISO weekday numbers 1 (Monday) to 7 (Sunday).",
                               code="invalid_working_days")
    data["working_days"] = sorted(set(days))
    if data.get("is_24x7"):
        data["start_time"] = data["end_time"] = None
        if not data["working_days"]:
            data["working_days"] = [1, 2, 3, 4, 5, 6, 7]
        return data
    start, end = data.get("start_time"), data.get("end_time")
    if not isinstance(start, time) or not isinstance(end, time):
        raise ValidationFailed("Start and end time are required unless the site works 24x7.", code="hours_required")
    if end <= start:
        raise ValidationFailed("End time must be after start time.", code="invalid_hours")
    if not data["working_days"]:
        raise ValidationFailed("Select at least one working day.", code="invalid_working_days")
    return data


def _set_default(calendar: OperatingCalendar):
    OperatingCalendar.objects.filter(site=calendar.site, is_default=True).exclude(pk=calendar.pk).update(
        is_default=False)


@transaction.atomic
def create_calendar(site: Site, *, actor, request=None, **data) -> OperatingCalendar:
    assert_site_active(site)
    data = _clean(data, CALENDAR_FIELDS)
    if not data.get("name"):
        raise ValidationFailed("Calendar name is required.")
    data.setdefault("is_24x7", False)
    data.setdefault("working_days", [])
    data = _validate_calendar(data)
    first = not site.calendars.exists()
    cal = OperatingCalendar(organization=site.organization, site=site, **data)
    cal.is_default = bool(data.get("is_default")) or first
    if cal.is_default:
        _set_default(cal)
    _save(cal, "A calendar with this name already exists for the site.", "duplicate_calendar")
    audit.record("calendar.created", actor=actor, organization=site.organization, target=cal,
                 after=audit.snapshot(cal, CALENDAR_FIELDS), metadata={"site": site.code}, request=request)
    return cal


@transaction.atomic
def update_calendar(cal: OperatingCalendar, *, actor, request=None, **changes) -> OperatingCalendar:
    changes = _clean(changes, CALENDAR_FIELDS)
    before = audit.snapshot(cal, CALENDAR_FIELDS)
    merged = {**before, **changes}
    if not merged.get("name"):
        raise ValidationFailed("Calendar name is required.")
    merged = _validate_calendar({
        **merged, "start_time": merged["start_time"] if not isinstance(merged["start_time"], str)
        else time.fromisoformat(merged["start_time"]),
        "end_time": merged["end_time"] if not isinstance(merged["end_time"], str)
        else time.fromisoformat(merged["end_time"])})
    if before["is_default"] and not merged["is_default"]:
        raise ValidationFailed("A site needs a default calendar; make another calendar the default instead.",
                               code="default_required")
    for k in CALENDAR_FIELDS:
        setattr(cal, k, merged[k])
    if cal.is_default:
        _set_default(cal)
    _save(cal, "A calendar with this name already exists for the site.", "duplicate_calendar")
    after = audit.snapshot(cal, CALENDAR_FIELDS)
    if before != after:
        audit.record("calendar.updated", actor=actor, organization=cal.organization, target=cal, before=before,
                     after=after, metadata={"site": cal.site.code}, request=request)
    return cal


@transaction.atomic
def delete_calendar(cal: OperatingCalendar, *, actor, request=None):
    if cal.is_default and cal.site.calendars.exclude(pk=cal.pk).exists():
        raise Conflict("Make another calendar the default before deleting this one.", code="default_required")
    snap, site, pk = audit.snapshot(cal, CALENDAR_FIELDS), cal.site, cal.pk
    org = cal.organization
    cal.holidays.all().delete()
    cal.delete()
    audit.record("calendar.deleted", actor=actor, organization=org, target_repr=f"Calendar {snap['name']} ({pk})",
                 before=snap, metadata={"site": site.code}, request=request)


@transaction.atomic
def add_holiday(cal: OperatingCalendar, *, date, name: str, actor, request=None) -> CalendarHoliday:
    name = (name or "").strip()
    if not name or date is None:
        raise ValidationFailed("Holiday date and name are required.")
    h = CalendarHoliday(organization=cal.organization, calendar=cal, date=date, name=name)
    _save(h, "This calendar already has a holiday on that date.", "duplicate_holiday")
    audit.record("calendar.holiday_added", actor=actor, organization=cal.organization, target=cal,
                 after={"date": date, "name": name}, request=request)
    return h


@transaction.atomic
def remove_holiday(holiday: CalendarHoliday, *, actor, request=None):
    cal, snap = holiday.calendar, {"date": holiday.date, "name": holiday.name}
    holiday.delete()
    audit.record("calendar.holiday_removed", actor=actor, organization=cal.organization, target=cal, before=snap,
                 request=request)


# --- contacts --------------------------------------------------------------------------------------


def _check_escalation_order(order):
    """The column is a smallint: refuse out-of-range input with a validation error instead of a database error."""
    if not isinstance(order, int) or isinstance(order, bool) or order < 1:
        raise ValidationFailed("Escalation order starts at 1.", code="invalid_escalation_order")
    if order > MAX_ESCALATION_ORDER:
        raise ValidationFailed(f"Escalation order cannot be higher than {MAX_ESCALATION_ORDER}.",
                               code="invalid_escalation_order")


@transaction.atomic
def add_contact(site: Site, *, actor, request=None, **data) -> SiteContact:
    data = _clean(data, CONTACT_FIELDS)
    if not data.get("name"):
        raise ValidationFailed("Contact name is required.")
    if not (data.get("phone") or data.get("email")):
        raise ValidationFailed("Provide a phone number or an email address.", code="contact_method_required")
    order = data.get("escalation_order") or (
        (site.contacts.order_by("-escalation_order").values_list("escalation_order", flat=True).first() or 0) + 1)
    _check_escalation_order(order)
    data["escalation_order"] = order
    c = SiteContact(organization=site.organization, site=site, **data)
    _save(c, f"Escalation level {order} is already used at this site.", "duplicate_escalation_order")
    audit.record("site.contact_added", actor=actor, organization=site.organization, target=c,
                 after=audit.snapshot(c, CONTACT_FIELDS), metadata={"site": site.code}, request=request)
    return c


@transaction.atomic
def update_contact(contact: SiteContact, *, actor, request=None, **changes) -> SiteContact:
    changes = _clean(changes, CONTACT_FIELDS)
    before = audit.snapshot(contact, CONTACT_FIELDS)
    for k, v in changes.items():
        setattr(contact, k, v)
    if not contact.name:
        raise ValidationFailed("Contact name is required.")
    if not (contact.phone or contact.email):
        raise ValidationFailed("Provide a phone number or an email address.", code="contact_method_required")
    _check_escalation_order(contact.escalation_order)
    _save(contact, f"Escalation level {contact.escalation_order} is already used at this site.",
          "duplicate_escalation_order")
    after = audit.snapshot(contact, CONTACT_FIELDS)
    if before != after:
        audit.record("site.contact_updated", actor=actor, organization=contact.organization, target=contact,
                     before=before, after=after, metadata={"site": contact.site.code}, request=request)
    return contact


@transaction.atomic
def remove_contact(contact: SiteContact, *, actor, request=None):
    snap, org, site, pk = audit.snapshot(contact, CONTACT_FIELDS), contact.organization, contact.site, contact.pk
    contact.delete()
    audit.record("site.contact_removed", actor=actor, organization=org,
                 target_repr=f"Contact {snap['name']} ({pk})", before=snap, metadata={"site": site.code},
                 request=request)
