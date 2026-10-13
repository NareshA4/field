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

from apps.assets.models import Asset
from apps.audit import services as audit
from apps.core.exceptions import Conflict, ValidationFailed
from apps.core.sequences import next_number
from apps.files import services as files

from .models import Downtime, ServiceRequest, ServiceRequestHistory
from .workflow import (
    ACTION_PERMISSIONS,
    ACTIVE_INCIDENT_STATUSES,
    APPROVED,
    CLOSED,
    IN_SERVICE,
    NEW,
    NON_BLOCKING_STATUSES,
    REASON_REQUIRED,
    REJECTED,
    REQUEST_STATUS,
    RESOLVED,
    TRIAGED,
    WORK_ORDER_CREATED,
)


class DuplicateIncidentError(Conflict):
    """Raised when one or more existing active incidents match candidate details on the same asset."""

    def __init__(self, duplicates: list[dict], message="Possible duplicate incident detected.",
                 code="duplicate_incident_detected"):
        super().__init__(message, code=code)
        self.duplicates = duplicates


SNAPSHOT = [
    "title", "description", "severity", "kind", "service_impact", "impact_notes",
    "failure_category", "assigned_team", "occurred_at",
]
_EDITABLE = {
    "title", "description", "severity", "service_impact", "impact_notes",
    "failure_category", "assigned_team", "occurred_at",
}
_SEVERITY_TO_PRIORITY = {"LOW": "LOW", "MEDIUM": "MEDIUM", "HIGH": "HIGH", "CRITICAL": "CRITICAL"}
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


# --- duplicate detection -----------------------------------------------------------------------------------


def _report_text_matches(candidate: str, existing: str) -> bool:
    """Compare report text conservatively, allowing small wording changes and shared key terms."""
    import difflib
    import re

    normalize = lambda value: " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))
    candidate, existing = normalize(candidate), normalize(existing)
    if not candidate or not existing:
        return False
    if candidate == existing or candidate in existing or existing in candidate:
        return True
    if difflib.SequenceMatcher(None, candidate, existing).ratio() >= 0.72:
        return True
    ignored = {"the", "and", "for", "with", "from", "not", "this", "that", "has", "have", "been", "in", "on", "of", "to"}
    left = {word for word in candidate.split() if len(word) >= 3 and word not in ignored}
    right = {word for word in existing.split() if len(word) >= 3 and word not in ignored}
    shared = left & right
    return len(shared) >= 2 and len(shared) / max(len(left), len(right)) >= 0.5


def find_duplicate_incidents(org, *, asset_id, kind: str = "INCIDENT", failure_category: str = "",
                            service_impact: str = "", severity: str = "", description: str = "",
                            title: str = "", exclude_pk=None) -> list[dict]:
    """Find open reports for the same asset and type whose description matches the new report.

    If either description is blank, title is used as the fallback matching text. Terminal statuses and
    linked duplicate follow-ups never block a report.
    """
    if not asset_id:
        return []

    qs = ServiceRequest.objects.for_organization(org).filter(
        asset_id=asset_id,
        kind=kind,
        status__in=ACTIVE_INCIDENT_STATUSES,
    ).exclude(status__in=NON_BLOCKING_STATUSES).exclude(duplicate_of__isnull=False).select_related(
        "asset", "asset__category", "asset__site", "reported_by__user"
    )
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)

    requests = list(qs.order_by("-created_at"))
    downtime_by_request = {
        item.request_id: item
        for item in Downtime.objects.for_organization(org).filter(request_id__in=[sr.pk for sr in requests])
    }
    matches = []
    for sr in requests:
        if description.strip() and sr.description.strip():
            text_match = _report_text_matches(description, sr.description)
        else:
            text_match = _report_text_matches(title, sr.title)
        if not text_match:
            continue

        wo = sr.linked_work_order
        downtime = downtime_by_request.get(sr.pk)
        matches.append({
            "id": str(sr.pk),
            "number": sr.number,
            "status": sr.status,
            "status_display": sr.get_status_display(),
            "kind": sr.kind,
            "kind_display": sr.get_kind_display(),
            "title": sr.title,
            "description": sr.description,
            "asset_tag": sr.asset.asset_tag,
            "asset_name": sr.asset.name,
            "site_code": sr.site.code,
            "severity": sr.severity,
            "severity_display": sr.get_severity_display(),
            "service_impact": sr.service_impact,
            "service_impact_display": sr.get_service_impact_display(),
            "impact_notes": sr.impact_notes,
            "failure_category": sr.failure_category or (sr.asset.category.name if sr.asset.category else ""),
            "assigned_team": sr.assigned_team_display,
            "linked_work_order": wo.order_number if wo else None,
            "linked_work_order_status": wo.get_status_display() if wo else None,
            "created_at": sr.created_at.isoformat(),
            "created_at_display": sr.created_at.strftime("%Y-%m-%d %H:%M"),
            "occurred_at_display": sr.occurred_at.strftime("%Y-%m-%d %H:%M") if sr.occurred_at else None,
            "downtime_started_at_display": downtime.started_at.strftime("%Y-%m-%d %H:%M") if downtime else None,
            "reported_by": sr.reported_by.user.display_name,
        })

    return matches


# --- create / edit -------------------------------------------------------------------------------------------


def create_request(org, *, asset, reporter, title: str, description: str = "", kind: str = "INCIDENT",
                   severity: str = "MEDIUM", service_impact: str = "NONE", impact_notes: str = "",
                   failure_category: str = "", assigned_team: str = "",
                   override_duplicate: bool = False, override_reason: str = "",
                   duplicate_of=None,
                   occurred_at=None, downtime_started_at=None, actor, request=None) -> ServiceRequest:
    """Reports an incident / service request against an asset. ``reporter`` is the actor's Membership.
    Supports simultaneous submissions via row-level transaction lock on the asset, and enforces
    duplicate detection across active incidents on the same asset.
    """
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

    # Concurrency control & duplicate check inside serialized transaction
    duplicates = []
    dup_records = None
    with transaction.atomic():
        locked_asset = Asset.objects.select_for_update().get(pk=asset.pk)
        if not duplicate_of:
            duplicates = find_duplicate_incidents(
                org, asset_id=locked_asset.pk, kind=kind, failure_category=failure_category,
                service_impact=service_impact, severity=severity,
                description=description, title=title,
            )
        if duplicates and not override_duplicate:
            dup_records = list(duplicates)

    if dup_records:
        for dup in dup_records:
            target_sr = ServiceRequest.objects.filter(pk=dup["id"]).first()
            audit.record(
                "incident.duplicate_detected",
                actor=actor,
                organization=org,
                target=target_sr,
                metadata={
                    "existing_number": dup["number"],
                    "asset": asset.asset_tag,
                    "attempted_title": title,
                    "attempted_severity": severity,
                },
                request=request,
            )
        raise DuplicateIncidentError(
            duplicates=dup_records,
            message=f"Possible duplicate incident detected: {dup_records[0]['number']}.",
            code="duplicate_incident_detected",
        )

    if duplicates and override_duplicate:
        override_reason = (override_reason or "").strip()
        if len(override_reason) < 3:
            raise ValidationFailed(
                "A mandatory override reason of at least 3 characters is required to create a duplicate incident.",
                code="override_reason_required",
            )

    occurred_at = occurred_at or timezone.now()
    _check_time(occurred_at, "The time of the failure")
    _check_time(downtime_started_at, "The downtime start")
    prefix = "INC" if kind == "INCIDENT" else "SR"

    with transaction.atomic():
        locked_asset = Asset.objects.select_for_update().get(pk=asset.pk)
        sr = ServiceRequest(
            organization=org, number=next_number(org, f"request.{kind.lower()}", prefix), kind=kind,
            title=_clean_title(title), description=(description or "").strip(), severity=severity,
            service_impact=service_impact, impact_notes=(impact_notes or "").strip()[:500],
            failure_category=(failure_category or "").strip()[:100],
            assigned_team=(assigned_team or "").strip()[:100],
            duplicate_of=duplicate_of,
            override_reason=(override_reason or "").strip()[:500],
            asset=locked_asset,
            site=locked_asset.site, status=NEW, reported_by=reporter, occurred_at=occurred_at)
        sr.save()
        _history(sr, from_status="", to_status=NEW, action="report", reason="", actor=actor, source="manual")

        audit.record("incident.created", actor=actor, organization=org, target=sr,
                     after={**audit.snapshot(sr, SNAPSHOT), "asset": asset.asset_tag, "site": asset.site.code,
                            "number": sr.number}, request=request)

        if duplicates and override_duplicate:
            audit.record(
                "incident.duplicate_overridden",
                actor=actor,
                organization=org,
                target=sr,
                metadata={
                    "override_reason": override_reason,
                    "duplicate_numbers": [d["number"] for d in duplicates],
                },
                request=request,
            )

        if downtime_started_at is not None:
            _write_downtime(sr, started_at=downtime_started_at, ended_at=None, actor=actor, request=request)
        _sla().on_request_created(sr)  # M11: the SLA starts from the persisted created_at
        return sr


@transaction.atomic
def link_duplicate_request(existing_sr: ServiceRequest, *, reporter, actor, title: str = "",
                           description: str = "", notes: str = "", severity: str | None = None,
                           service_impact: str | None = None, failure_category: str | None = None,
                           assigned_team: str | None = None, request=None) -> ServiceRequest:
    """Links a new report to an existing active incident instead of creating a duplicate ticket."""
    existing_sr = ServiceRequest.objects.select_for_update().get(pk=existing_sr.pk)
    if existing_sr.status not in ACTIVE_INCIDENT_STATUSES or existing_sr.duplicate_of_id is not None:
        raise Conflict("This incident is no longer open for linked reports. Refresh and review the current matches.",
                       code="duplicate_target_unavailable")
    org = existing_sr.organization

    title_clean = _clean_title(title or f"Follow-up for {existing_sr.number}")
    severity = severity or existing_sr.severity
    service_impact = service_impact or existing_sr.service_impact
    if severity not in ServiceRequest.Severity.values or service_impact not in ServiceRequest.Impact.values:
        raise ValidationFailed("Choose a valid severity and service impact for the linked report.",
                               code="invalid_linked_report_details")
    prefix = "INC" if existing_sr.kind == "INCIDENT" else "SR"

    new_sr = ServiceRequest(
        organization=org,
        number=next_number(org, f"request.{existing_sr.kind.lower()}", prefix),
        kind=existing_sr.kind,
        title=title_clean,
        description=(description or "").strip(),
        severity=severity,
        service_impact=service_impact,
        impact_notes=(notes or "").strip()[:500],
        failure_category=(failure_category if failure_category is not None else existing_sr.failure_category),
        assigned_team=(assigned_team if assigned_team is not None else existing_sr.assigned_team),
        duplicate_of=existing_sr,
        asset=existing_sr.asset,
        site=existing_sr.site,
        status=RESOLVED,
        reported_by=reporter,
        occurred_at=timezone.now(),
    )
    new_sr.save()

    reason_text = f"Linked duplicate request {new_sr.number}: {title_clean}"
    if notes:
        reason_text += f". Notes: {notes}"
    _history(existing_sr, from_status=existing_sr.status, to_status=existing_sr.status,
             action="duplicate_linked", reason=reason_text[:500], actor=actor, source="manual")

    _history(new_sr, from_status="", to_status=RESOLVED,
             action="linked_to_existing", reason=f"Linked to duplicate {existing_sr.number}",
             actor=actor, source="manual")

    audit.record(
        "incident.duplicate_linked",
        actor=actor,
        organization=org,
        target=existing_sr,
        metadata={
            "existing_number": existing_sr.number,
            "linked_request_id": str(new_sr.pk),
            "linked_request_number": new_sr.number,
            "title": title_clean,
            "notes": notes,
        },
        request=request,
    )
    return new_sr


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
    if "failure_category" in changes:
        sr.failure_category = (changes["failure_category"] or "").strip()[:100]
    if "assigned_team" in changes:
        sr.assigned_team = (changes["assigned_team"] or "").strip()[:100]
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
    """Convert an APPROVED request to one linked Draft work order, inheriting source data and evidence.

    The caller must hold ``work_order.create`` for the request's site. Optional planning values are passed through
    to M06; source-owned fields always come from the approved request.
    """
    from apps.workorders import services as work_orders
    from apps.workorders.models import WorkOrder, WorkOrderEvidence
    from apps.files.models import Attachment
    from django.contrib.contenttypes.models import ContentType

    sr = ServiceRequest.objects.select_related("asset", "site").select_for_update(of=("self",)).get(pk=sr.pk)
    if sr.duplicate_of_id is not None:
        raise Conflict("Cannot create a work order for a linked duplicate incident.", code="duplicate_incident_work_order")
    if sr.status != APPROVED:
        REQUEST_STATUS.get(sr.status, "link_work_order")  # raises invalid_transition with the usual envelope
    existing_wo = WorkOrder.objects.for_organization(sr.organization).filter(
        source_reference=sr.number,
        source_type__in=(WorkOrder.SourceType.REQUEST, WorkOrder.SourceType.INCIDENT),
    ).first()
    if existing_wo:
        raise Conflict(f"A work order ({existing_wo.order_number}) already exists for this request.", code="duplicate_work_order")
    source_attachments = list(Attachment.objects.for_organization(sr.organization).filter(
        content_type=ContentType.objects.get_for_model(sr), object_id=str(sr.pk)))
    component = plan.pop("component", getattr(sr, "component", None))
    wo = work_orders.create_work_order(
        sr.organization, asset=sr.asset, actor=actor, membership=membership, request=request, source_request=sr,
        component=component,
        work_type=plan.pop("work_type", "CORRECTIVE"), title=plan.pop("title", None) or sr.title,
        description=plan.pop("description", None) or sr.description,
        priority=plan.pop("priority", None) or _SEVERITY_TO_PRIORITY[sr.severity], **plan)
    linked_ids = []
    for attachment in source_attachments:
        WorkOrderEvidence.objects.get_or_create(
            organization=sr.organization, work_order=wo, attachment=attachment,
            defaults={"linked_by": actor},
        )
        linked_ids.append(str(attachment.pk))
    audit.record(
        "work_order.converted_from_service_request", actor=actor, organization=sr.organization, target=wo,
        metadata={
            "service_request_id": str(sr.pk), "service_request_number": sr.number,
            "title": sr.title, "description": sr.description, "site_id": str(sr.site_id),
            "asset_id": str(sr.asset_id), "component_id": str(component.pk if component else ""),
            "severity": sr.severity, "priority": _SEVERITY_TO_PRIORITY[sr.severity],
            "service_impact": sr.service_impact, "attachment_ids": linked_ids,
            "attachment_count": len(linked_ids), "status": WorkOrder.Status.DRAFT,
        }, request=request,
    )
    _apply(sr, "link_work_order", reason=wo.order_number, actor=actor, source="manual", request=request)
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
