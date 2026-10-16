"""M08 read side. Everything is organization-scoped first.

Inspections / findings are visible when the caller has ``inspection.view`` for the site, or (technicians) when they
started the inspection or the inspection belongs to a work order assigned to them, with ``inspection.execute`` for
the site. Templates are organization-wide configuration (``checklist.view`` / ``checklist.execute``)."""
from __future__ import annotations

import uuid

from django.db.models import Q

from apps.rbac import services as rbac
from apps.sites.selectors import scoped_get

from .models import ChecklistItem, ChecklistTemplate, Finding, Inspection


def templates_for(org):
    return ChecklistTemplate.objects.for_organization(org)


def get_template(org, pk) -> ChecklistTemplate:
    return scoped_get(templates_for(org), pk, "Checklist")


def filter_templates(qs, params):
    status = (params.get("status") or "").strip().upper()
    if status:
        qs = qs.filter(status=status) if status in ("DRAFT", "ACTIVE", "INACTIVE") else qs.none()
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(name__icontains=q)
    return qs


def items_for(org, template):
    return ChecklistItem.objects.for_organization(org).filter(template=template).order_by("position")


def _visibility(membership):
    full = rbac.site_scope(membership, "inspection.view")
    if full.all_sites:
        return Q()
    cond = Q(pk__isnull=True)  # matches nothing
    if full.site_ids:
        cond |= Q(site_id__in=full.site_ids)
    mine = rbac.site_scope(membership, "inspection.execute")
    own = Q(started_by=membership) | Q(work_order__assigned_to=membership)
    if mine.all_sites:
        cond |= own
    elif mine.site_ids:
        cond |= own & Q(site_id__in=mine.site_ids)
    return cond


def inspections_for(membership, org):
    qs = Inspection.objects.for_organization(org).select_related("template", "asset", "site", "work_order",
                                                                 "started_by__user", "completed_by__user")
    return qs.filter(_visibility(membership))


def get_inspection(membership, org, pk) -> Inspection:
    return scoped_get(inspections_for(membership, org), pk, "Inspection")


def can_see_inspection(membership, inspection) -> bool:
    return inspections_for(membership, inspection.organization).filter(pk=inspection.pk).exists()


def findings_for(membership, org):
    qs = Finding.objects.for_organization(org).select_related("asset", "site", "work_order", "item",
                                                              "inspection__template")
    full = rbac.site_scope(membership, "inspection.view")
    if full.all_sites:
        return qs
    cond = Q(pk__isnull=True)
    if full.site_ids:
        cond |= Q(site_id__in=full.site_ids)
    mine = rbac.site_scope(membership, "inspection.execute")
    own = Q(inspection__started_by=membership) | Q(work_order__assigned_to=membership)
    if mine.all_sites:
        cond |= own
    elif mine.site_ids:
        cond |= own & Q(site_id__in=mine.site_ids)
    return qs.filter(cond)


def get_finding(membership, org, pk) -> Finding:
    return scoped_get(findings_for(membership, org), pk, "Finding")


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def filter_inspections(qs, params):
    for param, field in (("site", "site_id"), ("asset", "asset_id"), ("work_order", "work_order_id"),
                         ("template", "template_id")):
        value = (params.get(param) or "").strip()
        if value:
            parsed = _uuid_or_none(value)
            qs = qs.filter(**{field: parsed}) if parsed else qs.none()
    status = (params.get("status") or "").strip().upper()
    if status:
        qs = qs.filter(status=status) if status in ("IN_PROGRESS", "COMPLETED") else qs.none()
    return qs.order_by("-started_at", "-id")


def filter_findings(qs, params):
    for param, field in (("site", "site_id"), ("asset", "asset_id"), ("work_order", "work_order_id"),
                         ("inspection", "inspection_id")):
        value = (params.get(param) or "").strip()
        if value:
            parsed = _uuid_or_none(value)
            qs = qs.filter(**{field: parsed}) if parsed else qs.none()
    for param, field, choices in (("status", "status", Finding.Status.values),
                                  ("severity", "severity", Finding.Severity.values)):
        value = (params.get(param) or "").strip().upper()
        if value:
            qs = qs.filter(**{field: value}) if value in choices else qs.none()
    return qs.order_by("-created_at", "-id")


def responses_for(org, inspection):
    from .models import InspectionResponse

    return {r.item_id: r for r in InspectionResponse.objects.for_organization(org).filter(inspection=inspection)}


def requirements_for(org, wo, services_module):
    """Checklists this work order needs / has: required templates (ACTIVE, applicable) plus every inspection that
    exists on it, with state. Used by the workspace and the API ``requirements`` action."""
    inspections = list(Inspection.objects.for_organization(org).filter(work_order=wo).select_related("template"))
    by_key = {i.template.key: i for i in inspections}
    rows = []
    seen = set()
    for t in services_module.required_templates(wo):
        i = by_key.get(t.key)
        rows.append({"template": t, "inspection": i, "required": True,
                     "state": "COMPLETED" if i and i.status == "COMPLETED" else ("IN_PROGRESS" if i else "NOT_STARTED")})
        seen.add(t.key)
    for i in inspections:
        if i.template.key not in seen:
            rows.append({"template": i.template, "inspection": i, "required": i.template.is_required,
                         "state": i.status})
    return rows


def pending_required_counts(org, work_orders) -> dict:
    """{work order id: number of required checklists not completed}, for a page of work orders in two queries
    (the workspace list uses this to avoid per-row lookups). Mirrors ``services.checklist_blockers``."""
    from apps.maintenance.models import MaintenanceCycle

    from .models import ChecklistTemplate
    from .workflow import ACTIVE, COMPLETED

    wos = list(work_orders)
    required = list(ChecklistTemplate.objects.for_organization(org).filter(status=ACTIVE, is_required=True))
    pm_keys = dict(MaintenanceCycle.objects.for_organization(org).filter(
        work_order__in=[w for w in wos if w.source_type == "PREVENTIVE_MAINTENANCE"]).exclude(
        schedule__plan__checklist_key="").values_list("work_order_id", "schedule__plan__checklist_key"))
    if not wos or (not required and not pm_keys):
        return {wo.pk: 0 for wo in wos}
    active_by_key = {str(t.key): t for t in ChecklistTemplate.objects.for_organization(org).filter(
        status=ACTIVE, key__in=set(pm_keys.values()))}
    done = {(wo_id, str(key)) for wo_id, key in Inspection.objects.for_organization(org).filter(
        work_order__in=wos, status=COMPLETED).values_list("work_order_id", "template__key")}
    out = {}
    for wo in wos:
        keys = {str(t.key) for t in required if t.work_type in ("", wo.work_type)}
        if pm_keys.get(wo.pk) in active_by_key:
            keys.add(pm_keys[wo.pk])
        out[wo.pk] = sum(1 for k in keys if (wo.pk, k) not in done)
    return out


def available_templates(org, wo):
    """ACTIVE templates a technician may run on this work order (required or optional, type-matching)."""
    from .workflow import ACTIVE

    return ChecklistTemplate.objects.for_organization(org).filter(status=ACTIVE).filter(
        work_type__in=("", wo.work_type)).order_by("name")
