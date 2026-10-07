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


def available_technicians(work_order):
    """Return active, authorized technicians with no other active work-order assignment."""
    if not work_order.planned_start or not work_order.planned_end or work_order.planned_end <= work_order.planned_start:
        return Membership.objects.none()

    candidates = Membership.objects.for_organization(work_order.organization).filter(
        status=Membership.Status.ACTIVE,
        user__is_active=True,
        membership_roles__role__system_key="technician",
    ).select_related("user").distinct()
    allowed_ids = [member.pk for member in candidates
                   if rbac.has_permission(member, "work_order.start", work_order.site)]
    if not allowed_ids:
        return candidates.none()

    booked_ids = WorkOrder.objects.for_organization(work_order.organization).filter(
        assigned_to_id__in=allowed_ids,
        status__in=ACTIVE_ASSIGNMENT_STATES,
    ).exclude(pk=work_order.pk).values_list("assigned_to_id", flat=True)
    return candidates.filter(pk__in=allowed_ids).exclude(pk__in=booked_ids).order_by(
        "user__full_name", "user__email")


def validate_assignment(work_order, technician):
    if technician.organization_id != work_order.organization_id:
        raise ValidationFailed("Technician must belong to this organization.", code="invalid_technician")
    if technician.status != Membership.Status.ACTIVE or not technician.user.is_active:
        raise ValidationFailed("Technician is inactive.", code="technician_inactive")
    if not technician.membership_roles.filter(role__system_key="technician").exists():
        raise ValidationFailed("The assigned member must have the Technician role.", code="technician_role_required")
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
    ).exclude(pk=work_order.pk).exists()
    if conflict:
        raise Conflict("Technician already has another active work order; only one active assignment is allowed.",
                       code="technician_conflict")

    # TechnicianProfile / Shift / availability do not exist in this checkout.
    return ["Shift availability is not configured; confirm the technician is on shift before assignment."]
