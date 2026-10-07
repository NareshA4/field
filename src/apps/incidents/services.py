"""M05 service layer (incident / service-request workflow, evidence, downtime).

Callers (API/UI) resolve objects through organization- and site-scoped selectors and check permissions with the
target site first; services enforce integrity (tenant ownership of every reference, lifecycle rules, append-only
history, one audit record per change) inside one transaction.

Boundary with M06: M05 may CREATE a work order from an approved request (``create_work_order_for_request``) but
never runs the work-order lifecycle. M06 reports back through the ``on_work_order_*`` hooks below, which are the
only way the SYSTEM actions of the request workflow are applied.
"""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import Conflict, ValidationFailed
from apps.core.sequences import next_number
from apps.files import services as files

from .models import Downtime, ServiceRequest, ServiceRequestHistory
from .workflow import (
    ACTION_PERMISSIONS,
    APPROVED,
    CLOSED,
    IN_SERVICE,
    NEW,
    REASON_REQUIRED,
    REJECTED,
    REQUEST_STATUS,
    RESOLVED,
    TRIAGED,
    WORK_ORDER_CREATED,
)

SNAPSHOT = ["title", "description", "severity", "kind", "service_impact", "impact_notes", "occurred_at"]
_EDITABLE = {"title", "description", "severity", "service_impact", "impact_notes", "occurred_at"}
_SEVERITY_TO_PRIORITY = {"LOW": "LOW", "MEDIUM": "MEDIUM", "HIGH": "HIGH", "CRITICAL": "URGENT"}
_FUTURE_SLACK = timedelta(minutes=5)


def _sla():
    """M11 contract (lazy import: M11 depends on the M05 models)."""
    from apps.sla import services as sla

    return sla


def _clean_title(title: str) -> str:
    title = (title or "").strip()
    if len(title) < 3:
        raise ValidationFailed("A title of at least 3 characters is required.", code="title_required")
    return title


def _check_time(value, what: str):
    if value is not None and value > timezone.now() + _FUTURE_SLACK:
        raise ValidationFailed(f"{what} cannot be in the future.", code="future_time")


def _history(sr, *, from_status, to_status, action, reason, actor, source):
    ServiceRequestHistory(organization=sr.organization, request=sr, from_status=from_status, to_status=to_status,
                          action=action, reason=(reason or "")[:500], changed_by=actor, source=source).save()


# --- create / edit -------------------------------------------------------------------------------------------


@transaction.atomic
def create_request(org, *, asset, reporter, title: str, description: str = "", kind: str = "INCIDENT",
                   severity: str = "MEDIUM", service_impact: str = "NONE", impact_notes: str = "",
                   occurred_at=None, downtime_started_at=None, actor, request=None) -> ServiceRequest:
    """Reports an incident / service request against an asset. ``reporter`` is the actor's Membership."""
    if asset.organization_id != org.pk:
        raise ValidationFailed("Asset belongs to a different organization.", code="cross_tenant_asset")
    if asset.status in ("RETIRED", "DISPOSED"):
        raise Conflict("Retired or disposed assets cannot have new requests.", code="asset_terminal")
    if reporter.organization_id != org.pk:
        raise ValidationFailed("Reporter belongs to a different organization.", code="cross_tenant_reporter")
    if kind not in ServiceRequest.Kind.values:
        raise ValidationFailed("Unknown request type.", code="invalid_kind")
    if severity not in ServiceRequest.Severity.values:
        raise ValidationFailed("Unknown severity.", code="invalid_severity")
    if service_impact not in ServiceRequest.Impact.values:
        raise ValidationFailed("Unknown service impact.", code="invalid_impact")
    occurred_at = occurred_at or timezone.now()
    _check_time(occurred_at, "The time of the failure")
    _check_time(downtime_started_at, "The downtime start")
    prefix = "INC" if kind == "INCIDENT" else "SR"
    sr = ServiceRequest(
        organization=org, number=next_number(org, f"request.{kind.lower()}", prefix), kind=kind,
        title=_clean_title(title), description=(description or "").strip(), severity=severity,
        service_impact=service_impact, impact_notes=(impact_notes or "").strip()[:500], asset=asset,
        site=asset.site, status=NEW, reported_by=reporter, occurred_at=occurred_at)
    sr.save()
    _history(sr, from_status="", to_status=NEW, action="report", reason="", actor=actor, source="manual")
    audit.record("incident.created", actor=actor, organization=org, target=sr,
                 after={**audit.snapshot(sr, SNAPSHOT), "asset": asset.asset_tag, "site": asset.site.code,
                        "number": sr.number}, request=request)
    if downtime_started_at is not None:
        _write_downtime(sr, started_at=downtime_started_at, ended_at=None, actor=actor, request=request)
    _sla().on_request_created(sr)  # M11: the SLA starts from the persisted created_at
    return sr


@transaction.atomic
def update_request(sr: ServiceRequest, *, actor, request=None, **changes) -> ServiceRequest:
    sr = ServiceRequest.objects.select_for_update().get(pk=sr.pk)
    if sr.status not in (NEW, TRIAGED):
        raise Conflict("Details can only be edited before the request is approved.", code="request_locked")
    unknown = set(changes) - _EDITABLE
    if unknown:
        raise ValidationFailed(f"Fields cannot be edited: {', '.join(sorted(unknown))}.", code="field_not_editable")
    before = audit.snapshot(sr, SNAPSHOT)
    if "title" in changes:
        sr.title = _clean_title(changes["title"])
    if "description" in changes:
        sr.description = (changes["description"] or "").strip()
    if "severity" in changes:
        if changes["severity"] not in ServiceRequest.Severity.values:
            raise ValidationFailed("Unknown severity.", code="invalid_severity")
        sr.severity = changes["severity"]
    if "service_impact" in changes:
        if changes["service_impact"] not in ServiceRequest.Impact.values:
            raise ValidationFailed("Unknown service impact.", code="invalid_impact")
        sr.service_impact = changes["service_impact"]
    if "impact_notes" in changes:
        sr.impact_notes = (changes["impact_notes"] or "").strip()[:500]
    if "occurred_at" in changes:
        _check_time(changes["occurred_at"], "The time of the failure")
        sr.occurred_at = changes["occurred_at"]
    sr.save()
    after = audit.snapshot(sr, SNAPSHOT)
    if after != before and before.get("severity") != after.get("severity"):
        _sla().on_request_updated(sr)  # M11: a new severity re-targets the open SLA
    if after != before:
        audit.record("incident.updated", actor=actor, organization=sr.organization, target=sr, before=before,
                     after=after, request=request)
    return sr


# --- workflow ------------------------------------------------------------------------------------------------


def _apply(sr: ServiceRequest, action: str, *, reason: str, actor, source: str, request=None) -> ServiceRequest:
    sr = ServiceRequest.objects.select_for_update().get(pk=sr.pk)
    previous, new = REQUEST_STATUS.apply(sr, action)
    now = timezone.now()
    if action == "triage":
        sr.triaged_at = now
    elif action in ("approve", "reject"):
        sr.decided_at, sr.decision_reason = now, (reason or "")[:500]
    elif action == "resolve":
        sr.resolved_at = now
    elif action in ("confirm",):
        sr.confirmed_at = now
    elif action == "close":
        sr.closed_at = now
    elif action == "reopen":
        sr.resolved_at = sr.confirmed_at = None
        sr.decision_reason = (reason or "")[:500]
    sr.save()
    _history(sr, from_status=previous, to_status=new, action=action, reason=reason, actor=actor, source=source)
    audit.record("incident.status_changed", actor=actor, organization=sr.organization, target=sr,
                 before={"status": previous}, after={"status": new},
                 metadata={"action": action, "reason": reason or "", "source": source}, request=request)
    _sla().on_request_status_changed(sr, previous, new)  # M11 hook (never edits the request)
    return sr


@transaction.atomic
def transition(sr: ServiceRequest, *, action: str, reason: str = "", actor, request=None) -> ServiceRequest:
    """User-facing transitions (triage / approve / reject / confirm / reopen / close). The caller has verified the
    permission in ACTION_PERMISSIONS for the request's site; system actions are refused here."""
    if action not in ACTION_PERMISSIONS:
        raise ValidationFailed("This action is performed by the work order, not by hand.", code="system_action")
    reason = (reason or "").strip()
    if action in REASON_REQUIRED and len(reason) < 3:
        raise ValidationFailed("A reason is required.", code="reason_required")
    sr = _apply(sr, action, reason=reason, actor=actor, source="manual", request=request)
    _notify_reporter(sr, action, reason)
    return sr


def _notify_reporter(sr: ServiceRequest, action: str, reason: str):
    texts = {"approve": "was approved", "reject": f"was rejected: {reason}", "reopen": "was reopened for rework",
             "resolve": "has been resolved - please confirm", "triage": "was triaged"}
    if action not in texts:
        return
    from apps.notifications import services as notifications

    notifications.notify(sr.organization, [sr.reported_by.user], title=f"{sr.number} {texts[action]}",
                         body=sr.title, link=f"/app/incidents/{sr.pk}/", source="incidents")


@transaction.atomic
def create_work_order_for_request(sr: ServiceRequest, *, actor, membership, request=None, **plan):
    """M05 -> M06: an APPROVED request creates a real work order (DRAFT) and moves to WORK ORDER CREATED. The
    caller holds ``work_order.create`` for the request's site. ``plan`` may carry title / description / priority /
    planned_start / planned_end / estimated_hours. The work-order lifecycle is M06's."""
    from apps.workorders import services as work_orders

    sr = ServiceRequest.objects.select_related("asset", "site").select_for_update(of=("self",)).get(pk=sr.pk)
    if sr.status != APPROVED:
        REQUEST_STATUS.get(sr.status, "link_work_order")  # raises invalid_transition with the usual envelope
    wo = work_orders.create_work_order(
        sr.organization, asset=sr.asset, actor=actor, request=request, source_request=sr,
        work_type="CORRECTIVE", title=plan.pop("title", None) or f"{sr.number}: {sr.title}",
        description=plan.pop("description", None) or sr.description,
        priority=plan.pop("priority", None) or _SEVERITY_TO_PRIORITY[sr.severity], **plan)
    _apply(sr, "link_work_order", reason=wo.number, actor=actor, source="manual", request=request)
    return wo


# --- hooks called by the work-order lifecycle (M06) --------------------------------------------------------------------


def on_work_order_started(sr: ServiceRequest, *, actor, request=None):
    if sr.status == WORK_ORDER_CREATED:
        _apply(sr, "start_service", reason="Work started", actor=actor, source="work_order", request=request)


def on_work_order_completed(sr: ServiceRequest, *, actor, request=None):
    if sr.status == IN_SERVICE:
        sr = _apply(sr, "resolve", reason="Work completed", actor=actor, source="work_order", request=request)
        _end_open_downtime(sr, actor=actor, source="work_order", request=request)
        _notify_reporter(sr, "resolve", "")


def on_work_order_reworked(sr: ServiceRequest, *, actor, request=None):
    if sr.status == RESOLVED:
        _apply(sr, "resume_service", reason="Returned for rework", actor=actor, source="work_order", request=request)


def on_work_order_cancelled(sr: ServiceRequest, *, actor, request=None):
    if sr.status == WORK_ORDER_CREATED:
        _apply(sr, "work_order_cancelled", reason="Work order cancelled", actor=actor, source="work_order",
               request=request)


# --- downtime ------------------------------------------------------------------------------------------------


def _write_downtime(sr, *, started_at, ended_at, actor, request, source="manual") -> Downtime:
    if started_at is None:
        raise ValidationFailed("The downtime start is required.", code="downtime_start_required")
    _check_time(started_at, "The downtime start")
    _check_time(ended_at, "The downtime end")
    if ended_at is not None and ended_at < started_at:
        raise ValidationFailed("The downtime end cannot be before its start.", code="downtime_end_before_start")
    dt = Downtime.objects.for_organization(sr.organization).filter(request=sr).first()
    fields = ["started_at", "ended_at"]
    if dt is None:
        dt = Downtime(organization=sr.organization, request=sr, asset=sr.asset, started_at=started_at,
                      ended_at=ended_at, recorded_by=actor, ended_by=actor if ended_at else None,
                      end_source=source if ended_at else "")
        dt.save()
        audit.record("incident.downtime_recorded", actor=actor, organization=sr.organization, target=sr,
                     after=audit.snapshot(dt, fields), request=request)
    else:
        before = audit.snapshot(dt, fields)
        dt.started_at, dt.ended_at = started_at, ended_at
        if ended_at:
            dt.ended_by, dt.end_source = actor, source
        else:
            dt.ended_by, dt.end_source = None, ""
        dt.save()
        audit.record("incident.downtime_updated", actor=actor, organization=sr.organization, target=sr,
                     before=before, after=audit.snapshot(dt, fields), request=request)
    return dt


@transaction.atomic
def set_downtime(sr: ServiceRequest, *, started_at, ended_at=None, actor, request=None) -> Downtime:
    sr = ServiceRequest.objects.select_for_update().get(pk=sr.pk)
    if sr.status in (REJECTED, CLOSED):
        raise Conflict("Downtime of a rejected or closed request can no longer be changed.", code="request_locked")
    return _write_downtime(sr, started_at=started_at, ended_at=ended_at, actor=actor, request=request)


def _end_open_downtime(sr, *, actor, source, request):
    dt = Downtime.objects.for_organization(sr.organization).filter(request=sr, ended_at__isnull=True).first()
    if dt is not None:
        _write_downtime(sr, started_at=dt.started_at, ended_at=max(timezone.now(), dt.started_at), actor=actor,
                        request=request, source=source)


# --- evidence ------------------------------------------------------------------------------------------------


@transaction.atomic
def add_evidence(sr: ServiceRequest, uploaded, *, description: str = "", actor, request=None):
    if sr.status in (REJECTED, CLOSED):
        raise Conflict("Evidence cannot be added to a rejected or closed request.", code="request_locked")
    att = files.attach(uploaded, target=sr, organization=sr.organization, user=actor, description=description,
                       read_permission="incident.view", request=request)
    audit.record("incident.evidence_added", actor=actor, organization=sr.organization, target=sr,
                 metadata={"file": att.original_name, "attachment": str(att.pk)}, request=request)
    return att
