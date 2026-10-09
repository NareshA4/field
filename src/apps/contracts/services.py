"""M10 service layer: providers, coverage agreements, the coverage / eligibility engine and renewal alerts.

Callers (API / UI / Celery) resolve objects through organization- and site-scoped selectors and check the
permission for the agreement's site first. This layer enforces tenancy of references, date validity, the overlap
rule and the eligibility rules, in one transaction with an audit record.

Eligibility (backend-authoritative, OUR IMPLEMENTATION DECISION; HPE only names "eligible claim validation"):
an agreement makes a piece of work eligible when it is active (``is_active``), the reference date lies in
``[start_date, end_date]`` (inclusive), the asset is one of its covered assets and the work type is not one of its
explicit exclusions. Overlap rule: one asset cannot hold two active agreements of the SAME kind whose date ranges
overlap (a warranty and an AMC may overlap). No claims processing: M10 only answers "is this covered / eligible".
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import Conflict, ValidationFailed
from apps.rbac import services as rbac

from .models import CoverageAgreement, CoverageCheck, CoverageExclusion, CoveredAsset, ServiceProvider

Kind = CoverageAgreement.Kind
AGREEMENT_FIELDS = ["reference", "title", "kind", "provider", "site", "start_date", "end_date", "terms",
                    "exclusion_notes", "sla_terms", "sla_profile", "renewal_alert_days", "is_active"]
WORK_TYPES = ("CORRECTIVE", "PREVENTIVE", "INSPECTION", "INSTALLATION", "OTHER")
KIND_ORDER = {Kind.WARRANTY: 0, Kind.AMC: 1, Kind.SERVICE_CONTRACT: 2}


# --- validation helpers ----------------------------------------------------------------------------------------------


def _text(value, what, *, minimum=1, maximum=200, required=True):
    value = (value or "").strip()
    if required and len(value) < minimum:
        raise ValidationFailed(f"{what} is required.", code="field_required")
    if len(value) > maximum:
        raise ValidationFailed(f"{what} is too long (max {maximum}).", code="field_too_long")
    return value


def _date(value, what):
    if isinstance(value, datetime.datetime):
        value = value.date()
    if not isinstance(value, datetime.date):
        try:
            value = datetime.date.fromisoformat(str(value))
        except ValueError as exc:
            raise ValidationFailed(f"{what} must be a valid date (YYYY-MM-DD).", code="invalid_date") from exc
    return value


def _dates(start, end):
    start, end = _date(start, "Start date"), _date(end, "End date")
    if end < start:
        raise ValidationFailed("The end date cannot be before the start date.", code="invalid_dates")
    if start.year < 1990 or end.year > 2100:
        raise ValidationFailed("Dates are outside the supported range.", code="invalid_dates")
    return start, end


def _alert_days(value):
    try:
        days = int(30 if value in (None, "") else value)
    except (TypeError, ValueError) as exc:
        raise ValidationFailed("Renewal alert days must be a whole number.", code="invalid_number") from exc
    if not 0 <= days <= 365:
        raise ValidationFailed("Renewal alert days must be between 0 and 365.", code="invalid_number")
    return days


def _kind(value):
    if value not in Kind.values:
        raise ValidationFailed("Unknown agreement type.", code="invalid_kind")
    return value


def _work_types(values):
    out = []
    for v in values or []:
        if v not in WORK_TYPES:
            raise ValidationFailed(f"Unknown work type {v!r}.", code="invalid_work_type")
        if v not in out:
            out.append(v)
    return out


def _check_provider(org, provider, *, must_be_active=True):
    if provider.organization_id != org.pk:
        raise ValidationFailed("Provider not found.", code="provider_unknown")
    if must_be_active and not provider.is_active:
        raise ValidationFailed("This provider is inactive.", code="provider_inactive")


def _check_sla_profile(org, profile, *, current=None):
    """The coverage SLA must be one of this organization's ACTIVE profiles (an already-linked inactive one may stay)."""
    if profile is None:
        return
    if profile.organization_id != org.pk:
        raise ValidationFailed("SLA profile belongs to a different organization.", code="cross_tenant_sla_profile")
    if profile.pk != current and not type(profile).objects.for_organization(org).filter(
            pk=profile.pk, is_active=True).exists():  # read fresh: the caller's object may be stale
        raise ValidationFailed("The SLA profile is inactive.", code="sla_profile_inactive")


def _check_site(org, site):
    if site.organization_id != org.pk:
        raise ValidationFailed("Site not found.", code="site_unknown")
    if getattr(site, "status", "ACTIVE") != "ACTIVE":
        raise ValidationFailed("Agreements can only be created for an active site.", code="site_inactive")


def _check_assets(org, site, assets):
    if not assets:
        raise ValidationFailed("Select at least one covered asset.", code="assets_required")
    for a in assets:
        if a.organization_id != org.pk:
            raise ValidationFailed("Asset not found.", code="asset_unknown")
        if a.site_id != site.pk:
            raise ValidationFailed(f"Asset {a.asset_tag} is not at site {site.code}; an agreement covers one site.",
                                   code="asset_wrong_site")


def _assert_no_overlap(org, agreement_kind, start, end, asset_ids, *, exclude=None):
    """Same asset + same kind + overlapping dates + both active = conflict (assets are locked by the caller)."""
    clash = CoveredAsset.objects.for_organization(org).filter(
        asset_id__in=asset_ids, agreement__kind=agreement_kind, agreement__is_active=True,
        agreement__start_date__lte=end, agreement__end_date__gte=start).select_related("agreement", "asset")
    if exclude is not None:
        clash = clash.exclude(agreement=exclude)
    hit = clash.first()
    if hit:
        raise Conflict(
            f"Asset {hit.asset.asset_tag} already has an active {hit.agreement.get_kind_display().lower()} "
            f"({hit.agreement.reference}) overlapping {start} to {end}.", code="coverage_overlap")


def _lock_assets(org, asset_ids):
    from apps.assets.models import Asset

    list(Asset.objects.for_organization(org).select_for_update(of=("self",)).filter(pk__in=asset_ids)
         .order_by("pk").values_list("pk", flat=True))


# --- providers -------------------------------------------------------------------------------------------------------


PROVIDER_FIELDS = ["name", "contact_name", "email", "phone", "notes", "is_active"]


@transaction.atomic
def create_provider(org, *, name, contact_name="", email="", phone="", notes="", actor, request=None):
    name = _text(name, "Name", minimum=2, maximum=150)
    if ServiceProvider.objects.for_organization(org).filter(name__iexact=name).exists():
        raise Conflict("A provider with that name already exists.", code="provider_name_taken")
    p = ServiceProvider(organization=org, name=name, contact_name=_text(contact_name, "Contact", maximum=120,
                                                                         required=False),
                        email=_text(email, "Email", maximum=254, required=False),
                        phone=_text(phone, "Phone", maximum=40, required=False),
                        notes=_text(notes, "Notes", maximum=500, required=False))
    p.save()
    audit.record("contract.provider_created", actor=actor, organization=org, target=p,
                 after=audit.snapshot(p, PROVIDER_FIELDS), request=request)
    return p


@transaction.atomic
def update_provider(provider, *, actor, request=None, **changes):
    provider = ServiceProvider.objects.select_for_update(of=("self",)).get(pk=provider.pk)
    unknown = set(changes) - {"name", "contact_name", "email", "phone", "notes"}
    if unknown:
        raise ValidationFailed(f"Fields cannot be edited: {', '.join(sorted(unknown))}.", code="field_not_editable")
    before = audit.snapshot(provider, PROVIDER_FIELDS)
    if "name" in changes:
        name = _text(changes["name"], "Name", minimum=2, maximum=150)
        if ServiceProvider.objects.for_organization(provider.organization).filter(
                name__iexact=name).exclude(pk=provider.pk).exists():
            raise Conflict("A provider with that name already exists.", code="provider_name_taken")
        provider.name = name
    for f, n, m in (("contact_name", "Contact", 120), ("email", "Email", 254), ("phone", "Phone", 40),
                    ("notes", "Notes", 500)):
        if f in changes:
            setattr(provider, f, _text(changes[f], n, maximum=m, required=False))
    provider.save()
    after = audit.snapshot(provider, PROVIDER_FIELDS)
    if after != before:
        audit.record("contract.provider_updated", actor=actor, organization=provider.organization, target=provider,
                     before=before, after=after, request=request)
    return provider


@transaction.atomic
def set_provider_active(provider, active: bool, *, actor, request=None):
    provider = ServiceProvider.objects.select_for_update(of=("self",)).get(pk=provider.pk)
    if provider.is_active == active:
        return provider
    if not active:
        today = datetime.date.today()
        if CoverageAgreement.objects.for_organization(provider.organization).filter(
                provider=provider, is_active=True, end_date__gte=today).exists():
            raise Conflict("The provider still has active agreements; deactivate or let them expire first.",
                           code="provider_in_use")
    provider.is_active = active
    provider.save(update_fields=["is_active", "updated_at"])
    audit.record("contract.provider_enabled" if active else "contract.provider_disabled", actor=actor,
                 organization=provider.organization, target=provider, before={"is_active": not active},
                 after={"is_active": active}, request=request)
    return provider


# --- agreements ------------------------------------------------------------------------------------------------------


def _set_exclusions(agreement, work_types):
    wanted = set(work_types)
    existing = {e.work_type: e for e in agreement.exclusions.all()}
    for wt, row in existing.items():
        if wt not in wanted:
            row.delete()
    for wt in wanted - set(existing):
        CoverageExclusion(organization=agreement.organization, agreement=agreement, work_type=wt).save()


def _snap(a):
    return {**audit.snapshot(a, AGREEMENT_FIELDS),
            "assets": sorted(x.asset.asset_tag for x in a.covered_assets.select_related("asset")),
            "excluded_work_types": sorted(e.work_type for e in a.exclusions.all())}


@transaction.atomic
def create_agreement(org, *, kind, reference, title, provider, site, start_date, end_date, assets, terms="",
                     exclusion_notes="", sla_terms="", sla_profile=None, renewal_alert_days=30,
                     excluded_work_types=(), renewed_from=None, actor, request=None) -> CoverageAgreement:
    kind = _kind(kind)
    reference = _text(reference, "Reference", maximum=80)
    title = _text(title, "Title", minimum=3, maximum=200)
    start, end = _dates(start_date, end_date)
    _check_provider(org, provider)
    _check_site(org, site)
    _check_sla_profile(org, sla_profile)
    assets = list({a.pk: a for a in assets}.values())
    _check_assets(org, site, assets)
    if CoverageAgreement.objects.for_organization(org).filter(reference__iexact=reference).exists():
        raise Conflict("An agreement with that reference already exists.", code="reference_taken")
    _lock_assets(org, [a.pk for a in assets])
    _assert_no_overlap(org, kind, start, end, [a.pk for a in assets])
    a = CoverageAgreement(
        organization=org, reference=reference, title=title, kind=kind, provider=provider, site=site,
        start_date=start, end_date=end, terms=(terms or "").strip(), exclusion_notes=(exclusion_notes or "").strip(),
        sla_terms=_text(sla_terms, "SLA terms", maximum=300, required=False), sla_profile=sla_profile,
        renewal_alert_days=_alert_days(renewal_alert_days), renewed_from=renewed_from, created_by=actor)
    try:
        with transaction.atomic():
            a.save()
    except IntegrityError as exc:
        raise Conflict("An agreement with that reference already exists.", code="reference_taken") from exc
    for asset in assets:
        CoveredAsset(organization=org, agreement=a, asset=asset).save()
    _set_exclusions(a, _work_types(excluded_work_types))
    audit.record("contract.agreement_created", actor=actor, organization=org, target=a, after=_snap(a),
                 request=request)
    return a


@transaction.atomic
def update_agreement(agreement, *, actor, request=None, **changes) -> CoverageAgreement:
    agreement = CoverageAgreement.objects.select_for_update(of=("self",)).select_related("provider", "site").get(
        pk=agreement.pk)
    allowed = {"title", "provider", "start_date", "end_date", "terms", "exclusion_notes", "sla_terms",
               "sla_profile", "renewal_alert_days", "excluded_work_types", "reference", "kind"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValidationFailed(f"Fields cannot be edited: {', '.join(sorted(unknown))}.", code="field_not_editable")
    if "kind" in changes and changes["kind"] != agreement.kind:
        raise ValidationFailed("The agreement type cannot be changed; create a new agreement.", code="kind_locked")
    org = agreement.organization
    before = _snap(agreement)
    if "reference" in changes:
        ref = _text(changes["reference"], "Reference", maximum=80)
        if CoverageAgreement.objects.for_organization(org).filter(reference__iexact=ref).exclude(
                pk=agreement.pk).exists():
            raise Conflict("An agreement with that reference already exists.", code="reference_taken")
        agreement.reference = ref
    if "title" in changes:
        agreement.title = _text(changes["title"], "Title", minimum=3, maximum=200)
    if "provider" in changes:
        _check_provider(org, changes["provider"], must_be_active=changes["provider"].pk != agreement.provider_id)
        agreement.provider = changes["provider"]
    for f in ("terms", "exclusion_notes"):
        if f in changes:
            setattr(agreement, f, (changes[f] or "").strip())
    if "sla_terms" in changes:
        agreement.sla_terms = _text(changes["sla_terms"], "SLA terms", maximum=300, required=False)
    if "sla_profile" in changes:
        _check_sla_profile(org, changes["sla_profile"], current=agreement.sla_profile_id)
        agreement.sla_profile = changes["sla_profile"]
    if "renewal_alert_days" in changes:
        agreement.renewal_alert_days = _alert_days(changes["renewal_alert_days"])
    if "start_date" in changes or "end_date" in changes:
        start, end = _dates(changes.get("start_date", agreement.start_date),
                            changes.get("end_date", agreement.end_date))
        if agreement.is_active:
            ids = list(agreement.covered_assets.values_list("asset_id", flat=True))
            _lock_assets(org, ids)
            _assert_no_overlap(org, agreement.kind, start, end, ids, exclude=agreement)
        if (start, end) != (agreement.start_date, agreement.end_date):
            agreement.renewal_alerted_at = None  # a new end date re-arms the renewal alert
        agreement.start_date, agreement.end_date = start, end
    agreement.save()
    if "excluded_work_types" in changes:
        _set_exclusions(agreement, _work_types(changes["excluded_work_types"]))
    after = _snap(agreement)
    if after != before:
        audit.record("contract.agreement_updated", actor=actor, organization=org, target=agreement, before=before,
                     after=after, request=request)
    return agreement


@transaction.atomic
def add_asset(agreement, asset, *, actor, request=None):
    agreement = CoverageAgreement.objects.select_for_update(of=("self",)).get(pk=agreement.pk)
    org = agreement.organization
    _check_assets(org, agreement.site, [asset])
    if agreement.covered_assets.filter(asset=asset).exists():
        raise Conflict("The asset is already covered by this agreement.", code="asset_already_covered")
    if agreement.is_active:
        _lock_assets(org, [asset.pk])
        _assert_no_overlap(org, agreement.kind, agreement.start_date, agreement.end_date, [asset.pk],
                           exclude=agreement)
    link = CoveredAsset(organization=org, agreement=agreement, asset=asset)
    link.save()
    audit.record("contract.asset_added", actor=actor, organization=org, target=agreement,
                 after={"asset": asset.asset_tag}, request=request)
    return link


@transaction.atomic
def remove_asset(agreement, asset, *, actor, request=None):
    agreement = CoverageAgreement.objects.select_for_update(of=("self",)).get(pk=agreement.pk)
    links = agreement.covered_assets.filter(asset=asset)
    if not links.exists():
        raise ValidationFailed("The asset is not covered by this agreement.", code="asset_not_covered")
    if agreement.covered_assets.count() == 1:
        raise ValidationFailed("An agreement must cover at least one asset; deactivate it instead.",
                               code="last_asset")
    links.delete()
    audit.record("contract.asset_removed", actor=actor, organization=agreement.organization, target=agreement,
                 before={"asset": asset.asset_tag}, request=request)


@transaction.atomic
def set_agreement_active(agreement, active: bool, *, reason="", actor, request=None):
    agreement = CoverageAgreement.objects.select_for_update(of=("self",)).select_related("provider").get(
        pk=agreement.pk)
    if agreement.is_active == active:
        return agreement
    if active:
        ids = list(agreement.covered_assets.values_list("asset_id", flat=True))
        _lock_assets(agreement.organization, ids)
        _assert_no_overlap(agreement.organization, agreement.kind, agreement.start_date, agreement.end_date, ids,
                           exclude=agreement)
        agreement.deactivation_reason = ""
    else:
        agreement.deactivation_reason = _text(reason, "Reason", maximum=300)
    agreement.is_active = active
    agreement.save(update_fields=["is_active", "deactivation_reason", "updated_at"])
    audit.record("contract.agreement_reactivated" if active else "contract.agreement_deactivated", actor=actor,
                 organization=agreement.organization, target=agreement, before={"is_active": not active},
                 after={"is_active": active, "reason": agreement.deactivation_reason}, request=request)
    return agreement


@transaction.atomic
def renew_agreement(agreement, *, new_end_date, reference, actor, request=None) -> CoverageAgreement:
    """Creates the follow-on agreement: same type, provider, site, terms, exclusions and assets; starts the day
    after the old one ends. One renewal per agreement (``renewed_from`` is one-to-one)."""
    agreement = CoverageAgreement.objects.select_for_update(of=("self",)).select_related("provider", "site").get(
        pk=agreement.pk)
    if not agreement.is_active:
        raise Conflict("An inactive agreement cannot be renewed.", code="agreement_inactive")
    if CoverageAgreement.objects.for_organization(agreement.organization).filter(renewed_from=agreement).exists():
        raise Conflict("This agreement has already been renewed.", code="already_renewed")
    new_start = agreement.end_date + datetime.timedelta(days=1)
    assets = [c.asset for c in agreement.covered_assets.select_related("asset")]
    new = create_agreement(
        agreement.organization, kind=agreement.kind, reference=reference, title=agreement.title,
        provider=agreement.provider, site=agreement.site, start_date=new_start, end_date=new_end_date,
        assets=assets, terms=agreement.terms, exclusion_notes=agreement.exclusion_notes,
        sla_terms=agreement.sla_terms, sla_profile=agreement.sla_profile, renewal_alert_days=agreement.renewal_alert_days,
        excluded_work_types=[e.work_type for e in agreement.exclusions.all()], renewed_from=agreement,
        actor=actor, request=request)
    audit.record("contract.agreement_renewed", actor=actor, organization=agreement.organization, target=agreement,
                 after={"renewal": new.reference, "new_end_date": new.end_date}, request=request)
    return new


# --- coverage engine -------------------------------------------------------------------------------------------------


@dataclass
class Entry:
    agreement: CoverageAgreement
    status: str  # COVERING | EXCLUDED | EXPIRED | NOT_STARTED | INACTIVE
    eligible: bool
    reason: str


@dataclass
class CoverageResult:
    asset: object
    on: datetime.date
    work_type: str
    entries: list = field(default_factory=list)

    @property
    def covered(self) -> bool:
        """The asset has an active, in-date agreement (regardless of the work type)."""
        return any(e.status in ("COVERING", "EXCLUDED") for e in self.entries)

    @property
    def eligible(self) -> bool:
        return any(e.eligible for e in self.entries)

    @property
    def best(self):
        pool = [e for e in self.entries if e.eligible] or [e for e in self.entries if e.status == "EXCLUDED"]
        return sorted(pool, key=lambda e: (KIND_ORDER[e.agreement.kind], e.agreement.end_date))[0] if pool else None

    @property
    def reason(self) -> str:
        if self.eligible:
            return self.best.reason
        if self.best:
            return self.best.reason
        if not self.entries:
            return "The asset is not linked to any warranty, AMC or service contract."
        return "; ".join(f"{e.agreement.reference}: {e.reason}" for e in self.entries)[:500]


def evaluate(org, asset, on: datetime.date | None = None, work_type: str = "") -> CoverageResult:
    """Coverage / eligibility of one asset on a date for a work type. Reads only the caller's organization (an
    asset of another organization yields no entries) and never trusts caller-supplied flags."""
    on = on or datetime.date.today()
    result = CoverageResult(asset=asset, on=on, work_type=work_type)
    if asset.organization_id != org.pk:
        return result
    links = (CoveredAsset.objects.for_organization(org).filter(asset=asset)
             .select_related("agreement__provider", "agreement__site").prefetch_related("agreement__exclusions"))
    for link in links:
        ag = link.agreement
        excluded = {e.work_type for e in ag.exclusions.all()}
        if not ag.is_active:
            result.entries.append(Entry(ag, "INACTIVE", False, "the agreement is inactive"))
        elif on < ag.start_date:
            result.entries.append(Entry(ag, "NOT_STARTED", False, f"coverage starts on {ag.start_date}"))
        elif on > ag.end_date:
            result.entries.append(Entry(ag, "EXPIRED", False, f"coverage expired on {ag.end_date}"))
        elif work_type and work_type in excluded:
            result.entries.append(Entry(ag, "EXCLUDED", False,
                                        f"{ag.get_kind_display().lower()} {ag.reference} excludes "
                                        f"{work_type.lower()} work"))
        else:
            result.entries.append(Entry(ag, "COVERING", True,
                                        f"covered by {ag.get_kind_display().lower()} {ag.reference} "
                                        f"({ag.provider.name}) until {ag.end_date}"))
    return result


def reference_date_for(work_order) -> datetime.date:
    """The date coverage is judged on: when the failure occurred (linked request) else when the order was raised,
    in the site's time zone."""
    moment = work_order.source_request.occurred_at if work_order.source_request_id else work_order.created_at
    try:
        zone = ZoneInfo(work_order.site.timezone or "UTC")
    except Exception:  # unknown zone name: UTC
        zone = ZoneInfo("UTC")
    return moment.astimezone(zone).date()


@transaction.atomic
def record_check(work_order, *, actor, request=None) -> CoverageCheck:
    """Evaluates and PERSISTS the eligibility of a work order (evidence; a new check appends a new row)."""
    org = work_order.organization
    on = reference_date_for(work_order)
    res = evaluate(org, work_order.asset, on, work_order.work_type)
    best = res.best
    check = CoverageCheck(organization=org, work_order=work_order, asset=work_order.asset,
                          agreement=best.agreement if best else None, work_type=work_order.work_type,
                          reference_date=on, eligible=res.eligible, reason=res.reason[:500], checked_by=actor)
    check.save()
    audit.record("contract.coverage_checked", actor=actor, organization=org, target=work_order,
                 after={"eligible": check.eligible, "agreement": best.agreement.reference if best else None,
                        "reason": check.reason, "reference_date": on}, request=request)
    return check


# --- renewal alerts --------------------------------------------------------------------------------------------------


def run_alerts(org, today: datetime.date | None = None) -> int:
    """Notifies users holding ``contract.update`` for the agreement's site, once per agreement, when it enters its
    renewal-alert window and has no renewal yet. Idempotent."""
    from apps.notifications.services import notify
    from apps.tenancy.models import Membership

    today = today or datetime.date.today()
    due = (CoverageAgreement.objects.for_organization(org)
           .filter(is_active=True, renewal_alerted_at__isnull=True, renewal__isnull=True, end_date__gte=today)
           .select_related("site"))
    members = list(Membership.objects.for_organization(org).filter(status=Membership.Status.ACTIVE)
                   .select_related("user"))
    sent = 0
    for ag in due:
        if (ag.end_date - today).days > ag.renewal_alert_days:
            continue
        with transaction.atomic():
            locked = CoverageAgreement.objects.select_for_update(of=("self",)).get(pk=ag.pk)
            if locked.renewal_alerted_at is not None:
                continue
            users = [m.user for m in members if rbac.has_permission(m, "contract.update", ag.site_id)]
            notify(org, users, title=f"{ag.get_kind_display()} {ag.reference} expires {ag.end_date}",
                   body=f"{ag.title} ends in {(ag.end_date - today).days} day(s). Renew it or let it lapse.",
                   link=f"/app/contracts/agreements/{ag.pk}/", source="contracts.renewal")
            locked.renewal_alerted_at = timezone.now()
            locked.save(update_fields=["renewal_alerted_at", "updated_at"])
            audit.record("contract.renewal_alert", organization=org, target=locked,
                         after={"end_date": ag.end_date, "notified": len(users)})
            sent += 1
    return sent
