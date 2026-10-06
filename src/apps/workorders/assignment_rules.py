"""Pluggable assignment checks owned by the Work Order module.

Shift, skills, workload capacity, and technician location are not represented in
this checkout. The existing, enforceable rules are kept here and missing shift
data is surfaced as a warning rather than silently treated as verified.
"""
from apps.core.exceptions import Conflict, ValidationFailed
from apps.rbac import services as rbac
from apps.tenancy.models import Membership

from .models import WorkOrder

ACTIVE_ASSIGNMENT_STATES = [
    WorkOrder.Status.ASSIGNED,
    WorkOrder.Status.DISPATCHED,
    WorkOrder.Status.IN_PROGRESS,
    WorkOrder.Status.ON_HOLD,
]


def validate_assignment(work_order, technician):
    if technician.organization_id != work_order.organization_id:
        raise ValidationFailed("Technician must belong to this organization.", code="invalid_technician")
    if technician.status != Membership.Status.ACTIVE or not technician.user.is_active:
        raise ValidationFailed("Technician is inactive.", code="technician_inactive")
    if not rbac.has_permission(technician, "work_order.start", work_order.site):
        raise ValidationFailed("Technician is not authorized for this site.", code="technician_site_forbidden")
    if not work_order.planned_start or not work_order.planned_end:
        raise ValidationFailed("Set a valid scheduled window before assigning a technician.",
                               code="schedule_required")
    if work_order.planned_end <= work_order.planned_start:
        raise ValidationFailed("Scheduled end must be after scheduled start.", code="invalid_schedule")

    conflict = WorkOrder.objects.for_organization(work_order.organization).filter(
        assigned_to=technician,
        status__in=ACTIVE_ASSIGNMENT_STATES,
        planned_start__lt=work_order.planned_end,
        planned_end__gt=work_order.planned_start,
    ).exclude(pk=work_order.pk).exists()
    if conflict:
        raise Conflict("Technician has a conflicting overlapping work order in this time window.",
                       code="technician_conflict")

    # TechnicianProfile / Shift / availability do not exist in this checkout.
    return ["Shift availability is not configured; confirm the technician is on shift before assignment."]
