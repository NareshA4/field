"""Audit read side (M15). One shared pipeline for the viewer, the API, the compliance reports and the exports:

    organization  ->  site scope (``audit.view``)  ->  filters (``search``)

so a filter, a report and an export can never show more than the viewer itself. Rows are append-only; this module
only reads."""
from __future__ import annotations

import datetime
import uuid

from django.db.models import Q
from django.utils.dateparse import parse_date

from apps.core.exceptions import ValidationFailed
from apps.rbac import services as rbac

from .models import AuditLog

STATUS_ACTIONS = Q(action__endswith=".status_changed")


def _meta_action(*names):
    return Q(action__endswith=".status_changed", metadata__action__in=names)


# category key -> (label, description, Q). Prefix rules mirror the action names written by the services.
CATEGORIES = {
    "security": ("Security events", "Sign-ins, lockouts, user / role / membership changes, label scans that failed.",
                 Q(action__startswith="auth.") | Q(action__startswith="user.") | Q(action__startswith="role.")
                 | Q(action__startswith="membership.") | Q(action__startswith="organization.")
                 | Q(action__in=("qr.scan_unknown", "qr.scan_denied", "qr.scan_revoked", "portal.account_enabled",
                                 "portal.account_disabled", "portal.asset_granted", "portal.asset_revoked"))),
    "state": ("State changes", "Every lifecycle transition of requests, work orders and assets.",
              STATUS_ACTIONS | Q(action="asset.location_changed")),
    "assignment": ("Assignments", "Technician assignment, dispatch and reassignment.",
                   Q(action="work_order.reassigned") | _meta_action("assign", "dispatch")),
    "approvals": ("Approvals", "Triage, approval, rejection, supervisor review, confirmation.",
                  _meta_action("triage", "approve", "reject", "confirm", "reopen", "review", "start_review")),
    "closures": ("Closures", "Closure of requests and work orders.", _meta_action("close")),
    "inventory": ("Inventory", "Stock receipts, issues, returns, transfers, adjustments and part lines.",
                  Q(action__startswith="stock.") | Q(action__startswith="part") | Q(action__startswith="warehouse.")),
    "sla": ("SLA events", "Timers, warnings, breaches and escalations.", Q(action__startswith="sla.")),
    "evidence": ("Evidence", "Uploaded files and evidence attached to records.",
                 Q(action="file.uploaded") | Q(action__endswith=".evidence_added")),
    "coverage": ("Contracts & labels", "Warranty / AMC / contract and QR label activity.",
                 Q(action__startswith="contract.") | Q(action__startswith="qr.")),
    "client": ("Client portal", "Requests, confirmations and access changes in the client portal.",
               Q(action__startswith="portal.")),
    "exports": ("Exports", "Evidence exports (audit rows and per-work-order evidence packages).", Q(action__in=("audit.exported", "audit.evidence_exported"))),
}


def organization_logs(org):
    return AuditLog.objects.for_organization(org).select_related("actor")


def visible_logs(membership, org):
    """Rows the caller may read: all of them with an organization-wide ``audit.view``, otherwise only rows of the
    caller's sites (organization-level rows without a site stay hidden from site-scoped reviewers)."""
    qs = organization_logs(org)
    scope = rbac.site_scope(membership, "audit.view")
    return qs if scope.all_sites else qs.filter(site_id__in=scope.site_ids)


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _resolve(model, org, value, field):
    """An id, or the human reference (asset tag / work order number), inside the organization."""
    parsed = _uuid(value)
    if parsed:
        return parsed
    obj = model.objects.for_organization(org).filter(**{f"{field}__iexact": value}).first()
    return obj.pk if obj else None


def _day(value, what, strict):
    if not value:
        return None
    try:
        parsed = parse_date(value) if isinstance(value, str) else value
    except ValueError:
        parsed = None
    if parsed is None:
        if strict:
            raise ValidationFailed(f"{what} must be a date (YYYY-MM-DD).", code="invalid_date")
    return parsed


def entity_q(org, asset=None, work_order=None):
    """Audit rows that belong to one asset (itself, its work orders, requests, inspections, contracts) or to one
    work order (itself, its source request, its part lines)."""
    from apps.incidents.models import ServiceRequest
    from apps.inventory.models import WorkOrderPart
    from apps.workorders.models import WorkOrder

    q = Q(pk__in=[])
    if asset is not None:
        wo_ids = [str(i) for i in WorkOrder.objects.for_organization(org).filter(asset_id=asset).values_list(
            "pk", flat=True)]
        sr_ids = [str(i) for i in ServiceRequest.objects.for_organization(org).filter(asset_id=asset).values_list(
            "pk", flat=True)]
        q = (Q(target_type="assets.asset", target_id=str(asset))
             | Q(target_type="workorders.workorder", target_id__in=wo_ids)
             | Q(target_type="incidents.servicerequest", target_id__in=sr_ids))
    if work_order is not None:
        wo = WorkOrder.objects.for_organization(org).filter(pk=work_order).first()
        line_ids = [str(i) for i in WorkOrderPart.objects.for_organization(org).filter(
            work_order_id=work_order).values_list("pk", flat=True)]
        q |= Q(target_type="workorders.workorder", target_id=str(work_order)) | Q(
            target_type="inventory.workorderpart", target_id__in=line_ids)
        if wo is not None and wo.source_request_id:
            q |= Q(target_type="incidents.servicerequest", target_id=str(wo.source_request_id))
    return q


def search(qs, params, *, org=None, strict=True):
    """Applies the viewer / API / export filters. ``strict`` raises ValidationFailed for malformed input (UI and API
    show it); the legacy lenient mode ignores it (platform console)."""
    get = lambda k: (params.get(k) or "").strip()  # noqa: E731
    action, q, actor = get("action"), get("q"), get("actor")
    if action:
        qs = qs.filter(action__startswith=action)
    if q:
        qs = qs.filter(Q(actor_email__icontains=q) | Q(target_repr__icontains=q) | Q(action__icontains=q))
    if actor:
        qs = qs.filter(actor_email__icontains=actor)
    if get("entity_type"):
        qs = qs.filter(target_type=get("entity_type"))
    if get("entity_id"):
        qs = qs.filter(target_id=get("entity_id"))
    if get("request_id"):
        qs = qs.filter(request_id=get("request_id"))
    if get("site"):
        sid = _uuid(get("site"))
        if sid is None:
            if strict:
                _bad("Site")
            qs = qs.none()
        else:
            qs = qs.filter(site_id=sid)
    category = get("category")
    if category:
        if category not in CATEGORIES:
            if strict:
                raise ValidationFailed("Unknown category.", code="invalid_category")
        else:
            qs = qs.filter(CATEGORIES[category][2])
    if org is not None and (get("asset") or get("work_order")):
        from apps.assets.models import Asset
        from apps.workorders.models import WorkOrder

        asset = _resolve(Asset, org, get("asset"), "asset_tag") if get("asset") else None
        wo = _resolve(WorkOrder, org, get("work_order"), "number") if get("work_order") else None
        if (get("asset") and asset is None) or (get("work_order") and wo is None):
            qs = qs.none()
        else:
            qs = qs.filter(entity_q(org, asset, wo))
    frm, to = _day(get("from"), "From", strict), _day(get("to"), "To", strict)
    if frm and to and frm > to and strict:
        raise ValidationFailed("The start date is after the end date.", code="invalid_range")
    tz = datetime.UTC
    if frm:
        qs = qs.filter(occurred_at__gte=datetime.datetime.combine(frm, datetime.time.min, tzinfo=tz))
    if to:
        qs = qs.filter(occurred_at__lt=datetime.datetime.combine(to + datetime.timedelta(days=1), datetime.time.min,
                                                                 tzinfo=tz))
    return qs


def _bad(what):
    raise ValidationFailed(f"{what} is not a valid id.", code="invalid_id")


def filter_logs(qs, params):
    """Legacy lenient filter (platform console, existing callers)."""
    return search(qs, params, strict=False)


def entity_types(qs):
    return sorted(set(qs.order_by().values_list("target_type", flat=True).distinct()) - {""})
