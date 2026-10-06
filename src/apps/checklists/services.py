"""M08 service layer (templates, inspections, responses, findings, completion).

Callers (API / UI) resolve objects through organization- and site-scoped selectors and check the permission for
the object's site first; this layer enforces the rest: tenant ownership of every reference, template freezing
(historical inspections stay understandable), the state machines, value validation per item type, the completion
rules (required answers, findings for exceptions, required evidence), one audit record per change, all in one
transaction.

Boundary with M06: execution against a work order requires the work order to be IN_PROGRESS and the caller to be
its assignee (or a dispatcher), exactly like M06 execution; ``checklist_blockers`` is the ONLY thing M06 asks M08
(``workorders.services`` calls it from the completion guard and ``closure_blockers``). M08 never changes a
work-order row.
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import Conflict, PermissionDenied, ValidationFailed
from apps.files import services as files
from apps.files.models import Attachment
from apps.rbac import services as rbac

from .models import ChecklistItem, ChecklistTemplate, Finding, Inspection, InspectionResponse
from .workflow import (
    ACTIVE,
    COMPLETED,
    DRAFT,
    IN_PROGRESS,
    INACTIVE,
    INSPECTION_STATUS,
    TEMPLATE_STATUS,
)

TEMPLATE_FIELDS = ["name", "description", "work_type", "is_required", "status", "version"]
ITEM_FIELDS = ["position", "prompt", "guidance", "item_type", "required", "options", "exception_options",
               "min_value", "max_value", "unit", "evidence_required"]
TRUE_WORDS = {"true", "pass", "yes", "1", "ok"}
FALSE_WORDS = {"false", "fail", "no", "0"}
MAX_TEXT = 2000


# --- validation helpers ----------------------------------------------------------------------------------------


def _name(value: str, what: str, minimum: int = 3) -> str:
    value = (value or "").strip()
    if len(value) < minimum:
        raise ValidationFailed(f"{what} of at least {minimum} characters is required.", code="invalid_text")
    return value


def _dec(value, what: str, *, allow_none=True):
    if value in (None, ""):
        if allow_none:
            return None
        raise ValidationFailed(f"{what} is required.", code="invalid_number")
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationFailed(f"{what} must be a number.", code="invalid_number") from exc
    if not d.is_finite():
        raise ValidationFailed(f"{what} must be a finite number.", code="invalid_number")
    if d.as_tuple().exponent < -4 or abs(d) >= Decimal(10) ** 10:
        raise ValidationFailed(f"{what} has too many digits (max 10 before and 4 after the decimal point).",
                               code="invalid_number")
    return d


def _clean_options(values, what: str) -> list[str]:
    if values in (None, ""):
        return []
    if isinstance(values, str):
        values = [v for v in values.replace("\r", "").split("\n")]
    if not isinstance(values, list | tuple):
        raise ValidationFailed(f"{what} must be a list.", code="invalid_options")
    out = []
    for v in values:
        s = str(v).strip()
        if s and s not in out:
            out.append(s[:100])
    return out


def _item_spec(item_type, *, options, exception_options, min_value, max_value, unit):
    """Normalises the type-specific configuration of an item and rejects inconsistent combinations."""
    if item_type not in ChecklistItem.ItemType.values:
        raise ValidationFailed("Unknown item type.", code="invalid_item_type")
    spec = {"options": [], "exception_options": [], "min_value": None, "max_value": None, "unit": ""}
    if item_type == "SELECTION":
        opts = _clean_options(options, "Options")
        if len(opts) < 2:
            raise ValidationFailed("A selection item needs at least two options.", code="invalid_options")
        exc = _clean_options(exception_options, "Exception options")
        if set(exc) - set(opts):
            raise ValidationFailed("Exception options must be among the options.", code="invalid_options")
        spec.update(options=opts, exception_options=exc)
    elif item_type == "NUMERIC":
        lo, hi = _dec(min_value, "Minimum"), _dec(max_value, "Maximum")
        if lo is not None and hi is not None and hi < lo:
            raise ValidationFailed("The maximum cannot be below the minimum.", code="invalid_range")
        spec.update(min_value=lo, max_value=hi, unit=(unit or "").strip()[:20])
    return spec


def _draft_only(template: ChecklistTemplate):
    if template.status != DRAFT:
        raise Conflict("Only a draft checklist can be changed. Create a new version to edit an active one.",
                       code="template_frozen", details={"state": template.status})


def _lock_template(template: ChecklistTemplate) -> ChecklistTemplate:
    return ChecklistTemplate.objects.select_for_update().get(pk=template.pk)


# --- templates ------------------------------------------------------------------------------------------------------


def _check_work_type(work_type: str) -> str:
    from apps.workorders.models import WorkOrder

    work_type = (work_type or "").strip()
    if work_type and work_type not in WorkOrder.WorkType.values:
        raise ValidationFailed("Unknown work type.", code="invalid_work_type")
    return work_type


@transaction.atomic
def create_template(org, *, actor, name: str, description: str = "", work_type: str = "", is_required=False,
                    request=None) -> ChecklistTemplate:
    t = ChecklistTemplate(organization=org, key=uuid.uuid4(), version=1, name=_name(name, "A name"),
                          description=(description or "").strip(), work_type=_check_work_type(work_type),
                          is_required=bool(is_required), created_by=actor)
    t.save()
    audit.record("checklist.created", actor=actor, organization=org, target=t,
                 after=audit.snapshot(t, TEMPLATE_FIELDS), request=request)
    return t


@transaction.atomic
def update_template(template: ChecklistTemplate, *, actor, request=None, **changes) -> ChecklistTemplate:
    t = _lock_template(template)
    _draft_only(t)
    unknown = set(changes) - {"name", "description", "work_type", "is_required"}
    if unknown:
        raise ValidationFailed(f"Fields cannot be edited: {', '.join(sorted(unknown))}.", code="field_not_editable")
    before = audit.snapshot(t, TEMPLATE_FIELDS)
    if "name" in changes:
        t.name = _name(changes["name"], "A name")
    if "description" in changes:
        t.description = (changes["description"] or "").strip()
    if "work_type" in changes:
        t.work_type = _check_work_type(changes["work_type"])
    if "is_required" in changes:
        t.is_required = bool(changes["is_required"])
    t.save()
    after = audit.snapshot(t, TEMPLATE_FIELDS)
    if before != after:
        audit.record("checklist.updated", actor=actor, organization=t.organization, target=t, before=before,
                     after=after, request=request)
    return t


@transaction.atomic
def add_item(template: ChecklistTemplate, *, actor, prompt: str, item_type: str, guidance: str = "",
             required=True, options=None, exception_options=None, min_value=None, max_value=None, unit: str = "",
             evidence_required=False, request=None) -> ChecklistItem:
    t = _lock_template(template)
    _draft_only(t)
    spec = _item_spec(item_type, options=options, exception_options=exception_options, min_value=min_value,
                      max_value=max_value, unit=unit)
    position = (ChecklistItem.objects.for_organization(t.organization).filter(template=t)
                .aggregate(m=Max("position"))["m"] or 0) + 1
    item = ChecklistItem(organization=t.organization, template=t, position=position,
                         prompt=_name(prompt, "A question"), guidance=(guidance or "").strip(),
                         item_type=item_type, required=bool(required), evidence_required=bool(evidence_required),
                         **spec)
    item.save()
    audit.record("checklist.item_added", actor=actor, organization=t.organization, target=t,
                 after=audit.snapshot(item, ITEM_FIELDS), request=request)
    return item


@transaction.atomic
def update_item(item: ChecklistItem, *, actor, request=None, **changes) -> ChecklistItem:
    item = ChecklistItem.objects.select_for_update().select_related("template").get(pk=item.pk)
    t = _lock_template(item.template)
    _draft_only(t)
    unknown = set(changes) - {"prompt", "guidance", "item_type", "required", "options", "exception_options",
                              "min_value", "max_value", "unit", "evidence_required"}
    if unknown:
        raise ValidationFailed(f"Fields cannot be edited: {', '.join(sorted(unknown))}.", code="field_not_editable")
    before = audit.snapshot(item, ITEM_FIELDS)
    item_type = changes.get("item_type", item.item_type)
    spec = _item_spec(
        item_type, options=changes.get("options", item.options),
        exception_options=changes.get("exception_options", item.exception_options),
        min_value=changes.get("min_value", item.min_value), max_value=changes.get("max_value", item.max_value),
        unit=changes.get("unit", item.unit))
    item.item_type = item_type
    for k, v in spec.items():
        setattr(item, k, v)
    if "prompt" in changes:
        item.prompt = _name(changes["prompt"], "A question")
    if "guidance" in changes:
        item.guidance = (changes["guidance"] or "").strip()
    if "required" in changes:
        item.required = bool(changes["required"])
    if "evidence_required" in changes:
        item.evidence_required = bool(changes["evidence_required"])
    item.save()
    after = audit.snapshot(item, ITEM_FIELDS)
    if before != after:
        audit.record("checklist.item_updated", actor=actor, organization=t.organization, target=t, before=before,
                     after=after, request=request)
    return item


def _renumber(template, ordered_items):
    # two passes inside one transaction; the position uniqueness is DEFERRED so this is safe
    for pos, it in enumerate(ordered_items, start=1):
        if it.position != pos:
            ChecklistItem.objects.for_organization(template.organization).filter(pk=it.pk).update(position=pos)


@transaction.atomic
def remove_item(item: ChecklistItem, *, actor, request=None) -> None:
    """Removes ONE item of a DRAFT template (a draft has no inspections). Frozen templates keep every item."""
    item = ChecklistItem.objects.select_related("template").get(pk=item.pk)
    t = _lock_template(item.template)
    _draft_only(t)
    snap = audit.snapshot(item, ITEM_FIELDS)
    item.delete()
    _renumber(t, list(ChecklistItem.objects.for_organization(t.organization).filter(template=t).order_by("position")))
    audit.record("checklist.item_removed", actor=actor, organization=t.organization, target=t, before=snap,
                 request=request)


@transaction.atomic
def reorder_items(template: ChecklistTemplate, ordered_ids: list, *, actor, request=None) -> list[ChecklistItem]:
    t = _lock_template(template)
    _draft_only(t)
    items = {str(i.pk): i for i in ChecklistItem.objects.for_organization(t.organization).filter(template=t)}
    ids = [str(i) for i in ordered_ids]
    if sorted(ids) != sorted(items) or len(set(ids)) != len(ids):
        raise ValidationFailed("The order must list every item of the checklist exactly once.",
                               code="invalid_order")
    before = [str(i.pk) for i in sorted(items.values(), key=lambda i: i.position)]
    _renumber(t, [items[i] for i in ids])
    audit.record("checklist.items_reordered", actor=actor, organization=t.organization, target=t,
                 before={"order": before}, after={"order": ids}, request=request)
    return list(ChecklistItem.objects.for_organization(t.organization).filter(template=t).order_by("position"))


@transaction.atomic
def activate_template(template: ChecklistTemplate, *, actor, request=None) -> ChecklistTemplate:
    t = _lock_template(template)
    TEMPLATE_STATUS.get(t.status, "activate")
    if not ChecklistItem.objects.for_organization(t.organization).filter(template=t).exists():
        raise Conflict("Add at least one item before activating the checklist.", code="template_empty")
    previous = list(ChecklistTemplate.objects.select_for_update().for_organization(t.organization).filter(
        key=t.key, status=ACTIVE).exclude(pk=t.pk))
    for old in previous:  # the new version replaces the active one
        old.status = INACTIVE
        old.save(update_fields=["status", "updated_at"])
        audit.record("checklist.deactivated", actor=actor, organization=t.organization, target=old,
                     metadata={"replaced_by_version": t.version}, request=request)
    TEMPLATE_STATUS.apply(t, "activate")
    t.activated_at = timezone.now()
    t.save()
    audit.record("checklist.activated", actor=actor, organization=t.organization, target=t,
                 after={"status": t.status, "version": t.version}, request=request)
    return t


@transaction.atomic
def deactivate_template(template: ChecklistTemplate, *, actor, request=None) -> ChecklistTemplate:
    t = _lock_template(template)
    TEMPLATE_STATUS.apply(t, "deactivate")
    t.save()
    audit.record("checklist.deactivated", actor=actor, organization=t.organization, target=t,
                 after={"status": t.status}, request=request)
    return t


@transaction.atomic
def new_version(template: ChecklistTemplate, *, actor, request=None) -> ChecklistTemplate:
    """Copies a frozen template into a new DRAFT version (same ``key``). Inspections keep pointing at the version
    they were executed with."""
    src = _lock_template(template)
    if src.status == DRAFT:
        raise Conflict("A draft can be edited directly; versioning applies to active or inactive checklists.",
                       code="already_draft")
    org = src.organization
    if ChecklistTemplate.objects.for_organization(org).filter(key=src.key, status=DRAFT).exists():
        raise Conflict("A draft version of this checklist already exists.", code="draft_exists")
    latest = ChecklistTemplate.objects.for_organization(org).filter(key=src.key).aggregate(m=Max("version"))["m"]
    t = ChecklistTemplate(organization=org, key=src.key, version=latest + 1, name=src.name,
                          description=src.description, work_type=src.work_type, is_required=src.is_required,
                          created_by=actor)
    t.save()
    for it in ChecklistItem.objects.for_organization(org).filter(template=src).order_by("position"):
        ChecklistItem(organization=org, template=t, position=it.position, prompt=it.prompt, guidance=it.guidance,
                      item_type=it.item_type, required=it.required, options=it.options,
                      exception_options=it.exception_options, min_value=it.min_value, max_value=it.max_value,
                      unit=it.unit, evidence_required=it.evidence_required).save()
    audit.record("checklist.version_created", actor=actor, organization=org, target=t,
                 after={"version": t.version, "from_version": src.version}, request=request)
    return t


# --- applicability and work-order integration ---------------------------------------------------------------------


def required_templates(wo):
    """ACTIVE templates this work order must complete: the required ones that apply to its type, plus (M04) the
    checklist its preventive-maintenance plan names. M08 stays the only authority on blocking / execution."""
    from django.db.models import Q

    cond = Q(is_required=True, work_type__in=("", wo.work_type))
    plan_key = maintenance_checklist_key(wo)
    if plan_key:
        cond |= Q(key=plan_key)
    return ChecklistTemplate.objects.for_organization(wo.organization).filter(status=ACTIVE).filter(cond)


def maintenance_checklist_key(wo) -> str:
    """M04 contract (lazy import): the checklist key of the PM plan that generated this work order, or ''."""
    if getattr(wo, "source_type", "") != "PREVENTIVE_MAINTENANCE":
        return ""
    from apps.maintenance import services as maintenance

    return maintenance.required_checklist_key(wo)


def checklist_blockers(wo) -> list[str]:
    """Reasons the work order's required checklists are not satisfied. Empty = nothing blocks.

    Reads PERSISTED inspection state only. A required template (ACTIVE, matching the work type) needs a COMPLETED
    inspection of any version of that checklist on this order; any unfinished inspection of a required checklist
    also blocks (so deactivating a template mid-way cannot be used to skip it)."""
    orgq = Inspection.objects.for_organization(wo.organization).filter(work_order=wo).select_related("template")
    inspections = list(orgq)
    blockers = []
    for t in required_templates(wo):
        done = [i for i in inspections if i.template.key == t.key and i.status == COMPLETED]
        if not done:
            running = [i for i in inspections if i.template.key == t.key]
            blockers.append(f"Required checklist '{t.name}' is not completed." if running
                            else f"Required checklist '{t.name}' has not been started.")
    for i in inspections:
        if i.status != COMPLETED and i.template.is_required and i.template.status != ACTIVE:
            blockers.append(f"Required checklist '{i.template.name}' is not completed.")
    return list(dict.fromkeys(blockers))


# --- inspections ----------------------------------------------------------------------------------------------------


def _assert_executor(wo, membership, site_id):
    if not rbac.has_permission(membership, "inspection.execute", site_id):
        raise PermissionDenied("You cannot execute inspections at this site.", code="inspection_forbidden")
    if wo is not None:
        from apps.workorders import services as wos

        wos.assert_can_execute(wo, membership)  # assignee or dispatcher, same rule as M06 execution


def _assert_wo_running(wo):
    if wo is not None and wo.status != "IN_PROGRESS":
        raise Conflict("The work order must be in progress (resume it if it is on hold) to record inspections.",
                       code="work_order_not_in_progress", details={"state": wo.status})


@transaction.atomic
def start_inspection(org, *, template: ChecklistTemplate, membership, actor, work_order=None, asset=None,
                     request=None) -> Inspection:
    from apps.workorders.models import WorkOrder

    if template.organization_id != org.pk:
        raise ValidationFailed("Checklist belongs to a different organization.", code="cross_tenant_template")
    template = _lock_template(template)
    if template.status != ACTIVE:
        raise Conflict("Only an active checklist can be used for an inspection.", code="template_not_active",
                       details={"state": template.status})
    if work_order is not None:
        if work_order.organization_id != org.pk:
            raise ValidationFailed("Work order belongs to a different organization.", code="cross_tenant_work_order")
        work_order = WorkOrder.objects.select_related("asset", "site").select_for_update(of=("self",)).get(
            pk=work_order.pk)
        asset = work_order.asset
        if template.work_type and template.work_type != work_order.work_type:
            raise ValidationFailed("This checklist does not apply to this type of work.",
                                   code="template_not_applicable")
        _assert_wo_running(work_order)
    elif asset is None or asset.organization_id != org.pk:
        raise ValidationFailed("An asset of this organization is required.", code="cross_tenant_asset")
    elif asset.status in ("RETIRED", "DISPOSED"):
        raise Conflict("Retired or disposed assets cannot be inspected.", code="asset_terminal")
    _assert_executor(work_order, membership, asset.site_id)
    if work_order is not None and Inspection.objects.for_organization(org).filter(
            work_order=work_order, template__key=template.key).exists():
        raise Conflict("This checklist has already been started for the work order.", code="inspection_exists")
    insp = Inspection(organization=org, template=template, work_order=work_order, asset=asset, site=asset.site,
                      started_by=membership)
    try:
        with transaction.atomic():
            insp.save()
    except IntegrityError as exc:  # concurrent duplicate start
        raise Conflict("This checklist has already been started for the work order.",
                       code="inspection_exists") from exc
    audit.record("inspection.started", actor=actor, organization=org, target=insp,
                 after={"template": str(template.pk), "version": template.version,
                        "work_order": work_order.number if work_order else None, "asset": asset.asset_tag},
                 request=request)
    return insp


def _lock_inspection(inspection: Inspection) -> Inspection:
    """Locks the work order (if any) first, then the inspection: one lock order everywhere."""
    if inspection.work_order_id:
        from apps.workorders.models import WorkOrder

        WorkOrder.objects.select_for_update().get(pk=inspection.work_order_id)
    return Inspection.objects.select_related("template", "work_order", "asset", "site").select_for_update(
        of=("self",)).get(pk=inspection.pk)


def _writable(insp: Inspection, membership):
    if insp.status != IN_PROGRESS:
        raise Conflict("This inspection is completed and can no longer be changed.", code="inspection_locked",
                       details={"state": insp.status})
    _assert_wo_running(insp.work_order)
    _assert_executor(insp.work_order, membership, insp.site_id)


def _parse_answer(item: ChecklistItem, raw):
    """Returns (value_text, value_number, value_bool, is_exception) or raises ValueError(message)."""
    t = item.item_type
    if t == "TEXT":
        text = str(raw).strip()
        if not text:
            raise ValueError("Enter an answer.")
        if len(text) > MAX_TEXT:
            raise ValueError(f"Keep the answer under {MAX_TEXT} characters.")
        return text, None, None, False
    if t == "NUMERIC":
        if isinstance(raw, bool):
            raise ValueError("Enter a number.")
        try:
            d = _dec(raw, "The value", allow_none=False)
        except ValidationFailed as exc:
            raise ValueError(exc.message) from exc
        out = (item.min_value is not None and d < item.min_value) or (item.max_value is not None and d > item.max_value)
        return "", d, None, bool(out)
    if t == "BOOLEAN":
        if isinstance(raw, bool):
            val = raw
        else:
            word = str(raw).strip().lower()
            if word in TRUE_WORDS:
                val = True
            elif word in FALSE_WORDS:
                val = False
            else:
                raise ValueError("Choose pass or fail.")
        return "", None, val, not val
    if t == "SELECTION":
        choice = str(raw).strip()
        if choice not in item.options:
            raise ValueError("Choose one of the listed options.")
        return choice, None, None, choice in item.exception_options
    raise ValueError("Unknown item type.")


@transaction.atomic
def save_responses(inspection: Inspection, answers: dict, *, membership, actor, request=None) -> list[InspectionResponse]:
    """Upserts answers (``{item_id: value}``). Blank values leave the stored answer untouched. All-or-nothing:
    any invalid value rejects the whole save with per-item messages in ``details['errors']``."""
    insp = _lock_inspection(inspection)
    _writable(insp, membership)
    items = {str(i.pk): i for i in ChecklistItem.objects.for_organization(insp.organization).filter(
        template=insp.template)}
    errors, parsed = {}, {}
    for key, raw in (answers or {}).items():
        key = str(key)
        item = items.get(key)
        if item is None:
            errors[key] = "This question is not part of the checklist."
            continue
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        try:
            parsed[key] = _parse_answer(item, raw)
        except ValueError as exc:
            errors[key] = str(exc)
    if errors:
        raise ValidationFailed("Some answers are not valid.", code="invalid_responses", details={"errors": errors})
    now = timezone.now()
    saved = []
    existing = {str(r.item_id): r for r in InspectionResponse.objects.for_organization(insp.organization).filter(
        inspection=insp)}
    for key, (text, number, boolean, exception) in parsed.items():
        row = existing.get(key) or InspectionResponse(organization=insp.organization, inspection=insp,
                                                      item=items[key])
        row.value_text, row.value_number, row.value_bool, row.is_exception = text, number, boolean, exception
        row.answered_by, row.answered_at = membership, now
        row.save()
        saved.append(row)
    if saved:
        audit.record("inspection.responses_saved", actor=actor, organization=insp.organization, target=insp,
                     metadata={"items": len(saved), "exceptions": sum(1 for r in saved if r.is_exception)},
                     request=request)
    return saved


@transaction.atomic
def add_finding(inspection: Inspection, *, description: str, severity: str = "MEDIUM", item=None, membership,
                actor, request=None) -> Finding:
    insp = _lock_inspection(inspection)
    _writable(insp, membership)
    if severity not in Finding.Severity.values:
        raise ValidationFailed("Unknown severity.", code="invalid_severity")
    if item is not None and (item.organization_id != insp.organization_id or item.template_id != insp.template_id):
        raise ValidationFailed("The question is not part of this inspection's checklist.", code="item_mismatch")
    f = Finding(organization=insp.organization, inspection=insp, item=item, work_order=insp.work_order,
                asset=insp.asset, site=insp.site, description=_name(description, "A description"),
                severity=severity, created_by=actor)
    f.save()
    audit.record("inspection.finding_added", actor=actor, organization=insp.organization, target=f,
                 after={"severity": severity, "item": str(item.pk) if item else None,
                        "inspection": str(insp.pk)}, request=request)
    return f


@transaction.atomic
def resolve_finding(finding: Finding, *, notes: str, actor, request=None) -> Finding:
    """Caller holds ``inspection.review`` for the finding's site."""
    f = Finding.objects.select_for_update().get(pk=finding.pk)
    if f.status != Finding.Status.OPEN:
        raise Conflict("This finding is already resolved.", code="invalid_transition",
                       details={"state": f.status, "action": "resolve"})
    f.status, f.resolved_by, f.resolved_at = Finding.Status.RESOLVED, actor, timezone.now()
    f.resolution_notes = _name(notes, "Resolution notes")
    f.save()
    audit.record("inspection.finding_resolved", actor=actor, organization=f.organization, target=f,
                 after={"status": f.status}, request=request)
    return f


def evidence_for_inspection(insp: Inspection):
    """Files attached to the answers and the findings of an inspection."""
    org = insp.organization
    resp_ids = [str(i) for i in InspectionResponse.objects.for_organization(org).filter(
        inspection=insp).values_list("pk", flat=True)]
    find_ids = [str(i) for i in Finding.objects.for_organization(org).filter(inspection=insp).values_list(
        "pk", flat=True)]
    cts = ContentType.objects.get_for_models(InspectionResponse, Finding).values()
    return Attachment.objects.for_organization(org).select_related("content_type", "uploaded_by").filter(
        content_type__in=cts, object_id__in=resp_ids + find_ids).order_by("created_at")


@transaction.atomic
def add_response_evidence(inspection: Inspection, item: ChecklistItem, uploaded, *, description: str = "",
                          membership, actor, request=None):
    """Attaches a file to the (already recorded) answer for ``item``."""
    insp = _lock_inspection(inspection)
    _writable(insp, membership)
    response = InspectionResponse.objects.for_organization(insp.organization).filter(
        inspection=insp, item_id=item.pk).first()
    if response is None:
        raise Conflict("Answer the question before attaching evidence to it.", code="answer_first")
    att = files.attach(uploaded, target=response, organization=insp.organization, user=actor,
                       description=description, request=request)
    audit.record("inspection.evidence_added", actor=actor, organization=insp.organization, target=insp,
                 metadata={"item": str(item.pk), "file": att.original_name, "attachment": str(att.pk)},
                 request=request)
    return att


@transaction.atomic
def add_finding_evidence(finding: Finding, uploaded, *, description: str = "", membership, actor, request=None):
    insp = _lock_inspection(finding.inspection)
    _writable(insp, membership)
    att = files.attach(uploaded, target=finding, organization=insp.organization, user=actor,
                       description=description, request=request)
    audit.record("inspection.evidence_added", actor=actor, organization=insp.organization, target=insp,
                 metadata={"finding": str(finding.pk), "file": att.original_name, "attachment": str(att.pk)},
                 request=request)
    return att


def completion_issues(insp: Inspection) -> list[str]:
    """What still prevents the inspection from being completed (reads persisted rows only)."""
    org = insp.organization
    items = list(ChecklistItem.objects.for_organization(org).filter(template=insp.template).order_by("position"))
    responses = {r.item_id: r for r in InspectionResponse.objects.for_organization(org).filter(inspection=insp)}
    finding_items = set(Finding.objects.for_organization(org).filter(inspection=insp).values_list("item_id",
                                                                                                  flat=True))
    evidence_ids = set(Attachment.objects.for_organization(org).filter(
        content_type=ContentType.objects.get_for_model(InspectionResponse),
        object_id__in=[str(r.pk) for r in responses.values()]).values_list("object_id", flat=True))
    issues = []
    for it in items:
        r = responses.get(it.pk)
        if r is None:
            if it.required:
                issues.append(f"Question {it.position} is required: {it.prompt}")
            continue
        if r.is_exception and it.pk not in finding_items:
            issues.append(f"Question {it.position} is an exception: record a finding.")
        if it.evidence_required and str(r.pk) not in evidence_ids:
            issues.append(f"Question {it.position} needs evidence (photo / file).")
    return issues


@transaction.atomic
def complete_inspection(inspection: Inspection, *, membership, actor, summary: str = "", request=None) -> Inspection:
    insp = _lock_inspection(inspection)
    INSPECTION_STATUS.get(insp.status, "complete")  # duplicate completion -> 409
    _assert_wo_running(insp.work_order)
    _assert_executor(insp.work_order, membership, insp.site_id)
    issues = completion_issues(insp)
    if issues:
        raise Conflict("The inspection cannot be completed yet: " + " ".join(issues), code="inspection_incomplete",
                       details={"issues": issues})
    INSPECTION_STATUS.apply(insp, "complete")
    insp.completed_by, insp.completed_at = membership, timezone.now()
    insp.summary = (summary or "").strip()[:2000]
    insp.save()
    audit.record("inspection.completed", actor=actor, organization=insp.organization, target=insp,
                 after={"status": insp.status, "findings": insp.findings.count()}, request=request)
    return insp
