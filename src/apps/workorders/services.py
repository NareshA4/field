from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import Conflict, InvalidTransition, NotFound, PermissionDenied, ValidationFailed
from apps.core.sequences import next_number
from apps.files.models import Attachment
from apps.rbac import services as rbac
from apps.sites.models import Site
from apps.tenancy.models import Membership

from .assignment_rules import ACTIVE_ASSIGNMENT_STATES, validate_assignment
from .models import (
    ClosureApproval,
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderEvent,
    WorkOrderEvidence,
    WorkOrderLabor,
    WorkOrderMaterial,
)

TRANSITIONS = {
    WorkOrder.Status.DRAFT: {WorkOrder.Status.PLANNED},
    WorkOrder.Status.PLANNED: {WorkOrder.Status.ASSIGNED},
    WorkOrder.Status.ASSIGNED: {WorkOrder.Status.DISPATCHED},
    WorkOrder.Status.DISPATCHED: {WorkOrder.Status.IN_PROGRESS},
    WorkOrder.Status.IN_PROGRESS: {WorkOrder.Status.ON_HOLD, WorkOrder.Status.COMPLETED},
    WorkOrder.Status.ON_HOLD: {WorkOrder.Status.IN_PROGRESS},
    WorkOrder.Status.COMPLETED: {WorkOrder.Status.SUPERVISOR_REVIEW},
    WorkOrder.Status.SUPERVISOR_REVIEW: {WorkOrder.Status.CLOSED, WorkOrder.Status.IN_PROGRESS},
}

EVIDENCE_REQUIRED_TYPES = (WorkOrder.WorkType.CORRECTIVE, WorkOrder.WorkType.INSTALLATION)
def allowed_actions(wo, membership):
    """Return the current user's actionable workflow controls for this order."""
    def permitted(code):
        return bool(membership and rbac.has_permission(membership, code, wo.site))

    is_assignee = bool(membership and wo.assigned_to_id == membership.pk)
    actions = []
    if wo.status in (WorkOrder.Status.DRAFT, WorkOrder.Status.PLANNED):
        if permitted("work_order.update"):
            actions.append({"operation": "edit", "label": "Edit details", "kind": "link"})
    if wo.status == WorkOrder.Status.DRAFT and permitted("work_order.plan"):
        actions.append({"operation": "plan", "label": "Plan work", "kind": "link",
                        "next_status": WorkOrder.Status.PLANNED})
    elif wo.status == WorkOrder.Status.PLANNED and permitted("work_order.assign"):
        actions.append({"operation": "assign", "label": "Assign technician", "kind": "link",
                        "next_status": WorkOrder.Status.ASSIGNED})
    elif wo.status == WorkOrder.Status.ASSIGNED and permitted("work_order.dispatch"):
        actions.append({"operation": "dispatch", "label": "Dispatch", "kind": "post",
                        "confirm": "Dispatch this work order?", "next_status": WorkOrder.Status.DISPATCHED})
    elif wo.status == WorkOrder.Status.DISPATCHED and is_assignee and permitted("work_order.start"):
        actions.append({"operation": "start", "label": "Start work", "kind": "post",
                        "confirm": "Start this work order?", "next_status": WorkOrder.Status.IN_PROGRESS})
    elif wo.status == WorkOrder.Status.IN_PROGRESS:
        if permitted("work_order.hold") and (is_assignee or permitted("work_order.review")):
            actions.append({"operation": "hold", "label": "Put on hold", "kind": "link",
                            "next_status": WorkOrder.Status.ON_HOLD})
        if is_assignee and permitted("work_order.complete"):
            actions.append({"operation": "complete", "label": "Complete work", "kind": "link",
                            "next_status": WorkOrder.Status.COMPLETED})
    elif wo.status == WorkOrder.Status.ON_HOLD and is_assignee and permitted("work_order.start"):
        actions.append({"operation": "resume", "label": "Resume work", "kind": "post",
                        "next_status": WorkOrder.Status.IN_PROGRESS})
    elif wo.status == WorkOrder.Status.COMPLETED and is_assignee and permitted("work_order.complete"):
        actions.append({"operation": "submit_for_review", "label": "Submit for review", "kind": "post",
                        "confirm": "Submit this completed work order for supervisor review?",
                        "next_status": WorkOrder.Status.SUPERVISOR_REVIEW})
    elif wo.status == WorkOrder.Status.SUPERVISOR_REVIEW and (
            permitted("work_order.review") or permitted("work_order.close")):
        if permitted("work_order.close"):
            actions.append({"operation": "close", "label": "Approve and close", "kind": "link",
                            "confirm": "Approve and close this work order?", "next_status": WorkOrder.Status.CLOSED})
        if permitted("work_order.review"):
            actions.append({"operation": "return_for_rework", "label": "Return for rework", "kind": "link",
                            "next_status": WorkOrder.Status.IN_PROGRESS})
    return actions


def _need(membership, code, site):
    if (not membership or not membership.is_active or membership.organization_id != site.organization_id
            or not rbac.has_permission(membership, code, site)):
        raise PermissionDenied("You do not have permission to perform this work order action.")


def _transition(work_order, target):
    if target not in TRANSITIONS.get(work_order.status, set()):
        raise InvalidTransition(f"Cannot move work order from {work_order.status} to {target}.",
                                code="invalid_work_order_transition")


@transaction.atomic
def transition_work_order(work_order, target_status, *, membership, actor, permission, action,
                          changes=None, reason="", metadata=None, request=None, expected_version=None):
    """The single persistence boundary for work-order status transitions.

    Status-specific services validate their business prerequisites, then delegate
    status, version, event, and audit persistence to this function.
    """
    wo = WorkOrder.objects.select_for_update(of=("self",)).select_related(
        "site", "assigned_to__user").get(pk=work_order.pk)
    _need(membership, permission, wo.site)
    if expected_version is not None and int(expected_version) != wo.version:
        raise Conflict("This work order changed since you loaded it. Refresh and retry.",
                       code="work_order_version_conflict")
    _transition(wo, target_status)
    before = _snapshot(wo)
    changes = changes or {}
    for name, value in changes.items():
        setattr(wo, name, value)
    wo.status = target_status
    update_fields = set(changes) | {"status", "updated_at"}
    wo._workflow_transition_write = True
    try:
        wo.save(update_fields=update_fields)
    finally:
        del wo._workflow_transition_write
    _record(wo, actor=actor, action=action, before=before, notes=reason,
            metadata=metadata, request=request)
    return wo


def _snapshot(wo):
    return {"status": wo.status, "title": wo.title, "description": wo.description,
            "source_type": wo.source_type, "source_reference": wo.source_reference,
            "asset_reference": wo.asset_reference, "component_reference": wo.component_reference,
            "service_request_reference": wo.service_request_reference,
            "site_id": str(wo.site_id), "asset_id": str(wo.asset_id) if wo.asset_id else None,
            "component_id": str(wo.component_id) if wo.component_id else None,
            "source_request_id": str(wo.source_request_id) if wo.source_request_id else None,
            "work_type": wo.work_type, "priority": wo.priority,
            "due_at": wo.due_at.isoformat() if wo.due_at else None,
            "estimated_hours": str(wo.estimated_hours) if wo.estimated_hours is not None else None,
            "assigned_to": str(wo.assigned_to_id) if wo.assigned_to_id else None,
            "closed_by": str(wo.closed_by_id) if wo.closed_by_id else None, "version": wo.version,
            "planned_start": wo.planned_start.isoformat() if wo.planned_start else None,
            "planned_end": wo.planned_end.isoformat() if wo.planned_end else None}


def _validate_schedule_window(start, end, due_at, estimated_hours):
    if start and end and end <= start:
        raise ValidationFailed("Planned end must be later than planned start.", code="invalid_schedule")
    if due_at and end and due_at <= end:
        raise ValidationFailed("Due date must be later than planned end.", code="due_not_after_end")
    if estimated_hours is not None and Decimal(estimated_hours) <= 0:
        raise ValidationFailed("Estimated hours must be greater than zero.", code="estimate_not_positive")
    if start and end and estimated_hours is not None:
        available_hours = Decimal(str((end - start).total_seconds())) / Decimal("3600")
        if Decimal(estimated_hours) > available_hours:
            raise ValidationFailed("Estimated hours cannot exceed the planned start-to-end window.",
                                   code="estimate_exceeds_schedule")


def _validate_linked_priority(work_order, priority):
    if not work_order.source_request_id:
        return
    expected = {
        "LOW": WorkOrder.Priority.LOW,
        "MEDIUM": WorkOrder.Priority.MEDIUM,
        "HIGH": WorkOrder.Priority.HIGH,
        "CRITICAL": WorkOrder.Priority.CRITICAL,
    }.get(work_order.source_request.severity)
    if priority != expected:
        raise ValidationFailed("Priority must match the linked approved incident or service request.",
                               code="source_priority_mismatch")


def _record(wo, *, actor, action, before=None, notes="", metadata=None, request=None):
    now = timezone.now()
    WorkOrder.objects.filter(pk=wo.pk).update(version=F("version") + 1, updated_at=now)
    wo.version += 1
    wo.updated_at = now
    WorkOrderEvent.objects.create(organization=wo.organization, work_order=wo, actor=actor,
                                  assigned_to=wo.assigned_to, action=action,
                                  from_status=(before or {}).get("status", ""),
                                  to_status=wo.status, reason=notes, metadata=metadata or {})
    audit.record(f"work_order.{action}", actor=actor, organization=wo.organization, target=wo,
                 before=before, after=_snapshot(wo), metadata=metadata or {}, request=request)
    _notify_transition(wo, action, actor)


def _sync_incident_request(wo, action, actor, request=None):
    """Mirror M06 lifecycle events to an M05 source incident/request when linked."""
    if wo.source_type not in (WorkOrder.SourceType.REQUEST, WorkOrder.SourceType.INCIDENT) or not wo.source_reference:
        return
    from apps.incidents import services as incident_services
    from apps.incidents.models import ServiceRequest

    source = ServiceRequest.objects.for_organization(wo.organization).select_for_update().filter(
        number=wo.source_reference).first()
    if source is not None:
        callback = getattr(incident_services, f"on_work_order_{action}")
        callback(source, actor=actor, request=request)


def _notify_transition(wo, action, actor):
    recipients = set()
    title = body = ""
    if action in ("assigned", "dispatched") and wo.assigned_to_id:
        recipients.add(wo.assigned_to.user_id)
        title = "Work order assigned" if action == "assigned" else "Work order dispatched"
        body = f"{wo.order_number}: {wo.title}"
    elif action == "held":
        recipients.add(wo.created_by_id)
        title, body = "Work order on hold", f"{wo.order_number}: {wo.hold_reason or wo.title}"
    elif action == "completed":
        title, body = "Work order awaiting review", f"{wo.order_number}: {wo.title} is completed."
        recipients.update(m.user_id for m in Membership.objects.for_organization(wo.organization).filter(
            status=Membership.Status.ACTIVE).select_related("user")
            if rbac.has_permission(m, "work_order.review", wo.site))
    elif action == "review_rejected" and wo.assigned_to_id:
        recipients.add(wo.assigned_to.user_id)
        title, body = "Work order returned for rework", f"{wo.order_number}: {wo.supervisor_notes}"
    elif action == "closed":
        recipients.add(wo.created_by_id)
        title, body = "Work order closed", f"{wo.order_number}: {wo.title} was approved and closed."
    recipients.discard(getattr(actor, "pk", None))
    if recipients:
        from apps.notifications.services import notify
        notify(wo.organization, recipients, title=title, body=body,
               link=f"/app/work-orders/{wo.pk}/", source="work_order")


def get_work_order(membership, pk):
    try:
        return WorkOrder.objects.for_organization(membership.organization).select_related(
            "site", "assigned_to__user", "created_by").get(pk=pk)
    except WorkOrder.DoesNotExist as exc:
        raise NotFound("Work order not found.") from exc


@transaction.atomic
def create_work_order(organization, *, site_id=None, title=None, actor, membership=None, request=None, **fields):
    from django.conf import settings
    if membership is None and actor is not None:
        membership = Membership.objects.filter(organization=organization, user=actor, status=Membership.Status.ACTIVE).first()
    source_request = fields.pop("source_request", None)
    source_asset = fields.pop("asset", None)
    component = fields.pop("component", None)
    if site_id is None and source_asset is not None:
        site_id = source_asset.site_id
    if source_request is not None:
        from apps.incidents.models import ServiceRequest
        from apps.incidents.workflow import APPROVED

        source_request = ServiceRequest.objects.select_for_update().select_related("asset", "site").filter(
            organization=organization, pk=source_request.pk).first()
        if source_request is None:
            raise NotFound("The source incident or request was not found.")
        if source_request.status != APPROVED:
            raise Conflict("Only an approved incident or service request can create a work order.",
                           code="source_request_not_approved")
        if site_id is not None and str(site_id) != str(source_request.site_id):
            raise ValidationFailed("The work-order site must match the approved source record.",
                                   code="source_site_mismatch")
        site_id = source_request.site_id
        if source_asset is not None and source_asset.pk != source_request.asset_id:
            raise ValidationFailed("The work-order asset must match the source incident or request.",
                                   code="source_asset_mismatch")
        source_asset = source_request.asset
        inherited_priority = {
            "LOW": WorkOrder.Priority.LOW,
            "MEDIUM": WorkOrder.Priority.MEDIUM,
            "HIGH": WorkOrder.Priority.HIGH,
            "CRITICAL": WorkOrder.Priority.CRITICAL,
        }[source_request.severity]
        requested_priority = fields.get("priority")
        if requested_priority and requested_priority != inherited_priority:
            raise ValidationFailed("Priority must match the approved source record.",
                                   code="source_priority_mismatch")
        source_type = (WorkOrder.SourceType.INCIDENT
                       if source_request.kind == ServiceRequest.Kind.INCIDENT
                       else WorkOrder.SourceType.REQUEST)
        requested_source_type = fields.get("source_type")
        if requested_source_type and requested_source_type != source_type:
            raise ValidationFailed("The selected source type does not match the linked record.",
                                   code="source_type_mismatch")
        source_reference = source_request.number
        if WorkOrder.objects.for_organization(organization).filter(
                source_type=source_type, source_reference=source_reference).exists():
            raise Conflict("A work order is already linked to this incident or request.",
                           code="source_work_order_exists")
        fields.update({
            "source_type": source_type,
            "source_reference": source_reference,
            "source_request": source_request,
            "source_id": source_request.pk,
            "service_request_reference": (source_reference
                if source_type == WorkOrder.SourceType.REQUEST else ""),
            "asset_reference": source_asset.asset_tag,
            "priority": inherited_priority,
        })
        title = title or f"{source_request.number}: {source_request.title}"[:200]
        fields["description"] = source_request.description

    site = Site.objects.for_organization(organization).filter(pk=site_id).first()
    if site is None:
        raise NotFound("Site not found.")
    _need(membership, "work_order.create", site)
    if membership.organization_id != organization.pk:
        raise NotFound("Site not found.")
    if site.status != Site.Status.ACTIVE:
        raise ValidationFailed("New work orders cannot be created for an inactive site.", code="site_inactive")
    title = (title or "").strip()
    if not title:
        raise ValidationFailed("A work order title is required.")
    source_type = fields.get("source_type", WorkOrder.SourceType.MANUAL)
    if source_type in (WorkOrder.SourceType.REQUEST, WorkOrder.SourceType.INCIDENT) and source_request is None:
        raise ValidationFailed("Select a valid approved incident or service request.",
                               code="source_relationship_required")
    if source_type == WorkOrder.SourceType.MANUAL and source_request is not None:
        raise ValidationFailed("Manual work orders cannot be linked to an incident or request.",
                               code="source_type_mismatch")
    source_reference = (fields.get("source_reference") or "").strip()
    if source_type == WorkOrder.SourceType.PM and not source_reference:
        raise ValidationFailed("A preventive-maintenance schedule reference is required.", code="pm_reference_required")
    if source_type == WorkOrder.SourceType.PM and WorkOrder.objects.for_organization(organization).filter(
            source_type=WorkOrder.SourceType.PM, source_reference=source_reference).exclude(
                status=WorkOrder.Status.CANCELLED).exists():
        raise Conflict("A work order already exists for this preventive-maintenance schedule.",
                       code="pm_work_order_exists")
    if source_type == WorkOrder.SourceType.MANUAL and source_reference:
        raise ValidationFailed("Manual work orders cannot contain a linked source reference.",
                               code="manual_source_reference")
    if source_type == WorkOrder.SourceType.MANUAL and fields.get("service_request_reference") and not source_reference:
        source_type = WorkOrder.SourceType.REQUEST
        source_reference = fields["service_request_reference"].strip()
        fields["source_type"] = source_type
    if source_type != WorkOrder.SourceType.MANUAL and not source_reference:
        raise ValidationFailed("Add the reference for the selected work-order source.", code="source_reference_required")
    fields["source_reference"] = source_reference
    if source_type == WorkOrder.SourceType.REQUEST:
        fields["service_request_reference"] = source_reference
    start, end, due_at = fields.get("planned_start"), fields.get("planned_end"), fields.get("due_at")
    _validate_schedule_window(start, end, due_at, fields.get("estimated_hours"))
    schedule_values = (start, end, due_at, fields.get("estimated_hours"))
    if any(value is not None for value in schedule_values) and not all(value is not None for value in schedule_values):
        raise ValidationFailed("Provide planned start, planned end, due date, and estimated hours together, or leave them blank and plan the Draft later.",
                               code="incomplete_draft_schedule")
    if source_asset is None:
        raise ValidationFailed("Asset is required.", code="asset_required")
    from apps.assets.models import Asset, AssetComponent
    source_asset = Asset.objects.for_organization(organization).filter(pk=source_asset.pk).first()
    if source_asset is None:
        raise NotFound("Asset not found.")
    if source_asset.status != Asset.Status.ACTIVE:
        raise ValidationFailed("Selected asset is inactive.", code="asset_inactive")
    if source_asset.site_id != site.pk:
        raise ValidationFailed("Invalid asset for the selected site.", code="asset_site_mismatch")
    if component is not None:
        if (component.organization_id != organization.pk or component.status != Asset.Status.ACTIVE or
                not AssetComponent.objects.filter(organization=organization, parent=source_asset,
                                                  child=component,
                                                  relationship_type=AssetComponent.Relationship.COMPONENT).exists()):
            raise ValidationFailed("Invalid component for the selected asset.", code="component_asset_mismatch")
    fields["asset"] = source_asset
    fields["component"] = component
    fields["asset_reference"] = source_asset.asset_tag
    fields["component_reference"] = component.asset_tag if component else ""
    provisional = fields.get("provisional_checklist", [])
    if provisional and not getattr(settings, "WORKORDER_PROVISIONAL_CHECKLIST_ENABLED", False):
        raise ValidationFailed("Provisional checklist notes are disabled.", code="provisional_checklist_disabled")
    if not isinstance(provisional, list) or any(not isinstance(item, dict) or
            not str(item.get("label", "")).strip() for item in provisional):
        raise ValidationFailed("Provisional checklist notes must each include a label.")
    wo = WorkOrder(organization=organization, site=site,
                   order_number=next_number(organization, "work_order", "WO"),
                   title=title, created_by=actor, **fields)
    from .checklist_adapter import configure_requirement
    configure_requirement(wo)
    try:
        wo.full_clean()
        wo.save()
    except ValidationError as exc:
        raise ValidationFailed("Work order is invalid.", details={"fields": exc.message_dict if hasattr(exc, "message_dict") else exc.messages}) from exc
    _record(wo, actor=actor, action="created", request=request)
    return wo


@transaction.atomic
def update_work_order(wo, *, membership, actor, request=None, **values):
    """Edit mutable work-order details; lifecycle status is changed only by workflow services."""
    # Lock only the work-order row: assigned_to is nullable, and PostgreSQL
    # rejects FOR UPDATE on the nullable side of a select_related join.
    wo = WorkOrder.objects.select_for_update(of=("self",)).select_related("site", "assigned_to").get(pk=wo.pk)
    _need(membership, "work_order.update", wo.site)
    if wo.status not in (WorkOrder.Status.DRAFT, WorkOrder.Status.PLANNED):
        raise Conflict("Only Draft or Planned work orders can be edited here.", code="work_order_locked")
    unknown = set(values) - {
        "title", "description", "asset_reference", "asset", "component", "component_reference",
        "work_type", "priority", "planned_start", "planned_end",
        "due_at", "estimated_hours", "site",
    }
    if unknown:
        raise ValidationFailed("Unsupported work-order fields.", details={"fields": sorted(unknown)})
    if wo.status == WorkOrder.Status.PLANNED:
        planning_fields = {"work_type", "priority", "planned_start", "planned_end", "due_at", "estimated_hours"}
        forbidden = set(values) - planning_fields
        if forbidden:
            raise Conflict("Only planning fields can be edited after a work order is Planned.",
                           code="planned_fields_locked", details={"fields": sorted(forbidden)})
    if "site" in values:
        new_site = values["site"]
        if new_site.organization_id != wo.organization_id or new_site.status != Site.Status.ACTIVE:
            raise NotFound("Site not found.")
        _need(membership, "work_order.update", new_site)
        if wo.source_request_id and new_site.pk != wo.source_request.site_id:
            raise ValidationFailed("Site must remain linked to the approved source record.", code="source_site_locked")
        if wo.status != WorkOrder.Status.DRAFT and new_site.pk != wo.site_id:
            raise Conflict("The site cannot be changed after the work order is planned.", code="site_locked")
        wo.site = new_site
    asset_value = values.pop("asset", None)
    component_not_submitted = object()
    component_value = values.pop("component", component_not_submitted)
    for name in ("work_type", "priority", "estimated_hours"):
        if values.get(name) == "":
            values[name] = None
    for name, value in values.items():
        if name != "site":
            setattr(wo, name, value)
    _validate_linked_priority(wo, wo.priority)
    if asset_value is not None:
        if wo.source_request_id and asset_value.pk != wo.source_request.asset_id:
            raise ValidationFailed("Asset must remain linked to the approved source record.", code="source_asset_locked")
        wo.asset = asset_value
    if component_value is not component_not_submitted:
        if wo.source_request_id and component_value != wo.component:
            raise ValidationFailed("Component cannot be changed independently for a source-linked work order.",
                                   code="source_component_locked")
        wo.component = component_value
    if wo.asset_id:
        if wo.asset.organization_id != wo.organization_id:
            raise ValidationFailed("Asset must belong to this organization.", code="cross_tenant_asset")
        if wo.asset.status != "ACTIVE":
            raise ValidationFailed("Selected asset is inactive.", code="asset_inactive")
        if wo.asset.site_id != wo.site_id:
            raise ValidationFailed("Invalid asset for the selected site.", code="asset_site_mismatch")
        wo.asset_reference = wo.asset.asset_tag
    if wo.component_id:
        from apps.assets.models import AssetComponent
        if not wo.asset_id or not AssetComponent.objects.filter(
                organization=wo.organization, parent_id=wo.asset_id, child_id=wo.component_id,
                relationship_type=AssetComponent.Relationship.COMPONENT).exists():
            raise ValidationFailed("Invalid component for the selected asset.", code="component_asset_mismatch")
        wo.component_reference = wo.component.asset_tag
    else:
        wo.component_reference = ""
    wo.source_reference = (wo.source_reference or "").strip()
    if wo.source_type != WorkOrder.SourceType.MANUAL and not wo.source_reference:
        raise ValidationFailed("Add the reference for the selected work-order source.", code="source_reference_required")
    if wo.source_type == WorkOrder.SourceType.PM and WorkOrder.objects.for_organization(wo.organization).filter(
            source_type=WorkOrder.SourceType.PM, source_reference=wo.source_reference).exclude(
                pk=wo.pk).exclude(status=WorkOrder.Status.CANCELLED).exists():
        raise Conflict("A work order already exists for this preventive-maintenance schedule.",
                       code="pm_work_order_exists")
    if wo.source_type in (WorkOrder.SourceType.REQUEST, WorkOrder.SourceType.INCIDENT):
        if not wo.source_request_id:
            raise ValidationFailed("A linked incident or service request is required.", code="source_relationship_required")
    wo.service_request_reference = wo.source_reference if wo.source_type == WorkOrder.SourceType.REQUEST else ""
    wo.title = (wo.title or "").strip()
    if not wo.title:
        raise ValidationFailed("A work order title is required.")
    _validate_schedule_window(wo.planned_start, wo.planned_end, wo.due_at, wo.estimated_hours)
    if wo.estimated_hours is not None and wo.estimated_hours < 0:
        raise ValidationFailed("Estimated hours cannot be negative.")
    schedule_values = (wo.planned_start, wo.planned_end, wo.due_at, wo.estimated_hours)
    if any(value is not None for value in schedule_values) and not all(value is not None for value in schedule_values):
        raise ValidationFailed("Provide all schedule fields together, or leave them blank until planning.",
                               code="incomplete_schedule")
    if wo.status != WorkOrder.Status.DRAFT and (
            not wo.work_type or not wo.priority or not all(value is not None for value in schedule_values)
            or wo.estimated_hours <= 0):
        raise ValidationFailed("A planned work order must retain its work type, priority, complete schedule, and positive estimate.",
                               code="planned_fields_required")
    from .checklist_adapter import configure_requirement
    configure_requirement(wo)
    if wo.assigned_to_id:
        conflict = WorkOrder.objects.for_organization(wo.organization).filter(
            assigned_to=wo.assigned_to, status__in=ACTIVE_ASSIGNMENT_STATES,
        ).exclude(pk=wo.pk).exists()
        if conflict:
            raise Conflict("Technician already has another active work order; only one active assignment is allowed.",
                           code="technician_conflict")
    before = _snapshot(WorkOrder.objects.select_related("site").get(pk=wo.pk))
    try:
        wo.full_clean()
        wo.save()
    except ValidationError as exc:
        raise ValidationFailed("Work order is invalid.", details={"fields": exc.message_dict if hasattr(exc, "message_dict") else exc.messages}) from exc
    _record(wo, actor=actor, action="updated", before=before, request=request)
    return wo


@transaction.atomic
def plan_work_order(wo, *, membership, actor, planned_start, planned_end, due_at=None,
                    estimated_hours=None, work_type=None, priority=None, expected_version=None, request=None):
    _need(membership, "work_order.plan", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    _transition(wo, WorkOrder.Status.PLANNED)
    if not planned_start or not planned_end or planned_end <= planned_start:
        raise ValidationFailed("Planned end must be after planned start.")
    effective_due_at = due_at if due_at is not None else wo.due_at
    effective_estimated_hours = estimated_hours if estimated_hours is not None else wo.estimated_hours
    effective_work_type = work_type or wo.work_type
    effective_priority = priority or wo.priority
    if not effective_work_type:
        raise ValidationFailed("Choose a work type before planning this work order.", code="work_type_required")
    if not effective_priority:
        raise ValidationFailed("Choose a priority before planning this work order.", code="priority_required")
    _validate_linked_priority(wo, effective_priority)
    if effective_due_at is None:
        raise ValidationFailed("Enter the due date and time before planning this work order.",
                               code="due_date_required")
    if effective_estimated_hours is None or Decimal(effective_estimated_hours) <= 0:
        raise ValidationFailed("Estimated hours must be greater than zero.", code="estimate_not_positive")
    _validate_schedule_window(planned_start, planned_end, effective_due_at, effective_estimated_hours)
    if estimated_hours is not None and Decimal(estimated_hours) < 0:
        raise ValidationFailed("Estimated hours cannot be negative.")
    changes = {"planned_start": planned_start, "planned_end": planned_end}
    changes["work_type"] = effective_work_type
    changes["priority"] = effective_priority
    from .checklist_adapter import configure_requirement
    configure_requirement(wo, effective_work_type)
    if wo.checklist_required and not wo.checklist_template_ref:
        raise ValidationFailed("A checklist template reference is required before planning.",
                               code="checklist_template_missing")
    changes.update({
        "checklist_required": wo.checklist_required,
        "checklist_template_ref": wo.checklist_template_ref,
        "checklist_status": wo.checklist_status,
        "checklist_validation_state": wo.checklist_validation_state,
        "checklist_instance_ref": wo.checklist_instance_ref,
        "checklist_completed_at": wo.checklist_completed_at,
        "checklist_validated_at": wo.checklist_validated_at,
    })
    if due_at is not None:
        changes["due_at"] = due_at
    if estimated_hours is not None:
        changes["estimated_hours"] = estimated_hours
    return transition_work_order(wo, WorkOrder.Status.PLANNED, membership=membership, actor=actor,
        permission="work_order.plan", action="planned", changes=changes,
        expected_version=expected_version, request=request)


@transaction.atomic
def assign_work_order(wo, *, membership, actor, assignee_id, scheduled_start=None, scheduled_end=None,
                      expected_version=None, request=None):
    _need(membership, "work_order.assign", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    if expected_version is not None and int(expected_version) != wo.version:
        raise Conflict("This work order changed since you loaded it. Refresh and retry.",
                       code="work_order_version_conflict")
    _transition(wo, WorkOrder.Status.ASSIGNED)
    assigned = Membership.objects.select_for_update().select_related("user").filter(
        organization=wo.organization, pk=assignee_id).first()
    if assigned is None:
        raise ValidationFailed("Technician was not found in this organization.", code="invalid_technician")
    scheduled_start = scheduled_start or wo.planned_start
    scheduled_end = scheduled_end or wo.planned_end
    if not scheduled_start or not scheduled_end or scheduled_end <= scheduled_start:
        raise ValidationFailed("Enter a valid scheduled start and end; end must be later than start.",
                               code="invalid_schedule")
    if wo.due_at and wo.due_at <= scheduled_end:
        raise ValidationFailed("Due date must be later than scheduled end.", code="due_before_schedule_end")
    if wo.estimated_hours is not None and Decimal(wo.estimated_hours) > Decimal(
            str((scheduled_end - scheduled_start).total_seconds())) / Decimal("3600"):
        raise ValidationFailed("Estimated hours cannot exceed the scheduled window.", code="estimate_exceeds_schedule")
    candidate = WorkOrder.objects.select_related("site").get(pk=wo.pk)
    candidate.planned_start, candidate.planned_end = scheduled_start, scheduled_end
    warnings = validate_assignment(candidate, assigned)
    assigned_order = transition_work_order(wo, WorkOrder.Status.ASSIGNED, membership=membership, actor=actor,
        permission="work_order.assign", action="assigned", changes={"assigned_to": assigned,
            "planned_start": scheduled_start, "planned_end": scheduled_end},
        metadata={"membership_id": str(assigned.pk), "warnings": warnings},
        expected_version=expected_version, request=request)
    assignment = WorkOrderAssignment(
        organization=assigned_order.organization, work_order=assigned_order, technician=assigned,
        scheduled_start=assigned_order.planned_start, scheduled_end=assigned_order.planned_end,
        assigned_by=actor,
    )
    try:
        assignment.full_clean()
        assignment.save()
    except ValidationError as exc:
        raise ValidationFailed("The technician assignment is invalid.",
                               details={"fields": exc.message_dict if hasattr(exc, "message_dict") else exc.messages}) from exc
    return assigned_order


@transaction.atomic
def reschedule_assigned_work_order(wo, *, membership, actor, scheduled_start, scheduled_end,
                                   assignee_id=None, expected_version=None, request=None):
    """Update an ASSIGNED order's schedule and assignment atomically, without a status transition."""
    wo = WorkOrder.objects.select_for_update(of=("self",)).select_related("site", "assigned_to__user").get(pk=wo.pk)
    _need(membership, "work_order.assign", wo.site)
    if wo.status != WorkOrder.Status.ASSIGNED:
        raise Conflict("Only an Assigned work order can be rescheduled.", code="work_order_not_reschedulable")
    if expected_version is not None and int(expected_version) != wo.version:
        raise Conflict("This work order changed since you loaded it. Refresh and retry.",
                       code="work_order_version_conflict")
    assignment = WorkOrderAssignment.objects.select_for_update().select_related(
        "technician__user").filter(work_order=wo).first()
    if assignment is None or assignment.technician_id != wo.assigned_to_id:
        raise ValidationFailed("The active technician assignment is missing or inconsistent.",
                               code="active_assignment_required")
    technician_id = assignee_id or assignment.technician_id
    technician = Membership.objects.select_for_update().select_related("user").filter(
        organization=wo.organization, pk=technician_id).first()
    if technician is None:
        raise NotFound("Technician not found.")
    if not scheduled_start or not scheduled_end or scheduled_end <= scheduled_start:
        raise ValidationFailed("Scheduled end must be later than scheduled start.", code="invalid_schedule")
    if wo.due_at and wo.due_at <= scheduled_end:
        raise ValidationFailed("Due date must be later than scheduled end.", code="due_before_schedule_end")
    if wo.estimated_hours is not None and Decimal(wo.estimated_hours) > Decimal(
            str((scheduled_end - scheduled_start).total_seconds())) / Decimal("3600"):
        raise ValidationFailed("Estimated hours cannot exceed the scheduled window.", code="estimate_exceeds_schedule")
    candidate = WorkOrder.objects.select_related("site").get(pk=wo.pk)
    candidate.planned_start, candidate.planned_end = scheduled_start, scheduled_end
    warnings = validate_assignment(candidate, technician)
    before = _snapshot(wo)
    wo.assigned_to = technician
    wo.planned_start, wo.planned_end = scheduled_start, scheduled_end
    wo.save(update_fields=("assigned_to", "planned_start", "planned_end", "updated_at"))
    assignment.technician = technician
    assignment.scheduled_start, assignment.scheduled_end = scheduled_start, scheduled_end
    assignment.save(update_fields=("technician", "scheduled_start", "scheduled_end", "updated_at"))
    _record(wo, actor=actor, action="rescheduled", before=before,
            metadata={"scheduled_start": scheduled_start.isoformat(), "scheduled_end": scheduled_end.isoformat(),
                      "technician_id": str(technician.pk), "warnings": warnings}, request=request)
    return wo


@transaction.atomic
def dispatch_work_order(wo, *, membership, actor, expected_version=None, request=None):
    _need(membership, "work_order.dispatch", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    _transition(wo, WorkOrder.Status.DISPATCHED)
    assignment = WorkOrderAssignment.objects.select_for_update().select_related(
        "technician__user").filter(work_order=wo).first()
    if assignment is None or not wo.assigned_to_id or assignment.technician_id != wo.assigned_to_id:
        raise ValidationFailed("Assign a technician with a valid schedule before dispatch.",
                               code="active_assignment_required")
    if assignment.technician.status != Membership.Status.ACTIVE or not assignment.technician.user.is_active:
        raise ValidationFailed("The assigned technician is inactive. Reassign before dispatch.",
                               code="technician_inactive")
    if (assignment.scheduled_start != wo.planned_start or assignment.scheduled_end != wo.planned_end or
            assignment.scheduled_end <= assignment.scheduled_start):
        raise ValidationFailed("The assignment schedule is invalid or out of date. Reassign before dispatch.",
                               code="assignment_schedule_invalid")
    return transition_work_order(wo, WorkOrder.Status.DISPATCHED, membership=membership, actor=actor,
        permission="work_order.dispatch", action="dispatched", changes={"dispatched_at": timezone.now()},
        expected_version=expected_version, request=request)


@transaction.atomic
def start_work_order(wo, *, membership, actor, expected_version=None, request=None):
    _need(membership, "work_order.start", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    _transition(wo, WorkOrder.Status.IN_PROGRESS)
    if wo.assigned_to_id != membership.pk:
        raise PermissionDenied("Only the assigned technician may start this work order.")
    wo = transition_work_order(wo, WorkOrder.Status.IN_PROGRESS, membership=membership, actor=actor,
        permission="work_order.start", action="started", changes={"started_at": timezone.now()},
        expected_version=expected_version, request=request)
    _sync_incident_request(wo, "started", actor, request)
    return wo


def assert_can_execute(wo, membership) -> None:
    """Execution (start / hold / resume / complete) belongs to the assignee; dispatchers may act for them."""
    if wo.assigned_to_id == membership.pk:
        return
    if rbac.has_permission(membership, "work_order.review", wo.site_id):
        raise PermissionDenied("Only the assigned technician (or a dispatcher) can do this.", code="not_assignee")
    if rbac.has_permission(membership, "work_order.dispatch", wo.site_id):
        return
    raise PermissionDenied("Only the assigned technician (or a dispatcher) can do this.", code="not_assignee")


def _assert_assignee_or_dispatcher(wo, membership):
    assert_can_execute(wo, membership)


def _assert_assignee_or_authorized(wo, membership, permission):
    if (wo.assigned_to_id != membership.pk and
            not rbac.has_permission(membership, permission, wo.site)):
        raise PermissionDenied("Only the assigned technician or an authorized supervisor may perform this action.")


@transaction.atomic
def hold_work_order(wo, *, membership, actor, reason, expected_version=None, request=None):
    _need(membership, "work_order.hold", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    _transition(wo, WorkOrder.Status.ON_HOLD)
    _assert_assignee_or_authorized(wo, membership, "work_order.hold")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationFailed("A reason is required to put a work order on hold.")
    return transition_work_order(wo, WorkOrder.Status.ON_HOLD, membership=membership, actor=actor,
        permission="work_order.hold", action="held", changes={"hold_reason": reason}, reason=reason,
        expected_version=expected_version, request=request)


@transaction.atomic
def resume_work_order(wo, *, membership, actor, expected_version=None, request=None):
    _need(membership, "work_order.start", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    _transition(wo, WorkOrder.Status.IN_PROGRESS)
    _assert_assignee_or_authorized(wo, membership, "work_order.start")
    return transition_work_order(wo, WorkOrder.Status.IN_PROGRESS, membership=membership, actor=actor,
        permission="work_order.start", action="resumed", changes={"hold_reason": ""},
        expected_version=expected_version, request=request)


def _check_completion(wo, notes):
    if len((notes or "").strip()) < 10:
        raise ValidationFailed("Resolution notes must contain at least 10 characters.", code="resolution_notes_required")
    from .checklist_adapter import validate_for_completion
    validate_for_completion(wo)
    evidence_required = wo.work_type in (WorkOrder.WorkType.CORRECTIVE, WorkOrder.WorkType.INSTALLATION)
    if evidence_required and not wo.completion_evidence.exists():
        raise Conflict("Attach completion evidence before completing this work order.", code="evidence_required")


@transaction.atomic
def complete_work_order(wo, *, membership, actor, resolution_notes, expected_version=None, request=None):
    _need(membership, "work_order.complete", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    _transition(wo, WorkOrder.Status.COMPLETED)
    if wo.assigned_to_id != membership.pk:
        raise PermissionDenied("Only the assigned technician may complete this work order.")
    _check_completion(wo, resolution_notes)
    changes = {"resolution_notes": resolution_notes.strip(), "completed_at": timezone.now()}
    wo = transition_work_order(wo, WorkOrder.Status.COMPLETED, membership=membership, actor=actor,
        permission="work_order.complete", action="completed", changes=changes,
        expected_version=expected_version, request=request)
    _sync_incident_request(wo, "completed", actor, request)
    return wo


@transaction.atomic
def submit_for_review(wo, *, membership, actor, expected_version=None, request=None):
    """Persist the documented COMPLETED -> SUPERVISOR_REVIEW transition separately."""
    code = "work_order.review" if rbac.has_permission(membership, "work_order.review", wo.site) else "work_order.complete"
    _need(membership, code, wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    _transition(wo, WorkOrder.Status.SUPERVISOR_REVIEW)
    if code == "work_order.complete" and wo.assigned_to_id != membership.pk:
        raise PermissionDenied("Only the assigned technician may submit this work order for review.")
    _check_completion(wo, wo.resolution_notes)
    return transition_work_order(wo, WorkOrder.Status.SUPERVISOR_REVIEW, membership=membership, actor=actor,
        permission=code, action="submitted_for_review", expected_version=expected_version,
        request=request)


@transaction.atomic
def review_work_order(wo, *, membership, actor, approved, notes="", expected_version=None,
                      request=None, require_comment=False):
    """Single implementation for legacy review and explicit close/return actions."""
    code = "work_order.close" if approved else "work_order.review"
    _need(membership, code, wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    notes = (notes or "").strip()
    if approved and wo.status == WorkOrder.Status.CLOSED:
        # The close endpoint is safe to retry after a network timeout.
        if wo.closure_approvals.filter(decision=ClosureApproval.Decision.APPROVED).exists():
            return wo
    if wo.status != WorkOrder.Status.SUPERVISOR_REVIEW:
        raise InvalidTransition("Only work orders awaiting supervisor review can be reviewed.")
    if approved and require_comment and not notes:
        raise ValidationFailed("A closure comment is required.", code="closure_comment_required")
    if not approved and not notes:
        raise ValidationFailed("A reason is required when rejecting a work order.")
    if approved:
        blockers = closure_blockers(wo)
        if blockers:
            raise Conflict("; ".join(blockers), code="closure_blocked")
        _check_completion(wo, wo.resolution_notes)
        target = WorkOrder.Status.CLOSED
        changes = {"closed_at": timezone.now(), "closed_by": actor, "supervisor_notes": notes}
        action = "closed"
    else:
        target = WorkOrder.Status.IN_PROGRESS
        changes = {"supervisor_notes": notes}
        action = "review_rejected"
    wo = transition_work_order(wo, target, membership=membership, actor=actor, permission=code,
        action=action, changes=changes, reason=notes, expected_version=expected_version, request=request)
    ClosureApproval.objects.create(organization=wo.organization, work_order=wo, approver=actor,
                                   decision=(ClosureApproval.Decision.APPROVED if approved
                                             else ClosureApproval.Decision.RETURNED), comment=notes)
    if action == "review_rejected":
        _sync_incident_request(wo, "reworked", actor, request)
    return wo


def close_work_order(wo, *, membership, actor, comment, expected_version=None, request=None):
    return review_work_order(wo, membership=membership, actor=actor, approved=True, notes=comment,
                             expected_version=expected_version, request=request, require_comment=True)


def return_for_rework(wo, *, membership, actor, comment, expected_version=None, request=None):
    return review_work_order(wo, membership=membership, actor=actor, approved=False, notes=comment,
                             expected_version=expected_version, request=request)


@transaction.atomic
def set_checklist(wo, *, membership, actor, items, request=None):
    from django.conf import settings
    _need(membership, "work_order.update", wo.site)
    if not getattr(settings, "WORKORDER_PROVISIONAL_CHECKLIST_ENABLED", False):
        raise Conflict("Provisional checklist notes are disabled. Checklist execution is managed by M08.",
                       code="provisional_checklist_disabled")
    if wo.status not in (WorkOrder.Status.DRAFT, WorkOrder.Status.PLANNED, WorkOrder.Status.ASSIGNED):
        raise Conflict("Provisional checklist notes can only be edited before dispatch.")
    if not isinstance(items, list) or not items or any(not isinstance(i, dict) or not str(i.get("label", "")).strip() for i in items):
        raise ValidationFailed("Add at least one checklist item with a label.")
    before = _snapshot(wo)
    wo.provisional_checklist = [{"label": str(i["label"]).strip()[:200]} for i in items]
    wo.save(update_fields=["provisional_checklist", "updated_at"])
    _record(wo, actor=actor, action="provisional_checklist_updated", before=before, request=request)
    return wo


@transaction.atomic
def register_checklist_reference(wo, *, membership, actor, template_ref, instance_ref, request=None):
    """Store opaque future-M08 identifiers; this does not assert completion."""
    _need(membership, "work_order.update", wo.site)
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    if not wo.checklist_required:
        raise Conflict("This work type has no checklist requirement.", code="checklist_not_required")
    if wo.status not in (WorkOrder.Status.DRAFT, WorkOrder.Status.PLANNED):
        raise Conflict("Checklist references are read-only after work is assigned.",
                       code="checklist_reference_read_only")
    if template_ref != wo.checklist_template_ref:
        raise ValidationFailed("Checklist template does not match the required work-type template.",
                               code="checklist_template_mismatch")
    instance_ref = (instance_ref or "").strip()
    if not instance_ref:
        raise ValidationFailed("A checklist instance reference is required.", code="checklist_instance_required")
    before = _snapshot(wo)
    wo.checklist_template_ref = template_ref
    wo.checklist_instance_ref = instance_ref
    wo.checklist_status = "PENDING"
    wo.checklist_validation_state = "PENDING"
    wo.checklist_completed_at = None
    wo.checklist_validated_at = None
    wo.save(update_fields=["checklist_template_ref", "checklist_instance_ref", "checklist_status",
                           "checklist_validation_state", "checklist_completed_at", "checklist_validated_at",
                           "updated_at"])
    _record(wo, actor=actor, action="checklist_reference_registered", before=before,
            metadata={"template_ref": template_ref, "instance_ref": instance_ref}, request=request)
    return wo


@transaction.atomic
def record_labor(wo, *, membership, actor, work_date, hours, description="Labor performed", technician=None, notes="", request=None):
    _need(membership, "work_order.record", wo.site)
    _assert_assignee_or_dispatcher(wo, membership)
    if wo.status not in (WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD):
        raise Conflict("Labor can only be recorded during active work.")
    try:
        hours = Decimal(str(hours))
    except Exception as exc:
        raise ValidationFailed("Hours must be a decimal between 0 and 24.") from exc
    desc = (description or notes or "Labor performed").strip()
    if hours <= 0 or hours > 24 or work_date > timezone.localdate() or not desc:
        raise ValidationFailed("Labor requires 0 < hours <= 24, a non-future date, and a description.")
    tech = technician or membership
    entry = WorkOrderLabor.objects.create(organization=wo.organization, work_order=wo, technician=tech,
                                          entered_by=actor, work_date=work_date, hours=hours,
                                          description=desc)
    _record(wo, actor=actor, action="labor_recorded", metadata={"labor_id": str(entry.pk), "hours": str(hours)},
            request=request)
    return entry


@transaction.atomic
def record_work_update(wo, *, membership, actor, update, request=None):
    _need(membership, "work_order.record", wo.site)
    _assert_assignee_or_dispatcher(wo, membership)
    if wo.status not in (WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD):
        raise Conflict("Work updates can only be recorded while work is in progress or on hold.")
    update = (update or "").strip()
    if len(update) < 5:
        raise ValidationFailed("Enter a work update of at least 5 characters.")
    _record(wo, actor=actor, action="work_update", notes=update, request=request)
    return wo


@transaction.atomic
def record_material(wo, *, membership, actor, item_name, quantity, unit="", part_reference="", notes="", request=None):
    _need(membership, "work_order.record", wo.site)
    _assert_assignee_or_dispatcher(wo, membership)
    if wo.status not in (WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD):
        raise Conflict("Materials can only be recorded during active work.")
    try:
        quantity = Decimal(str(quantity))
    except Exception as exc:
        raise ValidationFailed("Quantity must be a positive decimal.") from exc
    if quantity <= 0 or not item_name.strip():
        raise ValidationFailed("Material name and positive quantity are required.")
    entry = WorkOrderMaterial.objects.create(organization=wo.organization, work_order=wo, entered_by=actor,
        item_name=item_name.strip(), quantity=quantity, unit=unit.strip(), part_reference=part_reference.strip(),
        notes=notes.strip())
    _record(wo, actor=actor, action="material_recorded", metadata={"material_id": str(entry.pk),
            "quantity": str(quantity), "inventory_movement": False}, request=request)
    return entry


@transaction.atomic
def add_evidence(wo, *, membership, actor, attachment_id, request=None):
    _need(membership, "work_order.attach", wo.site)
    attachment = Attachment.objects.for_organization(wo.organization).filter(pk=attachment_id).first()
    if attachment is None:
        raise NotFound("Evidence file not found.")
    if attachment.object_id != str(wo.pk):
        raise ValidationFailed("Evidence must already be attached to this work order.")
    before = _snapshot(wo)
    WorkOrderEvidence.objects.get_or_create(organization=wo.organization, work_order=wo, attachment=attachment,
                                            defaults={"linked_by": actor})
    _record(wo, actor=actor, action="evidence_linked", before=before,
            metadata={"attachment_id": str(attachment.pk)}, request=request)
    return attachment


def closure_blockers(wo):
    blockers = []
    if len((wo.resolution_notes or "").strip()) < 10:
        blockers.append("Resolution notes must contain at least 10 characters.")
    try:
        from apps.checklists.services import checklist_blockers as m08_checklist_blockers
        blockers.extend(m08_checklist_blockers(wo))
    except Exception:
        from .checklist_adapter import get_checklist_status
        checklist = get_checklist_status(wo)
        if not checklist.is_valid:
            blockers.append(checklist.message)
    if wo.work_type in (WorkOrder.WorkType.CORRECTIVE, WorkOrder.WorkType.INSTALLATION) and not wo.completion_evidence.exists():
        blockers.append("Completion evidence is required.")
    return blockers


def evidence_for(wo):
    return Attachment.objects.for_organization(wo.organization).filter(work_order_links__work_order=wo)


def checklist_blockers(wo):
    from apps.checklists.services import checklist_blockers as m08_checklist_blockers
    return m08_checklist_blockers(wo)


def transition(wo, action, *, actor, membership, reason="", request=None, **kwargs):
    """Convenience dispatcher for work-order lifecycle transitions."""
    if action == "plan":
        from datetime import timedelta
        planned_start = kwargs.get("planned_start")
        planned_end = kwargs.get("planned_end")
        due_at = kwargs.get("due_at") or (planned_end + timedelta(days=1) if planned_end else None)
        estimated_hours = kwargs.get("estimated_hours") or (wo.estimated_hours if wo.estimated_hours and wo.estimated_hours > 0 else Decimal("2.0"))
        priority = kwargs.get("priority") or wo.priority or WorkOrder.Priority.MEDIUM
        work_type = kwargs.get("work_type") or wo.work_type or WorkOrder.WorkType.PREVENTIVE
        return plan_work_order(wo, membership=membership, actor=actor, planned_start=planned_start,
                               planned_end=planned_end, due_at=due_at, estimated_hours=estimated_hours,
                               priority=priority, work_type=work_type, request=request,
                               expected_version=kwargs.get("expected_version"))
    elif action == "assign":
        assignee = kwargs.get("technician", kwargs.get("assignee_id"))
        assignee_id = assignee.pk if hasattr(assignee, "pk") else assignee
        scheduled_start = kwargs.get("planned_start", kwargs.get("scheduled_start"))
        scheduled_end = kwargs.get("planned_end", kwargs.get("scheduled_end"))
        return assign_work_order(wo, membership=membership, actor=actor, assignee_id=assignee_id,
                                 scheduled_start=scheduled_start, scheduled_end=scheduled_end, request=request)
    elif action == "dispatch":
        return dispatch_work_order(wo, membership=membership, actor=actor, request=request)
    elif action == "start":
        return start_work_order(wo, membership=membership, actor=actor, request=request)
    elif action == "hold":
        return hold_work_order(wo, membership=membership, actor=actor, reason=reason or kwargs.get("reason", "Hold"), request=request)
    elif action == "resume":
        return resume_work_order(wo, membership=membership, actor=actor, request=request)
    elif action == "complete":
        resolution_notes = kwargs.get("resolution_notes", reason or "Completed work order.")
        return complete_work_order(wo, membership=membership, actor=actor, resolution_notes=resolution_notes, request=request)
    elif action in ("start_review", "submit_for_review"):
        return submit_for_review(wo, membership=membership, actor=actor, request=request)
    elif action == "review":
        return review_work_order(wo, membership=membership, actor=actor, approved=kwargs.get("approved", True),
                                 notes=kwargs.get("notes", reason), request=request)
    elif action == "close":
        comment = kwargs.get("comment", kwargs.get("notes", reason or "Closed after review"))
        return close_work_order(wo, membership=membership, actor=actor, comment=comment, request=request)
    elif action in ("return_for_rework", "reject_review"):
        comment = kwargs.get("comment", kwargs.get("notes", reason or "Returned for rework"))
        return return_for_rework(wo, membership=membership, actor=actor, comment=comment, request=request)
    elif action == "cancel":
        return cancel_work_order(wo, membership=membership, actor=actor, reason=reason, request=request)
    else:
        raise ValidationFailed(f"Unknown workflow action '{action}'.", code="invalid_action")
