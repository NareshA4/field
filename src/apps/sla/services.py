"""M11 service layer: SLA profiles / targets / escalation rules, tracking of requests and work orders, the monitor
(warning, breach, escalation) and breach acknowledgement.

Time rules (D-044): timers start from the persisted ``created_at`` of the request / work order (never client time);
response and resolution are separate; a timer pauses ONLY while the subject is in a state the profile explicitly
lists in ``pause_states`` (tokens like ``WORK_ORDER:ON_HOLD``), otherwise it keeps running; pausing shifts the
unmet due times by the paused duration. Due times are absolute and persisted on the tracking row, so editing a
profile never rewrites running trackings. All elapsed-time logic takes ``now`` as a parameter (the monitor passes
``timezone.now()``; tests pass a controllable clock) - production logic is not weakened for tests.

Idempotency: every warning / breach / escalation is an ``SLAEvent`` with a unique ``dedupe_key`` per tracking and a
breach is unique per (tracking, target), both inserted under the tracking row lock; a second run, a Celery retry or
a concurrent worker finds them and does nothing (no duplicate rows, no duplicate notifications).
Hooks (``on_request_*``, ``on_work_order_*``) are called by the authoritative M05 / M06 services; M11 never changes
a request or a work order and never duplicates their state machines.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import Conflict, ValidationFailed

from .models import (
    REQUEST,
    RESOLUTION,
    RESPONSE,
    WORK_ORDER,
    EscalationRule,
    SLABreach,
    SLAEvent,
    SLAPauseEvent,
    SLAProfile,
    SLATarget,
    SLATracking,
)

log = logging.getLogger(__name__)
Status, TS = SLATracking.Status, SLATracking.TargetState

REQUEST_PRIORITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
WORK_ORDER_PRIORITIES = ("LOW", "MEDIUM", "HIGH", "URGENT")
SEVERITY = {"LOW": "MINOR", "MEDIUM": "MINOR", "HIGH": "MAJOR", "CRITICAL": "CRITICAL", "URGENT": "CRITICAL"}
MAX_RULES_PER_PROFILE = 6
PROFILE_FIELDS = ["name", "description", "applies_to", "site", "work_type", "pause_states", "is_active",
                  "coverage_only", "client", "contract", "asset", "asset_category", "service_type"]


def priorities_for(applies_to: str) -> tuple:
    return REQUEST_PRIORITIES if applies_to == REQUEST else WORK_ORDER_PRIORITIES


def valid_pause_tokens(applies_to: str) -> set[str]:
    from apps.incidents.workflow import STATES as REQUEST_STATES
    from apps.workorders.workflow import STATES as WO_STATES

    tokens = {f"WORK_ORDER:{s}" for s in WO_STATES}
    if applies_to == REQUEST:
        tokens |= {f"REQUEST:{s}" for s in REQUEST_STATES}
    return tokens


# --- profiles / targets / rules ------------------------------------------------------------------------------------------


def parse_duration_minutes(val, what="Duration") -> int:
    if val is None or str(val).strip() == "":
        raise ValidationFailed(f"{what} is required.", code="invalid_minutes")
    if isinstance(val, (int, float)):
        v = int(val)
    else:
        s = str(val).strip().lower()
        if s.isdigit():
            v = int(s)
        else:
            import re
            total_minutes = 0
            matched = False
            d = re.search(r'([\d.]+)\s*(?:days?|d)\b', s)
            if d:
                total_minutes += int(round(float(d.group(1)) * 24 * 60))
                matched = True
            h = re.search(r'([\d.]+)\s*(?:hours?|hrs?|hr|h)\b', s)
            if h:
                total_minutes += int(round(float(h.group(1)) * 60))
                matched = True
            m = re.search(r'([\d.]+)\s*(?:minutes?|mins?|min|m)\b', s)
            if m:
                total_minutes += int(round(float(m.group(1))))
                matched = True
            if matched:
                v = total_minutes
            else:
                try:
                    v = int(round(float(s)))
                except ValueError as exc:
                    raise ValidationFailed(
                        f"{what} must be a whole number of minutes or duration format (e.g. '15 minutes', '4 hours').",
                        code="invalid_minutes",
                    ) from exc
    if not 1 <= v <= 60 * 24 * 365:
        raise ValidationFailed(f"{what} must be between 1 minute and one year.", code="invalid_minutes")
    return v


def parse_percentage(val, what="Warning Alert") -> int:
    if val is None or str(val).strip() == "":
        raise ValidationFailed(f"{what} is required.", code="invalid_warning")
    if isinstance(val, (int, float)):
        v = int(val)
    else:
        s = str(val).strip().rstrip("%").strip()
        try:
            v = int(round(float(s)))
        except ValueError as exc:
            raise ValidationFailed(f"{what} must be a whole percentage.", code="invalid_warning") from exc
    if not 1 <= v <= 99:
        raise ValidationFailed(f"{what} must be between 1 and 99 percent.", code="invalid_warning")
    return v


def _minutes(value, what):
    return parse_duration_minutes(value, what)


def _pause_states(applies_to, values) -> list:
    out = sorted({str(v).strip() for v in (values or []) if str(v).strip()})
    bad = [v for v in out if v not in valid_pause_tokens(applies_to)]
    if bad:
        raise ValidationFailed(f"Unknown pause state(s): {', '.join(bad)}.", code="invalid_pause_state")
    return out


def _scope_values(*, applies_to, site, client, contract, asset, asset_category, service_type, work_type):
    return {"applies_to": applies_to, "site_id": getattr(site, "pk", None),
            "client_id": getattr(client, "pk", None), "contract_id": getattr(contract, "pk", None),
            "asset_id": getattr(asset, "pk", None), "asset_category_id": getattr(asset_category, "pk", None),
            "service_type": service_type or work_type or "", "work_type": ""}


def _ensure_scope_available(org, scope, *, current=None, coverage_only=False):
    if coverage_only:
        return
    qs = SLAProfile.objects.for_organization(org).filter(is_active=True, coverage_only=False,
        applies_to=scope["applies_to"], site_id=scope["site_id"], client_id=scope["client_id"],
        contract_id=scope["contract_id"], asset_id=scope["asset_id"],
        asset_category_id=scope["asset_category_id"])
    if current is not None:
        qs = qs.exclude(pk=current.pk)
    conflicts = [p for p in qs if (p.service_type or p.work_type) == scope["service_type"]]
    if conflicts:
        conflicting_names = ", ".join(f"'{p.name}'" for p in conflicts[:2])
        scope_desc = "organization-wide default" if not any([scope["site_id"], scope["client_id"], scope["contract_id"], scope["asset_id"], scope["asset_category_id"], scope["service_type"]]) else "this specific matching scope"
        applies_label = "Incident / Breakdown" if scope["applies_to"] == "REQUEST" else "Work Order"
        raise Conflict(
            f"An active SLA Profile ({conflicting_names}) already covers {scope_desc} for {applies_label}. Please edit or deactivate the existing profile before creating a duplicate.",
            code="sla_scope_conflict"
        )


@transaction.atomic
def create_profile(org, *, name, applies_to, actor, description="", site=None, work_type="", pause_states=(),
                   coverage_only=False, client=None, contract=None, asset=None, asset_category=None,
                   service_type="", is_active=True, request=None) -> SLAProfile:
    from apps.workorders.models import WorkOrder

    name = (name or "").strip()
    from apps.tenancy.models import Organization
    Organization.objects.select_for_update().get(pk=org.pk)
    if len(name) < 3:
        raise ValidationFailed("A name of at least 3 characters is required.", code="name_required")
    applies_to_str = str(applies_to).strip()
    if applies_to_str in ("Incident / Breakdown", "Incident / Breakdown and Service Request", "Incident", "REQUEST"):
        applies_to = REQUEST
    elif applies_to_str in ("Work Order", "WORK_ORDER"):
        applies_to = WORK_ORDER
    if applies_to not in SLAProfile.AppliesTo.values:
        raise ValidationFailed("Choose what the profile applies to.", code="invalid_applies_to")
    if site is not None and site.organization_id != org.pk:
        raise ValidationFailed("Site not found in this organization.", code="cross_tenant_site")
    for label, obj in (("client", client), ("contract", contract), ("asset", asset),
                       ("asset category", asset_category)):
        if obj is not None and obj.organization_id != org.pk:
            raise ValidationFailed(f"{label.title()} not found in this organization.", code="cross_tenant_scope")
    if client is not None:
        from apps.rbac.models import MembershipRole
        if not MembershipRole.objects.filter(membership=client, role__system_key="client_requester").exists():
            raise ValidationFailed("Select a client requester from this organization.", code="invalid_client")
    if asset is not None and site is not None and asset.site_id != site.pk:
        raise ValidationFailed("The selected asset must belong to the policy site.", code="scope_site_mismatch")
    if asset is not None and asset_category is not None and asset.category_id != asset_category.pk:
        raise ValidationFailed("The selected asset does not belong to the selected category.",
                               code="scope_category_mismatch")
    if contract is not None and site is not None and contract.site_id != site.pk:
        raise ValidationFailed("The selected contract must belong to the policy site.", code="scope_contract_mismatch")
    work_type = (work_type or "").strip()
    if work_type and (applies_to != WORK_ORDER or work_type not in WorkOrder.WorkType.values):
        raise ValidationFailed("A work type filter is only valid for work-order profiles.", code="invalid_work_type")
    service_type = (service_type or "").strip()
    if service_type:
        if applies_to == REQUEST:
            from apps.incidents.models import ServiceRequest
            choices = ServiceRequest.Kind.values
        else:
            choices = WorkOrder.WorkType.values
        if service_type not in choices:
            raise ValidationFailed("Choose a valid service type for this policy.", code="invalid_service_type")
    if SLAProfile.objects.for_organization(org).filter(name__iexact=name).exists():
        raise Conflict("A profile with this name already exists.", code="profile_name_taken")
    if is_active:
        _ensure_scope_available(org, _scope_values(applies_to=applies_to, site=site, client=client, contract=contract,
            asset=asset, asset_category=asset_category, service_type=service_type, work_type=work_type),
            coverage_only=bool(coverage_only))
    profile = SLAProfile(organization=org, name=name[:120], description=(description or "").strip()[:300],
                         applies_to=applies_to, site=site, client=client, contract=contract, asset=asset,
                         asset_category=asset_category, service_type=service_type, work_type=work_type,
                         coverage_only=bool(coverage_only), is_active=bool(is_active),
                         pause_states=_pause_states(applies_to, pause_states))
    try:
        with transaction.atomic():
            profile.save()
    except IntegrityError as exc:
        raise Conflict("An active profile already exists for this scope; deactivate it first.",
                       code="profile_scope_taken") from exc
    audit.record("sla.profile_created", actor=actor, organization=org, target=profile,
                 after=audit.snapshot(profile, PROFILE_FIELDS), request=request)
    return profile


@transaction.atomic
def update_profile(profile: SLAProfile, *, actor, request=None, **changes) -> SLAProfile:
    profile = SLAProfile.objects.select_for_update().get(pk=profile.pk)
    from apps.tenancy.models import Organization
    Organization.objects.select_for_update().get(pk=profile.organization_id)
    unknown = set(changes) - {"name", "description", "pause_states", "applies_to", "site", "client", "contract",
                              "asset", "asset_category", "service_type", "work_type", "coverage_only"}
    if unknown:
        raise ValidationFailed(f"Fields cannot be edited: {', '.join(sorted(unknown))}.", code="field_not_editable")
    before = audit.snapshot(profile, PROFILE_FIELDS)
    if "name" in changes:
        name = (changes["name"] or "").strip()
        if len(name) < 3:
            raise ValidationFailed("A name of at least 3 characters is required.", code="name_required")
        if SLAProfile.objects.for_organization(profile.organization).filter(name__iexact=name).exclude(
                pk=profile.pk).exists():
            raise Conflict("A profile with this name already exists.", code="profile_name_taken")
        profile.name = name[:120]
    if "description" in changes:
        profile.description = (changes["description"] or "").strip()[:300]
    if "applies_to" in changes:
        a_str = str(changes["applies_to"]).strip()
        if a_str in ("Incident / Breakdown", "Incident / Breakdown and Service Request", "Incident", "REQUEST"):
            changes["applies_to"] = REQUEST
        elif a_str in ("Work Order", "WORK_ORDER"):
            changes["applies_to"] = WORK_ORDER
        if changes["applies_to"] not in SLAProfile.AppliesTo.values:
            raise ValidationFailed("Choose what the policy applies to.", code="invalid_applies_to")
        profile.applies_to = changes["applies_to"]
    if "pause_states" in changes:
        profile.pause_states = _pause_states(profile.applies_to, changes["pause_states"])
    for field in ("site", "client", "contract", "asset", "asset_category"):
        if field in changes:
            value = changes[field]
            if value is not None and value.organization_id != profile.organization_id:
                raise ValidationFailed(f"{field.replace('_', ' ').title()} not found in this organization.",
                                       code="cross_tenant_scope")
            setattr(profile, field, value)
    if profile.client_id:
        from apps.rbac.models import MembershipRole
        if not MembershipRole.objects.filter(membership=profile.client,
                                              role__system_key="client_requester").exists():
            raise ValidationFailed("Select a client requester from this organization.", code="invalid_client")
    if "service_type" in changes:
        value = (changes["service_type"] or "").strip()
        if value:
            from apps.incidents.models import ServiceRequest
            from apps.workorders.models import WorkOrder
            allowed = (ServiceRequest.Kind.values if profile.applies_to == REQUEST else WorkOrder.WorkType.values)
            if value not in allowed:
                raise ValidationFailed("Choose a valid service type for this policy.", code="invalid_service_type")
        profile.service_type = value
    if "work_type" in changes:
        value = (changes["work_type"] or "").strip()
        from apps.workorders.models import WorkOrder
        if value and (profile.applies_to != WORK_ORDER or value not in WorkOrder.WorkType.values):
            raise ValidationFailed("Choose a valid Work Order type.", code="invalid_work_type")
        profile.work_type = value
    if "coverage_only" in changes:
        profile.coverage_only = bool(changes["coverage_only"])
    if "site" in changes and profile.asset_id and profile.asset.site_id != profile.site_id:
        raise ValidationFailed("The selected asset must belong to the policy site.", code="scope_site_mismatch")
    if profile.asset_id and profile.asset_category_id and profile.asset.category_id != profile.asset_category_id:
        raise ValidationFailed("The selected asset does not belong to the selected category.",
                               code="scope_category_mismatch")
    if profile.contract_id and profile.site_id and profile.contract.site_id != profile.site_id:
        raise ValidationFailed("The selected contract must belong to the policy site.", code="scope_contract_mismatch")
    if profile.contract_id and profile.contract.organization_id != profile.organization_id:
        raise ValidationFailed("Choose a contract from this organization.", code="cross_tenant_scope")
    _ensure_scope_available(profile.organization, _scope_values(applies_to=profile.applies_to, site=profile.site,
        client=profile.client, contract=profile.contract, asset=profile.asset,
        asset_category=profile.asset_category, service_type=profile.service_type,
        work_type=profile.work_type), current=profile, coverage_only=profile.coverage_only)
    profile.save()
    after = audit.snapshot(profile, PROFILE_FIELDS)
    if after != before:
        audit.record("sla.profile_updated", actor=actor, organization=profile.organization, target=profile,
                     before=before, after=after, request=request)
    return profile


@transaction.atomic
def set_profile_active(profile: SLAProfile, active: bool, *, actor, request=None) -> SLAProfile:
    profile = SLAProfile.objects.select_for_update().get(pk=profile.pk)
    from apps.tenancy.models import Organization
    Organization.objects.select_for_update().get(pk=profile.organization_id)
    if profile.is_active == active:
        return profile
    if active:
        if not profile.targets.exists():
            raise ValidationFailed(
                "Cannot activate an incomplete SLA Profile. Define at least one Priority Target (Response Target and Resolution Target) before activating.",
                code="missing_targets"
            )
        _ensure_scope_available(profile.organization, _scope_values(applies_to=profile.applies_to, site=profile.site,
            client=profile.client, contract=profile.contract, asset=profile.asset,
            asset_category=profile.asset_category, service_type=profile.service_type,
            work_type=profile.work_type), current=profile, coverage_only=profile.coverage_only)
    profile.is_active = active
    try:
        with transaction.atomic():
            profile.save(update_fields=["is_active", "updated_at"])
    except IntegrityError as exc:
        raise Conflict("Another active profile already covers this scope.", code="profile_scope_taken") from exc
    audit.record("sla.profile_activated" if active else "sla.profile_deactivated", actor=actor,
                 organization=profile.organization, target=profile, before={"is_active": not active},
                 after={"is_active": active}, request=request)
    return profile


@transaction.atomic
def set_target(profile: SLAProfile, *, priority, response_minutes, resolution_minutes, warning_percent=80, actor,
               request=None) -> SLATarget:
    """Creates or replaces the target of one priority. Running trackings keep their persisted due times."""
    profile = SLAProfile.objects.select_for_update().get(pk=profile.pk)
    priority_upper = str(priority).strip().upper()
    if priority_upper not in priorities_for(profile.applies_to):
        raise ValidationFailed(f"Priority must be one of {', '.join(priorities_for(profile.applies_to))}.",
                               code="invalid_priority")
    resp = _minutes(response_minutes, "Response Target")
    reso = _minutes(resolution_minutes, "Resolution Target")
    if reso < resp:
        raise ValidationFailed("The Resolution Target cannot be shorter than the Response Target.",
                               code="resolution_before_response")
    warn = parse_percentage(warning_percent, "Warning Alert")
    target = SLATarget.objects.for_organization(profile.organization).filter(profile=profile,
                                                                             priority=priority_upper).first()
    before = None
    if target is None:
        target = SLATarget(organization=profile.organization, profile=profile, priority=priority_upper)
    else:
        before = {"response": target.response_minutes, "resolution": target.resolution_minutes,
                  "warning": target.warning_percent}
    target.response_minutes, target.resolution_minutes, target.warning_percent = resp, reso, warn
    target.save()
    audit.record("sla.target_set", actor=actor, organization=profile.organization, target=profile, before=before,
                 after={"priority": priority_upper, "response": resp, "resolution": reso, "warning": warn}, request=request)
    return target


@transaction.atomic
def remove_target(target: SLATarget, *, actor, request=None) -> None:
    profile = target.profile
    info = {"priority": target.priority, "response": target.response_minutes, "resolution": target.resolution_minutes}
    target.delete()
    audit.record("sla.target_removed", actor=actor, organization=profile.organization, target=profile, before=info,
                 request=request)


@transaction.atomic
def create_rule(profile: SLAProfile, *, target_kind, trigger, notify_role=None, notify_assignee=False,
                after_minutes=0, level=1, actor, request=None) -> EscalationRule:
    profile = SLAProfile.objects.select_for_update().get(pk=profile.pk)
    if target_kind not in (RESPONSE, RESOLUTION):
        raise ValidationFailed("Choose response or resolution.", code="invalid_target_kind")
    if trigger not in EscalationRule.Trigger.values:
        raise ValidationFailed("Unknown trigger.", code="invalid_trigger")
    if notify_role is not None and notify_role.organization_id != profile.organization_id:
        raise ValidationFailed("Role not found in this organization.", code="cross_tenant_role")
    if notify_role is None and not notify_assignee:
        raise ValidationFailed("Choose who is notified (a role and / or the assignee).", code="recipient_required")
    after = 0
    if trigger == EscalationRule.Trigger.ESCALATION:
        after = _minutes(after_minutes, "Escalate after")
    try:
        level = int(level)
    except (TypeError, ValueError) as exc:
        raise ValidationFailed("The level must be 1, 2 or 3.", code="invalid_level") from exc
    if not 1 <= level <= 3:
        raise ValidationFailed("The level must be 1, 2 or 3.", code="invalid_level")
    if EscalationRule.objects.for_organization(profile.organization).filter(profile=profile).count() >= (
            MAX_RULES_PER_PROFILE):
        raise Conflict(f"A profile can have at most {MAX_RULES_PER_PROFILE} escalation rules.", code="too_many_rules")
    rule = EscalationRule(organization=profile.organization, profile=profile, target_kind=target_kind,
                          trigger=trigger, after_minutes=after, level=level, notify_role=notify_role,
                          notify_assignee=bool(notify_assignee))
    try:
        with transaction.atomic():
            rule.save()
    except IntegrityError as exc:
        raise Conflict("This profile already has a rule for that step.", code="duplicate_rule") from exc
    audit.record("sla.rule_created", actor=actor, organization=profile.organization, target=profile,
                 after={"target": target_kind, "trigger": trigger, "after_minutes": after, "level": level,
                        "role": notify_role.name if notify_role else None, "assignee": bool(notify_assignee)},
                 request=request)
    return rule


@transaction.atomic
def delete_rule(rule: EscalationRule, *, actor, request=None) -> None:
    profile = rule.profile
    info = {"target": rule.target_kind, "trigger": rule.trigger, "after_minutes": rule.after_minutes}
    rule.delete()
    audit.record("sla.rule_deleted", actor=actor, organization=profile.organization, target=profile, before=info,
                 request=request)


# --- tracking: start --------------------------------------------------------------------------------------------------


def _profile_for(org, applies_to, site, *, client=None, asset=None, service_type="", on=None):
    """Return all applicable policies from most specific to the organization default."""
    candidates = []
    for p in SLAProfile.objects.for_organization(org).filter(applies_to=applies_to, is_active=True,
                                                              coverage_only=False).select_related(
            "site", "client__user", "contract", "asset", "asset_category"):
        if p.client_id and (client is None or p.client_id != client.pk):
            continue
        if p.asset_id and (asset is None or p.asset_id != asset.pk):
            continue
        if p.asset_category_id and (asset is None or p.asset_category_id != asset.category_id):
            continue
        if p.site_id and p.site_id != site.pk:
            continue
        required_type = p.service_type or p.work_type
        if required_type and required_type != service_type:
            continue
        if p.contract_id:
            from apps.contracts.models import CoveredAsset
            agreement = p.contract
            if asset is None or not agreement.is_active or not (agreement.start_date <= (on or timezone.localdate())
                                                                 <= agreement.end_date) or not CoveredAsset.objects.for_organization(org).filter(
                    agreement=agreement, asset=asset).exists():
                continue
            if service_type and service_type in set(agreement.exclusions.values_list("work_type", flat=True)):
                continue
        candidates.append(p)
    if not candidates:
        return []
    candidates.sort(key=lambda x: (x.client_id is not None, x.contract_id is not None, x.asset_id is not None,
                                   x.asset_category_id is not None, x.site_id is not None,
                                   bool(x.service_type or x.work_type)), reverse=True)
    return [(p, ("Client-specific policy" if p.client_id else "Contract / AMC policy" if p.contract_id else
                 "Asset-specific policy" if p.asset_id else "Asset category policy" if p.asset_category_id else
                 "Site-specific policy" if p.site_id else "Service-type policy" if p.service_type or p.work_type else
                 "Organization default policy"),
             (p.client_id is not None, p.contract_id is not None, p.asset_id is not None,
              p.asset_category_id is not None, p.site_id is not None, bool(p.service_type or p.work_type)))
            for p in candidates]


def _event(tracking, event_type, *, at, kind="", detail="", rule=None, dedupe="", actor=None, notified=0):
    ev = SLAEvent(organization=tracking.organization, tracking=tracking, event_type=event_type, target_kind=kind,
                  at=at, detail=detail[:300], rule=rule, dedupe_key=dedupe, actor=actor, notified=notified)
    ev.save()
    return ev


def _coverage_profile(org, asset, applies_to, work_type, on) -> SLAProfile | None:
    """M10 -> M11: an agreement in force on ``on`` that covers ``asset`` (and does not exclude the work type) and names
    an internal SLA profile of the right kind brings that profile in; it beats the site / organization one."""
    if asset is None:
        return None
    from apps.contracts.models import CoveredAsset

    links = (CoveredAsset.objects.for_organization(org).filter(
        asset=asset, agreement__is_active=True, agreement__start_date__lte=on, agreement__end_date__gte=on,
        agreement__sla_profile__isnull=False, agreement__sla_profile__is_active=True,
        agreement__sla_profile__applies_to=applies_to)
        .select_related("agreement__sla_profile").prefetch_related("agreement__exclusions")
        .order_by("agreement__end_date", "agreement__reference"))
    for link in links:
        profile = link.agreement.sla_profile
        if work_type and work_type in {e.work_type for e in link.agreement.exclusions.all()}:
            continue
        if profile.service_type and profile.service_type != work_type:
            continue
        if profile.work_type and profile.work_type != work_type:  # preserve saved policies using the older field
            continue
        return profile
    return None


def _start(subject_kwargs, *, org, site, applies_to, priority, work_type, started_at,
           asset=None, client=None) -> SLATracking | None:
    profile, target, reason = None, None, ""
    covered = _coverage_profile(org, asset, applies_to, work_type, started_at.date())
    scoped = _profile_for(org, applies_to, site, client=client, asset=asset, service_type=work_type,
                          on=started_at.date())
    candidates = list(scoped)
    if covered is not None and all(covered.pk != p.pk for p, _, _ in candidates):
        candidates.append((covered, "Contract / AMC policy", (False, True, False, False, False, False)))
    candidates.sort(key=lambda candidate: candidate[2], reverse=True)
    for candidate, candidate_reason, _rank in candidates:
        if candidate is None:
            continue
        target = SLATarget.objects.for_organization(org).filter(profile=candidate, priority=priority).first()
        if target is not None:
            profile, reason = candidate, candidate_reason
            break
    if profile is None:
        return None
    tracking = SLATracking(
        organization=org, site=site, profile=profile, selection_reason=reason, priority=priority,
        response_minutes=target.response_minutes,
        resolution_minutes=target.resolution_minutes, warning_percent=target.warning_percent, started_at=started_at,
        response_due_at=started_at + timedelta(minutes=target.response_minutes),
        resolution_due_at=started_at + timedelta(minutes=target.resolution_minutes), **subject_kwargs)
    tracking.save()
    _event(tracking, "STARTED", at=started_at,
           detail=f"{profile.name}: response {target.response_minutes} min, resolution {target.resolution_minutes} min")
    audit.record("sla.tracking_started", organization=org, target=tracking,
                 after={"profile": profile.name, "priority": priority, "response_due": tracking.response_due_at,
                        "resolution_due": tracking.resolution_due_at})
    return tracking


def on_request_created(sr) -> SLATracking | None:
    """M05 hook: the SLA starts from the request's persisted ``created_at``."""
    return _start({"request": sr}, org=sr.organization, site=sr.site, applies_to=REQUEST, priority=sr.severity,
                  work_type=sr.kind, started_at=sr.created_at, asset=sr.asset, client=sr.reported_by)


def on_work_order_created(wo) -> SLATracking | None:
    """M06 hook: work orders that come from a request are covered by the REQUEST's SLA (no duplicate clock)."""
    if wo.source_request_id:
        return None
    return _start({"work_order": wo}, org=wo.organization, site=wo.site, applies_to=WORK_ORDER,
                  priority=wo.priority, work_type=wo.work_type, started_at=wo.created_at, asset=wo.asset,
                  client=wo.source_request.reported_by if wo.source_request_id else None)


# --- tracking: lifecycle hooks ----------------------------------------------------------------------------------------


def _locked(**subject) -> SLATracking | None:
    return SLATracking.objects.select_for_update(of=("self",)).select_related("profile", "request", "work_order").filter(
        **subject).first()


def _pause_wanted(t: SLATracking) -> bool:
    tokens = set(t.profile.pause_states or [])
    if not tokens:
        return False  # unconfigured: the timers keep running
    if t.request_id:
        if f"REQUEST:{t.request.status}" in tokens:
            return True
        from apps.workorders.models import WorkOrder

        live = WorkOrder.objects.for_organization(t.organization).filter(source_request=t.request_id).exclude(
            status__in=("CANCELLED", "CLOSED")).order_by("-created_at").first()
        return live is not None and f"WORK_ORDER:{live.status}" in tokens
    return f"WORK_ORDER:{t.work_order.status}" in tokens


def _pause_context(t: SLATracking):
    tokens = set(t.profile.pause_states or [])
    if t.request_id and f"REQUEST:{t.request.status}" in tokens:
        from apps.incidents.models import ServiceRequestHistory
        history = ServiceRequestHistory.objects.for_organization(t.organization).filter(
            request_id=t.request_id, to_status=t.request.status).order_by("-created_at").first()
        return f"REQUEST:{t.request.status}", (history.reason if history and history.reason else
                                                  f"Request status: {t.request.status.replace('_', ' ').title()}"), \
            history.changed_by if history else None
    from apps.workorders.models import WorkOrder
    wo = t.work_order
    if t.request_id:
        wo = WorkOrder.objects.for_organization(t.organization).filter(source_request=t.request_id).exclude(
            status__in=("CANCELLED", "CLOSED")).select_related("assigned_to__user").order_by("-created_at").first()
    if wo is not None and f"WORK_ORDER:{wo.status}" in tokens:
        return f"WORK_ORDER:{wo.status}", (wo.hold_reason or
                                            f"Work Order status: {wo.status.replace('_', ' ').title()}"), None
    return "", "", None


def _sync_pause(t: SLATracking, now: datetime, *, actor=None) -> None:
    if t.status not in (Status.ACTIVE, Status.AT_RISK, Status.BREACHED, Status.PAUSED):
        return
    workflow_state, reason, state_actor = _pause_context(t)
    want = bool(workflow_state)
    if want and t.status in (Status.ACTIVE, Status.AT_RISK, Status.BREACHED):
        t.status, t.paused_at = Status.PAUSED, now
        t.save(update_fields=["status", "paused_at", "updated_at"])
        SLAPauseEvent.objects.create(organization=t.organization, tracking=t, reason=reason[:300],
                                     workflow_state=workflow_state, started_at=now, paused_by=actor or state_actor)
        _event(t, "PAUSED", at=now, detail=f"{workflow_state}: {reason}"[:300], actor=actor or state_actor)
    elif not want and t.status == Status.PAUSED:
        delta = now - t.paused_at
        if t.response_met_at is None:
            t.response_due_at += delta
        if t.resolution_met_at is None:
            t.resolution_due_at += delta
        t.paused_seconds += int(delta.total_seconds())
        has_open_breach = SLABreach.objects.for_organization(t.organization).filter(
            tracking=t, status__in=(SLABreach.Status.OPEN, SLABreach.Status.ACKNOWLEDGED)).exists()
        t.status, t.paused_at = (Status.BREACHED if has_open_breach else Status.ACTIVE), None
        t.save(update_fields=["status", "paused_at", "paused_seconds", "response_due_at", "resolution_due_at",
                              "updated_at"])
        pause = t.pause_events.filter(ended_at__isnull=True).order_by("-started_at").first()
        if pause:
            pause.ended_at = now
            pause.duration_seconds = int(delta.total_seconds())
            pause.save(update_fields=["ended_at", "duration_seconds", "updated_at"])
        _event(t, "RESUMED", at=now, detail=f"Timers resumed after {int(delta.total_seconds())} s")


def _record_breach(t: SLATracking, kind: str, due: datetime, now: datetime) -> tuple[SLABreach, bool]:
    breach = SLABreach.objects.for_organization(t.organization).filter(tracking=t, target_kind=kind).first()
    if breach is not None:
        return breach, False
    breach = SLABreach(organization=t.organization, tracking=t, profile=t.profile, site=t.site, target_kind=kind,
                       priority=t.priority, severity=SEVERITY.get(t.priority, "MAJOR"), breached_at=due,
                       detected_at=now)
    breach.save()
    return breach, True


def _finish_open_pause(t: SLATracking, now: datetime) -> None:
    pause = t.pause_events.filter(ended_at__isnull=True).order_by("-started_at").first()
    if pause is None:
        return
    pause.ended_at = now
    pause.duration_seconds = max(0, int((now - pause.started_at).total_seconds()))
    pause.save(update_fields=["ended_at", "duration_seconds", "updated_at"])
    t.paused_seconds += pause.duration_seconds
    t.paused_at = None


def _meet(t: SLATracking, kind: str, now: datetime, *, actor=None) -> None:
    due = t.response_due_at if kind == RESPONSE else t.resolution_due_at
    late = now > due
    if late:  # the target was missed: the breach is recorded even if the monitor never saw it, then closed
        breach, created = _record_breach(t, kind, due, now)
        if created:
            _event(t, "BREACHED", at=due, kind=kind, detail="Detected when the target was met late",
                   dedupe=f"breach:{kind}")
        breach.status, breach.closed_at, breach.closed_reason = SLABreach.Status.CLOSED, now, "target met late"
        breach.save(update_fields=["status", "closed_at", "closed_reason", "updated_at"])
    else:
        SLABreach.objects.for_organization(t.organization).filter(tracking=t, target_kind=kind).update(
            status=SLABreach.Status.CLOSED, closed_at=now, closed_reason="target met")
    state = TS.MET_LATE if late else TS.MET
    if kind == RESPONSE:
        t.response_met_at, t.response_state = now, state
    else:
        t.resolution_met_at, t.resolution_state = now, state
        _finish_open_pause(t, now)
        t.status = Status.UNDER_REVIEW
    t.save()
    _event(t, f"{kind}_MET", at=now, kind=kind, detail="late" if late else "on time", actor=actor)
    audit.record("sla.response_met" if kind == RESPONSE else "sla.resolution_met", actor=actor,
                 organization=t.organization, target=t, after={"late": late, "at": now})


def _reopen(t: SLATracking, now: datetime) -> None:
    """The resolved subject went back to work (reopen / rework): the resolution timer runs again."""
    if t.resolution_met_at is None and t.status != Status.COMPLETED:
        return
    t.resolution_met_at, t.status = None, Status.ACTIVE
    t.resolution_state = TS.BREACHED if SLABreach.objects.for_organization(t.organization).filter(
        tracking=t, target_kind=RESOLUTION).exists() else TS.PENDING
    t.save()
    SLABreach.objects.for_organization(t.organization).filter(tracking=t, target_kind=RESOLUTION).update(
        status=SLABreach.Status.OPEN, closed_at=None, closed_reason="")
    _event(t, "REOPENED", at=now, kind=RESOLUTION, detail="Work resumed after resolution")


def _cancel(t: SLATracking, now: datetime, why: str) -> None:
    if t.status in (Status.COMPLETED, Status.CLOSED, Status.CANCELLED):
        return
    _finish_open_pause(t, now)
    t.status, t.paused_at = Status.CANCELLED, None
    if t.response_met_at is None:
        t.response_state = TS.NOT_APPLICABLE if t.response_state == TS.PENDING else t.response_state
    if t.resolution_met_at is None and t.resolution_state == TS.PENDING:
        t.resolution_state = TS.NOT_APPLICABLE
    t.save()
    SLABreach.objects.for_organization(t.organization).filter(
        tracking=t, status__in=("OPEN", "ACKNOWLEDGED")).update(
        status=SLABreach.Status.CLOSED, closed_at=now, closed_reason=why[:60])
    _event(t, "CANCELLED", at=now, detail=why)


@transaction.atomic
def on_request_status_changed(sr, previous: str, new: str, *, actor=None) -> None:
    """M05 hook after every request transition (the request row is already updated and locked by the caller)."""
    t = _locked(request=sr)
    if t is None:
        return
    now = timezone.now()
    if actor and f"REQUEST:{new}" in set(t.profile.pause_states or []):
        from apps.incidents.models import ServiceRequestHistory
        last = ServiceRequestHistory.objects.for_organization(t.organization).filter(
            request=sr, to_status=new).order_by("-created_at").first()
        if last and last.source == "manual" and len((last.reason or "").strip()) < 3:
            raise ValidationFailed("Add a Pause Reason before moving this item into a configured pause state.",
                                   code="pause_reason_required")
    _sync_pause(t, now, actor=actor)  # first: a resume shifts due times before judging a target
    if previous == "NEW" and new != "NEW" and t.response_met_at is None and t.status != Status.CANCELLED:
        _meet(t, RESPONSE, now)  # the first backend transition out of NEW (triage) is the response
    if new == "REJECTED":
        _cancel(t, now, "request rejected")
    elif new in ("RESOLVED", "CONFIRMED", "CLOSED"):
        if t.resolution_met_at is None and t.status in (Status.ACTIVE, Status.AT_RISK, Status.BREACHED, Status.PAUSED):
            _meet(t, RESOLUTION, now)
        if new == "CONFIRMED":
            _event(t, "CLIENT_CONFIRMED", at=now, detail="Client confirmed the reported resolution", actor=actor)
        if new == "CLOSED":
            t.status = Status.CLOSED
            t.save(update_fields=["status", "updated_at"])
            _event(t, "CLOSED", at=now, detail="Incident / Breakdown closed", actor=actor)
    elif previous in ("RESOLVED", "CONFIRMED") and new in ("APPROVED", "IN_SERVICE"):
        _reopen(t, now)


@transaction.atomic
def on_request_updated(sr, *, actor=None, request=None) -> None:
    """M05 hook: a changed severity re-targets a tracking whose targets are still open (before approval)."""
    t = _locked(request=sr)
    if t is None or t.priority == sr.severity or t.status in (Status.COMPLETED, Status.UNDER_REVIEW,
                                                              Status.CLOSED, Status.CANCELLED):
        return
    target = SLATarget.objects.for_organization(t.organization).filter(profile=t.profile,
                                                                       priority=sr.severity).first()
    if target is None:
        return
    before = {"priority": t.priority, "response_minutes": t.response_minutes,
              "resolution_minutes": t.resolution_minutes, "response_due_at": t.response_due_at,
              "resolution_due_at": t.resolution_due_at}
    shift = timedelta(seconds=t.paused_seconds)
    t.priority, t.response_minutes, t.resolution_minutes = sr.severity, target.response_minutes, \
        target.resolution_minutes
    t.warning_percent = target.warning_percent
    if t.response_met_at is None:
        t.response_due_at = t.started_at + timedelta(minutes=target.response_minutes) + shift
    if t.resolution_met_at is None:
        t.resolution_due_at = t.started_at + timedelta(minutes=target.resolution_minutes) + shift
    t.save()
    _event(t, "RETARGETED", at=timezone.now(), detail=f"Priority changed from {before['priority']} to {sr.severity}",
           actor=actor)
    audit.record("sla.priority_retargeted", actor=actor, organization=t.organization, target=t, before=before,
                 after={"priority": t.priority, "response_minutes": t.response_minutes,
                        "resolution_minutes": t.resolution_minutes, "response_due_at": t.response_due_at,
                        "resolution_due_at": t.resolution_due_at}, request=request)


@transaction.atomic
def on_work_order_updated(wo, previous_priority: str | None, *, actor=None, request=None) -> None:
    """Recalculate outstanding Work Order targets after a permitted planning priority change."""
    if wo.source_request_id or previous_priority == wo.priority or not wo.priority:
        return
    t = _locked(work_order=wo)
    if t is None or t.status in (Status.COMPLETED, Status.UNDER_REVIEW, Status.CLOSED, Status.CANCELLED):
        return
    target = SLATarget.objects.for_organization(t.organization).filter(profile=t.profile,
                                                                       priority=wo.priority).first()
    if target is None:
        return
    before = {"priority": t.priority, "response_minutes": t.response_minutes,
              "resolution_minutes": t.resolution_minutes, "response_due_at": t.response_due_at,
              "resolution_due_at": t.resolution_due_at}
    shift = timedelta(seconds=t.paused_seconds)
    t.priority, t.response_minutes, t.resolution_minutes = wo.priority, target.response_minutes, \
        target.resolution_minutes
    t.warning_percent = target.warning_percent
    if t.response_met_at is None:
        t.response_due_at = t.started_at + timedelta(minutes=target.response_minutes) + shift
    if t.resolution_met_at is None:
        t.resolution_due_at = t.started_at + timedelta(minutes=target.resolution_minutes) + shift
    t.save()
    _event(t, "RETARGETED", at=timezone.now(), detail=f"Priority changed from {previous_priority} to {wo.priority}",
           actor=actor)
    audit.record("sla.priority_retargeted", actor=actor, organization=t.organization, target=t, before=before,
                 after={"priority": t.priority, "response_minutes": t.response_minutes,
                        "resolution_minutes": t.resolution_minutes, "response_due_at": t.response_due_at,
                        "resolution_due_at": t.resolution_due_at}, request=request)


@transaction.atomic
def on_work_order_changed(wo, previous: str, new: str, *, actor=None) -> None:
    """M06 hook after every work-order transition."""
    now = timezone.now()
    if wo.source_request_id:  # the request's SLA only reacts to pause states of its live work order
        t = _locked(request_id=wo.source_request_id)
        if t is not None:
            if actor and f"WORK_ORDER:{new}" in set(t.profile.pause_states or []):
                from apps.workorders.models import WorkOrderEvent
                last = WorkOrderEvent.objects.for_organization(t.organization).filter(
                    work_order=wo, to_status=new).order_by("-created_at").first()
                if last and len((last.reason or wo.hold_reason or "").strip()) < 3:
                    raise ValidationFailed("Add a Pause Reason before moving this item into a configured pause state.",
                                           code="pause_reason_required")
            _sync_pause(t, now, actor=actor)
            if new == "SUPERVISOR_REVIEW" and t.resolution_met_at is not None:
                t.status = Status.UNDER_REVIEW
                t.save(update_fields=["status", "updated_at"])
                _event(t, "SUPERVISOR_REVIEW", at=now,
                       detail="Work Order submitted for Supervisor Review", actor=actor)
            elif new == "CLOSED" and t.status != Status.CANCELLED:
                t.status = Status.CLOSED
                t.save(update_fields=["status", "updated_at"])
                _event(t, "CLOSED", at=now, detail="Work Order closed after Supervisor Review", actor=actor)
            elif previous == "SUPERVISOR_REVIEW" and new == "IN_PROGRESS":
                _reopen(t, now)
        return
    t = _locked(work_order=wo)
    if t is None:
        return
    if actor and f"WORK_ORDER:{new}" in set(t.profile.pause_states or []):
        from apps.workorders.models import WorkOrderEvent
        last = WorkOrderEvent.objects.for_organization(t.organization).filter(
            work_order=wo, to_status=new).order_by("-created_at").first()
        if last and len((last.reason or wo.hold_reason or "").strip()) < 3:
            raise ValidationFailed("Add a Pause Reason before moving this Work Order into a configured pause state.",
                                   code="pause_reason_required")
    _sync_pause(t, now, actor=actor)  # first: a resume shifts due times before judging a target
    if new in ("ASSIGNED", "DISPATCHED", "IN_PROGRESS") and t.response_met_at is None and t.status in (
            Status.ACTIVE, Status.AT_RISK, Status.BREACHED, Status.PAUSED):
        _meet(t, RESPONSE, now)  # work orders: the first assignment is the response (D-044)
    if new == "CANCELLED":
        _cancel(t, now, "work order cancelled")
    elif new == "CLOSED":
        t.status = Status.CLOSED
        t.save(update_fields=["status", "updated_at"])
        _event(t, "CLOSED", at=now, detail="Work Order closed after Supervisor Review", actor=actor)
    elif new in ("COMPLETED", "SUPERVISOR_REVIEW", "CLOSED"):
        if t.resolution_met_at is None and t.status in (Status.ACTIVE, Status.AT_RISK, Status.BREACHED, Status.PAUSED):
            if t.response_met_at is None:
                _meet(t, RESPONSE, now)
            _meet(t, RESOLUTION, now)
    elif previous in ("COMPLETED", "SUPERVISOR_REVIEW") and new == "IN_PROGRESS":
        _reopen(t, now)


# --- the monitor ------------------------------------------------------------------------------------------------------


def _recipients(rule: EscalationRule, t: SLATracking) -> list:
    from apps.rbac.models import MembershipRole
    from apps.tenancy.models import Membership

    users = {}
    if rule.notify_role_id:
        rows = MembershipRole.objects.filter(
            role=rule.notify_role, membership__organization=t.organization,
            membership__status=Membership.Status.ACTIVE).filter(
            models_q_site(t.site_id)).select_related("membership__user")
        for r in rows:
            users[r.membership.user_id] = r.membership.user
    if rule.notify_assignee:
        wo = t.work_order
        if wo is None and t.request_id:
            from apps.workorders.models import WorkOrder

            wo = WorkOrder.objects.for_organization(t.organization).filter(source_request=t.request_id).exclude(
                status__in=("CANCELLED", "CLOSED")).select_related("assigned_to__user").order_by(
                "-created_at").first()
        if wo is not None and wo.assigned_to_id and wo.assigned_to.is_active:
            users[wo.assigned_to.user_id] = wo.assigned_to.user
    return list(users.values())


def models_q_site(site_id):
    from django.db.models import Q

    return Q(site__isnull=True) | Q(site_id=site_id)


def _subject_link(t: SLATracking) -> tuple[str, str]:
    if t.request_id:
        return t.request.number, f"/app/incidents/{t.request_id}/"
    return t.work_order.number, f"/app/work-orders/{t.work_order_id}/"


def _notify(t: SLATracking, rules, *, title: str, body: str, level: str) -> int:
    from apps.notifications import services as notifications
    from apps.notifications.models import Notification

    number, link = _subject_link(t)
    users = {}
    for rule in rules:
        for u in _recipients(rule, t):
            users[u.pk] = u
    if not users:
        return 0
    # breaches and escalations are also emailed (Celery); early warnings stay in-app to avoid mailbox noise
    return len(notifications.notify(t.organization, list(users.values()), title=f"{number}: {title}"[:160],
                                    body=body, link=link, level=level, source="sla",
                                    email=level == Notification.Level.CRITICAL))


def _rules(t: SLATracking, kind: str, trigger: str):
    return list(EscalationRule.objects.for_organization(t.organization).filter(
        profile=t.profile, target_kind=kind, trigger=trigger).select_related("notify_role").order_by("after_minutes"))


def _fire(t: SLATracking, event_type: str, kind: str, now: datetime, *, dedupe: str, detail: str, rules,
          title: str, level: str, rule=None, result: dict | None = None) -> bool:
    """Persists one SLA event exactly once (unique dedupe key) and, only then, sends its notifications."""
    try:
        with transaction.atomic():
            ev = _event(t, event_type, at=now, kind=kind, detail=detail, rule=rule, dedupe=dedupe)
    except IntegrityError:
        return False  # already fired by an earlier run / another worker
    sent = _notify(t, rules, title=title, body=detail, level=level)
    if sent:
        SLAEvent.objects.filter(pk=ev.pk).update(notified=sent)  # bookkeeping on the append-only row
    if result is not None:
        result["notifications"] = result.get("notifications", 0) + sent
        action_name = "Warning Alert" if event_type == "WARNING" else ("SLA Breach" if event_type == "BREACHED" else ("Escalation" if event_type == "ESCALATED" else event_type))
        target_name = "Response Target" if kind == RESPONSE else "Resolution Target"
        recip_note = f"{sent} notification(s) dispatched" if sent else "Logged to dashboard"
        result.setdefault("actions", []).append(f"{action_name} for {target_name} ({recip_note})")
    return True


def _check_target(t: SLATracking, kind: str, now: datetime, result: dict) -> None:
    from apps.notifications.models import Notification

    met = t.response_met_at if kind == RESPONSE else t.resolution_met_at
    if met is not None:
        return
    due = t.response_due_at if kind == RESPONSE else t.resolution_due_at
    minutes = t.response_minutes if kind == RESPONSE else t.resolution_minutes
    label = "Response Target" if kind == RESPONSE else "Resolution Target"
    warn_at = due - timedelta(seconds=minutes * 60 * (100 - t.warning_percent) / 100)
    if warn_at <= now < due:
        pct = t.warning_percent
        if _fire(t, "WARNING", kind, now, dedupe=f"warning:{kind}", rules=_rules(t, kind, "WARNING"),
                 title=f"{label} SLA warning", level=Notification.Level.WARNING,
                 detail=f"{label} target is {pct}% elapsed; due {due.isoformat(timespec='minutes')}.",
                 result=result):
            result["warnings"] += 1
        if t.status == Status.ACTIVE:
            t.status = Status.AT_RISK
            t.save(update_fields=["status", "updated_at"])
            _event(t, "AT_RISK", at=now, kind=kind, detail=f"{label} target reached its warning threshold")
    if now < due:
        return
    breach, created = _record_breach(t, kind, due, now)
    if created:
        if kind == RESPONSE:
            t.response_state = TS.BREACHED
        else:
            t.resolution_state = TS.BREACHED
        t.save(update_fields=["response_state", "resolution_state", "updated_at"])
        _fire(t, "BREACHED", kind, now, dedupe=f"breach:{kind}", rules=_rules(t, kind, "BREACH"),
              title=f"{label} SLA breached", level=Notification.Level.CRITICAL,
              detail=f"{label} target was due {due.isoformat(timespec='minutes')} and is still unmet.",
              result=result)
        breach.notified_at = now
        breach.save(update_fields=["notified_at", "updated_at"])
        audit.record("sla.breach_detected", organization=t.organization, target=breach,
                     after={"target": kind, "priority": t.priority, "due": due, "tracking": str(t.pk)})
        result["breaches"] += 1
    if breach.status in (SLABreach.Status.OPEN, SLABreach.Status.ACKNOWLEDGED) and t.status != Status.BREACHED:
        t.status = Status.BREACHED
        t.save(update_fields=["status", "updated_at"])
    for rule in _rules(t, kind, "ESCALATION"):
        if now < due + timedelta(minutes=rule.after_minutes):
            continue
        if _fire(t, "ESCALATED", kind, now, dedupe=f"escalation:{rule.pk}", rules=[rule], rule=rule,
                 title=f"{label} SLA escalation (level {rule.level})", level=Notification.Level.CRITICAL,
                 detail=f"{label} target still unmet {rule.after_minutes} min after the breach.",
                 result=result):
            breach.escalation_level = max(breach.escalation_level, rule.level)
            breach.escalated_at = now
            breach.save(update_fields=["escalation_level", "escalated_at", "updated_at"])
            audit.record("sla.escalated", organization=t.organization, target=breach,
                         after={"level": rule.level, "rule": str(rule.pk), "target": kind})
            result["escalations"] += 1


@transaction.atomic
def process_tracking(tracking: SLATracking, now: datetime | None = None) -> dict:
    """Evaluates one tracking at ``now``: warning -> breach -> escalations. Safe to repeat."""
    now = now or timezone.now()
    result = {"warnings": 0, "breaches": 0, "escalations": 0, "notifications": 0, "actions": []}
    t = SLATracking.objects.select_for_update(of=("self",)).select_related(
        "profile", "request", "work_order", "site").get(pk=tracking.pk)
    t.last_checked_at = now
    if t.status in (Status.ACTIVE, Status.AT_RISK, Status.BREACHED):  # paused/finished trackings are not evaluated
        for kind in (RESPONSE, RESOLUTION):
            _check_target(t, kind, now, result)
        pending_due = []
        for kind in (RESPONSE, RESOLUTION):
            met = t.response_met_at if kind == RESPONSE else t.resolution_met_at
            due = t.response_due_at if kind == RESPONSE else t.resolution_due_at
            minutes = t.response_minutes if kind == RESPONSE else t.resolution_minutes
            if met is None and now < due:
                warn_at = due - timedelta(seconds=minutes * 60 * (100 - t.warning_percent) / 100)
                pending_due.append(warn_at <= now)
        if t.status == Status.AT_RISK and not any(pending_due):
            t.status = Status.ACTIVE
            t.save(update_fields=["status", "updated_at"])
            _event(t, "RISK_CLEARED", at=now, detail="No unmet target is currently at risk")
    t.save(update_fields=["last_checked_at", "updated_at"])

    # If no newly fired actions occurred in this run, record the item's operational standing
    if not result["actions"]:
        if t.status == Status.BREACHED:
            result["actions"].append("SLA Breach active (awaiting resolution / escalation)")
        elif t.status == Status.AT_RISK:
            result["actions"].append("At Risk (within Warning Alert threshold)")
        else:
            result["actions"].append("On Track (running within targets)")

    return result


def process_organization(org, now: datetime | None = None) -> dict:
    """Monitor unit of work for one tenant (each tracking in its own transaction; a failing one is logged and does
    not stop the others)."""
    from apps.core.tenant import tenant_context

    now = now or timezone.now()
    total = {
        "checked": 0,
        "warnings": 0,
        "breaches": 0,
        "escalations": 0,
        "notifications": 0,
        "errors": 0,
        "items": [],
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    with tenant_context(org):
        trackings_qs = SLATracking.objects.for_organization(org).filter(
            status__in=(Status.ACTIVE, Status.AT_RISK, Status.BREACHED)
        ).select_related("request", "work_order", "site", "profile")
        for t_obj in list(trackings_qs):
            try:
                res = process_tracking(t_obj, now)
            except Exception:  # noqa: BLE001
                total["errors"] += 1
                log.exception("SLA processing failed for tracking %s", t_obj.pk)
                continue
            total["checked"] += 1
            for key in ("warnings", "breaches", "escalations", "notifications"):
                total[key] += res.get(key, 0)

            t_refreshed = SLATracking.objects.filter(pk=t_obj.pk).first() or t_obj
            num = t_obj.request.number if t_obj.request else (t_obj.work_order.number if t_obj.work_order else "Item")
            title = t_obj.request.title if t_obj.request else (t_obj.work_order.title if t_obj.work_order else "")
            link = f"/app/incidents/{t_obj.request_id}/" if t_obj.request_id else f"/app/work-orders/{t_obj.work_order_id}/"
            item_type = "Incident / Breakdown" if t_obj.request else "Work Order"
            total["items"].append({
                "id": str(t_obj.pk),
                "number": num,
                "title": title,
                "type": item_type,
                "link": link,
                "site": t_obj.site.code if t_obj.site else "Organization",
                "priority": t_obj.priority,
                "profile": t_obj.profile.name if t_obj.profile else "Default",
                "status": t_refreshed.status,
                "warnings": res.get("warnings", 0),
                "breaches": res.get("breaches", 0),
                "escalations": res.get("escalations", 0),
                "notifications": res.get("notifications", 0),
                "actions": res.get("actions", []),
            })
    return total


# --- breaches ------------------------------------------------------------------------------------------------------------


@transaction.atomic
def acknowledge_breach(breach: SLABreach, *, actor, request=None) -> SLABreach:
    breach = SLABreach.objects.select_for_update().get(pk=breach.pk)
    if breach.status != SLABreach.Status.OPEN:
        raise Conflict("Only an open breach can be acknowledged.", code="breach_not_open",
                       details={"status": breach.status})
    breach.status, breach.acknowledged_by, breach.acknowledged_at = SLABreach.Status.ACKNOWLEDGED, actor, \
        timezone.now()
    breach.save()
    _event(breach.tracking, "ACKNOWLEDGED", at=breach.acknowledged_at, kind=breach.target_kind,
           detail=f"Acknowledged by {getattr(actor, 'email', '')}", actor=actor)
    audit.record("sla.breach_acknowledged", actor=actor, organization=breach.organization, target=breach,
                 after={"status": breach.status}, request=request)
    return breach
