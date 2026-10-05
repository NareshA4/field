"""M15 evidence package (HPE "evidence exports", Day-90 demo 7 "show audit evidence"): one ZIP per work order that
gathers everything an auditor needs - the work-order record, its lifecycle timeline and closure / approval decisions,
the source request history, the checklist results and findings, labor / materials / part movements, SLA and coverage
information, the uploaded evidence files themselves and the audit-trail rows - plus a manifest with SHA-256 checksums.

Authorisation is the caller's: the work order must be visible to them (organization + site / assignment scope), they
need ``audit.export`` for the order's site and the audit rows are the ones ``audit.view`` already allows. Nothing
outside the order's own organization is ever read (every query is tenant scoped). The export is itself audited."""
from __future__ import annotations

import csv
import datetime
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from apps.core.exceptions import NotFound, PermissionDenied
from apps.rbac import services as rbac

from . import exports, selectors, services

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
ROW_LIMIT = exports.EXPORT_LIMIT


@dataclass
class Package:
    body: bytes
    filename: str
    files: int
    skipped: int
    audit_rows: int


def _cell(value) -> str:
    return exports._safe(value)


def _iso(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.astimezone(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _csv(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([_cell(c) for c in r])
    return buf.getvalue().encode("utf-8")


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, indent=2, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80] or "file"


def build(membership, org, work_order_pk, *, actor, request=None) -> Package:
    from apps.checklists import services as cl_services
    from apps.checklists.models import Finding, Inspection, InspectionResponse
    from apps.contracts.models import CoverageCheck
    from apps.incidents.models import ServiceRequestHistory
    from apps.inventory.models import StockMovement, WorkOrderPart
    from apps.sla.models import SLAEvent, SLATracking
    from apps.workorders import selectors as wo_selectors
    from apps.workorders import services as wo_services
    from apps.workorders.models import WorkOrderEvent, WorkOrderLabor, WorkOrderMaterial

    try:
        wo = wo_selectors.get_work_order(membership, org, work_order_pk)
    except Exception as exc:  # unknown id / other tenant / out of scope: the caller learns nothing
        raise NotFound("Work order not found.") from exc
    if not rbac.has_permission(membership, "audit.export", wo.site_id):
        raise PermissionDenied("You may not export evidence for this site.")
    wo = type(wo).objects.select_related("asset", "site", "assigned_to__user", "source_request", "closed_by").get(
        pk=wo.pk)

    entries: dict[str, bytes] = {}
    sr = wo.source_request

    entries["summary.json"] = _json_bytes({
        "work_order": {
            "number": wo.number, "title": wo.title, "description": wo.description, "type": wo.work_type,
            "priority": wo.priority, "status": wo.status, "site": f"{wo.site.code} {wo.site.name}",
            "asset": f"{wo.asset.asset_tag} {wo.asset.name}", "asset_status_now": wo.asset.status,
            "assigned_to": wo.assigned_to.user.email if wo.assigned_to_id else None,
            "planned_start": _iso(wo.planned_start), "planned_end": _iso(wo.planned_end),
            "dispatched_at": _iso(wo.dispatched_at), "started_at": _iso(wo.started_at),
            "completed_at": _iso(wo.completed_at), "review_started_at": _iso(wo.review_started_at),
            "closed_at": _iso(wo.closed_at), "closed_by": wo.closed_by.email if wo.closed_by_id else None,
            "resolution_notes": wo.resolution_notes, "hold_reason": wo.hold_reason},
        "source_request": ({"number": sr.number, "kind": sr.kind, "title": sr.title, "severity": sr.severity,
                            "status": sr.status, "occurred_at": _iso(sr.occurred_at)} if sr else None),
        "closure_blockers_now": wo_services.closure_blockers(wo) if wo.status not in ("CLOSED", "CANCELLED") else [],
    })

    events = WorkOrderEvent.objects.for_organization(org).filter(work_order=wo).select_related(
        "actor", "assigned_to__user").order_by("created_at", "id")
    entries["timeline.csv"] = _csv(
        ["When (UTC)", "Action", "From", "To", "Actor", "Assigned to", "Reason"],
        [[_iso(e.created_at), e.action, e.from_status, e.to_status, e.actor.email if e.actor_id else "system",
          e.assigned_to.user.email if e.assigned_to_id else "", e.reason] for e in events])
    decisions = ("start_review", "close", "reject_review")
    approvals = [[_iso(e.created_at), e.action, e.actor.email if e.actor_id else "system", e.reason]
                 for e in events if e.action in decisions]
    if sr is not None:
        for h in ServiceRequestHistory.objects.for_organization(org).filter(request=sr).select_related(
                "changed_by").order_by("created_at", "id"):
            if h.action in ("triage", "approve", "reject", "confirm", "reopen", "close"):
                approvals.append([_iso(h.created_at), f"request:{h.action}",
                                  h.changed_by.email if h.changed_by_id else "system", h.reason])
    entries["approvals.csv"] = _csv(["When (UTC)", "Decision", "Actor", "Comment"], sorted(approvals))

    if sr is not None:
        entries["request_history.csv"] = _csv(
            ["When (UTC)", "Action", "From", "To", "Actor", "Source", "Reason"],
            [[_iso(h.created_at), h.action, h.from_status, h.to_status, h.changed_by.email if h.changed_by_id else "",
              h.source, h.reason] for h in ServiceRequestHistory.objects.for_organization(org).filter(
                request=sr).select_related("changed_by").order_by("created_at", "id")])

    checklists = []
    for insp in Inspection.objects.for_organization(org).filter(work_order=wo).select_related(
            "template", "completed_by__user").order_by("started_at"):
        responses = InspectionResponse.objects.for_organization(org).filter(inspection=insp).select_related(
            "item", "answered_by__user").order_by("item__position")
        checklists.append({
            "template": f"{insp.template.name} v{insp.template.version}", "status": insp.status,
            "started_at": _iso(insp.started_at), "completed_at": _iso(insp.completed_at),
            "completed_by": insp.completed_by.user.email if insp.completed_by_id else None, "summary": insp.summary,
            "responses": [{"item": r.item.prompt, "text": r.value_text,
                           "number": None if r.value_number is None else str(r.value_number),
                           "pass": r.value_bool, "exception": r.is_exception,
                           "answered_by": r.answered_by.user.email, "answered_at": _iso(r.answered_at)}
                          for r in responses],
            "findings": [{"severity": f.severity, "status": f.status, "description": f.description,
                          "resolution_notes": f.resolution_notes, "resolved_at": _iso(f.resolved_at)}
                         for f in Finding.objects.for_organization(org).filter(inspection=insp)]})
    entries["checklists.json"] = _json_bytes(checklists)

    entries["labor.csv"] = _csv(
        ["Date", "Technician", "Hours", "Notes"],
        [[row.work_date, row.technician.user.email, row.hours, row.notes] for row in WorkOrderLabor.objects.for_organization(
            org).filter(work_order=wo).select_related("technician__user").order_by("work_date", "id")])
    entries["materials.csv"] = _csv(
        ["Description", "Part number", "Quantity", "Unit"],
        [[m.description, m.part_number, m.quantity, m.unit] for m in WorkOrderMaterial.objects.for_organization(
            org).filter(work_order=wo)])
    lines = list(WorkOrderPart.objects.for_organization(org).filter(work_order=wo).select_related("part"))
    entries["parts.csv"] = _csv(
        ["Part", "Status", "Requested", "Issued", "Consumed", "Returned"],
        [[f"{p.part.part_number} {p.part.name}", p.status, p.quantity_requested, p.quantity_issued,
          p.quantity_consumed, p.quantity_returned] for p in lines])
    entries["stock_movements.csv"] = _csv(
        ["When (UTC)", "Type", "Part", "Warehouse", "Quantity", "On hand after", "Reason"],
        [[_iso(m.created_at), m.movement_type, m.part.part_number, m.warehouse.code, m.quantity, m.on_hand_after,
          m.reason] for m in StockMovement.objects.for_organization(org).filter(work_order=wo).select_related(
            "part", "warehouse").order_by("created_at", "id")])
    entries["coverage_checks.csv"] = _csv(
        ["When (UTC)", "Agreement", "Eligible", "Reference date", "Reason"],
        [[_iso(c.created_at), c.agreement.reference if c.agreement_id else "", c.eligible, c.reference_date, c.reason]
         for c in CoverageCheck.objects.for_organization(org).filter(work_order=wo).select_related(
            "agreement").order_by("created_at")])

    trackings = SLATracking.objects.for_organization(org).filter(work_order=wo) | SLATracking.objects.for_organization(
        org).filter(request=sr) if sr else SLATracking.objects.for_organization(org).filter(work_order=wo)
    sla_rows = []
    for t in trackings.select_related("profile"):
        sla_rows.append({
            "profile": t.profile.name, "priority": t.priority, "started_at": _iso(t.started_at),
            "response_due_at": _iso(t.response_due_at), "response_met_at": _iso(t.response_met_at),
            "response_state": t.response_state, "resolution_due_at": _iso(t.resolution_due_at),
            "resolution_met_at": _iso(t.resolution_met_at), "resolution_state": t.resolution_state,
            "events": [{"at": _iso(e.at), "type": e.event_type, "kind": e.target_kind, "detail": e.detail}
                       for e in SLAEvent.objects.for_organization(org).filter(tracking=t).order_by("at", "id")]})
    entries["sla.json"] = _json_bytes(sla_rows)

    # audit trail: the rows of this order / its request / its part lines, restricted to what the caller may view
    logs = selectors.visible_logs(membership, org).filter(selectors.entity_q(org, work_order=wo.pk))
    rows, truncated = exports.rows_for(logs, org)
    entries["audit_trail.csv"] = _csv(exports.COLUMNS, rows)

    # evidence files: the order's own, plus those attached to checklist answers and findings
    wo_files = list(wo_services.evidence_for(wo).select_related("uploaded_by"))
    insp_files = []
    for insp in Inspection.objects.for_organization(org).filter(work_order=wo):
        insp_files.extend(cl_services.evidence_for_inspection(insp))
    files, skipped, total, manifest = [], [], 0, []
    ct_wo = ContentType.objects.get_for_model(wo)
    for att in wo_files + insp_files:
        label = "work_order" if att.content_type_id == ct_wo.pk else "checklist"
        path = f"evidence/{label}/{att.pk}_{_safe_name(att.original_name)}"
        info = {"path": path, "original_name": att.original_name, "mime_type": att.mime_type, "size": att.size,
                "sha256": att.sha256, "uploaded_by": att.uploaded_by.email, "uploaded_at": _iso(att.created_at),
                "description": att.description}
        if att.size > MAX_FILE_BYTES or total + att.size > MAX_TOTAL_BYTES:
            skipped.append({**info, "reason": "too large for the package; download it from the record"})
            continue
        try:
            with att.file.open("rb") as fh:
                data = fh.read()
        except OSError:
            skipped.append({**info, "reason": "stored file is not readable"})
            continue
        actual = hashlib.sha256(data).hexdigest()
        info["checksum_ok"] = actual == att.sha256
        total += len(data)
        files.append((path, data))
        manifest.append(info)

    now = timezone.now()
    meta = {"package": "work-order evidence", "work_order": wo.number, "organization": org.slug,
            "generated_at": now.isoformat(), "generated_by": actor.email, "audit_rows": len(rows),
            "audit_rows_truncated": truncated, "files": manifest, "files_skipped": skipped,
            "documents": {name: hashlib.sha256(body).hexdigest() for name, body in sorted(entries.items())}}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in sorted(entries.items()):
            z.writestr(name, body)
        for path, data in files:
            z.writestr(path, data)
        z.writestr("manifest.json", _json_bytes(meta))
    services.record("audit.evidence_exported", actor=actor, organization=org, target=wo, request=request,
                    metadata={"work_order": wo.number, "files": len(files), "files_skipped": len(skipped),
                              "audit_rows": len(rows)})
    return Package(out.getvalue(), f"evidence-{wo.number}-{now:%Y%m%d-%H%M%S}.zip", len(files), len(skipped),
                   len(rows))
