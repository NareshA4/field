"""M11 read side. Trackings, breaches and events are restricted to the organization AND the sites where the caller
holds ``sla.view`` (the site of the tracked request / work order); profiles are organization configuration
(``sla.manage``). ``metrics`` is the data contract M14 will build its dashboards on (no UI here)."""
from __future__ import annotations

import uuid
from datetime import datetime

from django.db.models import Count, Q

from apps.rbac import services as rbac
from apps.sites.selectors import scoped_get

from .models import RESOLUTION, RESPONSE, SLABreach, SLAEvent, SLAProfile, SLATracking


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _uuid_filter(qs, params, param, field):
    value = (params.get(param) or "").strip()
    if value:
        parsed = _uuid_or_none(value)
        return qs.filter(**{field: parsed}) if parsed else qs.none()
    return qs


# --- profiles -------------------------------------------------------------------------------------------------------------


def profiles_for(org):
    return SLAProfile.objects.for_organization(org).select_related("site")


def get_profile(org, pk) -> SLAProfile:
    return scoped_get(profiles_for(org), pk, "SLA profile")


# --- trackings ------------------------------------------------------------------------------------------------------------


def trackings_for(membership, org):
    scope = rbac.site_scope(membership, "sla.view")
    qs = SLATracking.objects.for_organization(org).select_related("profile", "request", "work_order", "site")
    return scope.filter(qs, "site_id")


def get_tracking(membership, org, pk) -> SLATracking:
    return scoped_get(trackings_for(membership, org), pk, "SLA tracking")


def filter_trackings(qs, params):
    qs = _uuid_filter(qs, params, "site", "site_id")
    qs = _uuid_filter(qs, params, "profile", "profile_id")
    qs = _uuid_filter(qs, params, "request", "request_id")
    qs = _uuid_filter(qs, params, "work_order", "work_order_id")
    status = (params.get("status") or "").strip()
    if status:
        qs = qs.filter(status=status) if status in SLATracking.Status.values else qs.none()
    priority = (params.get("priority") or "").strip()
    if priority:
        qs = qs.filter(priority=priority)
    kind = (params.get("subject") or "").strip()
    if kind == "REQUEST":
        qs = qs.filter(request__isnull=False)
    elif kind == "WORK_ORDER":
        qs = qs.filter(work_order__isnull=False)
    if (params.get("breached") or "") in ("1", "true"):
        qs = qs.filter(Q(response_state__in=("BREACHED", "MET_LATE")) | Q(resolution_state__in=("BREACHED", "MET_LATE")))
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(request__number__icontains=q) | Q(work_order__number__icontains=q)
                       | Q(request__title__icontains=q) | Q(work_order__title__icontains=q))
    return qs.order_by("-started_at", "-id")


def events_for(org, tracking):
    return SLAEvent.objects.for_organization(org).filter(tracking=tracking).select_related("actor", "rule")


def tracking_for_request(membership, org, request_obj):
    """The SLA panel of a request page (None when no profile applied or the caller lacks ``sla.view``)."""
    return trackings_for(membership, org).filter(request=request_obj).first()


def tracking_for_work_order(membership, org, wo):
    return trackings_for(membership, org).filter(work_order=wo).first()


# --- breaches -------------------------------------------------------------------------------------------------------------


def breaches_for(membership, org):
    scope = rbac.site_scope(membership, "sla.view")
    qs = SLABreach.objects.for_organization(org).select_related(
        "tracking__request", "tracking__work_order", "profile", "site", "acknowledged_by")
    return scope.filter(qs, "site_id")


def get_breach(membership, org, pk) -> SLABreach:
    return scoped_get(breaches_for(membership, org), pk, "SLA breach")


def filter_breaches(qs, params):
    qs = _uuid_filter(qs, params, "site", "site_id")
    qs = _uuid_filter(qs, params, "tracking", "tracking_id")
    status = (params.get("status") or "").strip()
    if status:
        qs = qs.filter(status=status) if status in SLABreach.Status.values else qs.none()
    kind = (params.get("target") or "").strip()
    if kind:
        qs = qs.filter(target_kind=kind)
    return qs.order_by("-breached_at", "-id")


# --- metrics (the data contract for M14) -------------------------------------------------------------------------------------


def _bucket(qs, field: str) -> dict:
    counts = {r[field]: r["n"] for r in qs.values(field).annotate(n=Count("id"))}
    met, late, breached, pending = (counts.get(k, 0) for k in ("MET", "MET_LATE", "BREACHED", "PENDING"))
    finished = met + late + breached
    return {"met": met, "met_late": late, "breached": breached, "pending": pending,
            "not_applicable": counts.get("NOT_APPLICABLE", 0),
            "compliance_percent": round(100 * met / finished, 1) if finished else None}


def metrics(membership, org, *, since: datetime | None = None, until: datetime | None = None) -> dict:
    """Real aggregates over the caller's visible trackings started in [since, until]: per-target outcome counts and
    compliance (on-time / decided), open breaches by target and priority, escalated breaches, paused trackings."""
    qs = trackings_for(membership, org)
    if since:
        qs = qs.filter(started_at__gte=since)
    if until:
        qs = qs.filter(started_at__lte=until)
    breaches = breaches_for(membership, org).filter(tracking__in=qs.values("pk"))
    open_breaches = breaches.exclude(status="CLOSED")
    by_priority = {}
    for r in open_breaches.values("target_kind", "priority").annotate(n=Count("id")):
        by_priority.setdefault(r["target_kind"].lower(), {})[r["priority"]] = r["n"]
    return {
        "trackings": qs.count(),
        "active": qs.filter(status="ACTIVE").count(),
        "paused": qs.filter(status="PAUSED").count(),
        "response": _bucket(qs, "response_state"),
        "resolution": _bucket(qs, "resolution_state"),
        "breaches_total": breaches.count(),
        "breaches_open": open_breaches.count(),
        "breaches_open_by_priority": by_priority,
        "breaches_escalated": breaches.filter(escalation_level__gt=0).count(),
        "targets": [RESPONSE, RESOLUTION],
    }
