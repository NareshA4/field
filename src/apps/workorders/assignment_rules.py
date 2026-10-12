"""Pluggable assignment checks owned by the Work Order module.

Shift, skills, workload capacity, and technician location are not represented in
this checkout. The existing, enforceable rules are kept here and missing shift
data is surfaced as a warning rather than silently treated as verified.
"""
from zoneinfo import ZoneInfo

from django.utils import timezone
from django.db.models.functions import Coalesce

from apps.core.exceptions import Conflict, ValidationFailed
from apps.rbac import services as rbac
from apps.sites.models import OperatingCalendar
from apps.tenancy.models import Membership

from .models import WorkOrder

ACTIVE_ASSIGNMENT_STATES = [
    WorkOrder.Status.ASSIGNED,
    WorkOrder.Status.DISPATCHED,
    WorkOrder.Status.IN_PROGRESS,
    WorkOrder.Status.ON_HOLD,
]


def schedule_warnings(work_order):
    """Flag work scheduled outside the site's configured operating calendar."""
    start, end = work_order.planned_start, work_order.planned_end
    if not start or not end:
        return ["Scheduled start and end are required."]
    calendar = OperatingCalendar.objects.filter(site=work_order.site, is_default=True).first()
    if calendar is None:
        return ["No default site operating calendar is configured; hours could not be checked."]
    if calendar.is_24x7:
        return []
    if not calendar.start_time or not calendar.end_time or not calendar.working_days:
        return [f"{calendar.name} has incomplete hours; confirm the shift manually."]
    zone = ZoneInfo(work_order.site.timezone or "UTC")
    local_start, local_end = timezone.localtime(start, zone), timezone.localtime(end, zone)
    warnings = []
    if local_start.date() != local_end.date():
        warnings.append("Scheduled window crosses calendar days; confirm site coverage.")
    holidays = calendar.holidays.filter(date__gte=local_start.date(), date__lte=local_end.date())
    is_working_day = local_start.isoweekday() in calendar.working_days
    in_hours = (calendar.start_time <= local_start.timetz().replace(tzinfo=None)
                and local_end.timetz().replace(tzinfo=None) <= calendar.end_time)
    if not is_working_day or holidays.exists() or not in_hours:
        warnings.append(f"Scheduled window falls outside {calendar.name} operating hours ({calendar.start_time:%H:%M}–{calendar.end_time:%H:%M}); confirm technician coverage.")
    return warnings


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
    ).annotate(
        occupied_start=Coalesce("assignment__scheduled_start", "planned_start"),
        occupied_end=Coalesce("assignment__scheduled_end", "planned_end"),
    ).filter(
        occupied_start__lt=work_order.planned_end,
        occupied_end__gt=work_order.planned_start,
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
    ).annotate(
        occupied_start=Coalesce("assignment__scheduled_start", "planned_start"),
        occupied_end=Coalesce("assignment__scheduled_end", "planned_end"),
    ).filter(
        occupied_start__lt=work_order.planned_end,
        occupied_end__gt=work_order.planned_start,
    ).exclude(pk=work_order.pk).exists()
    if conflict:
        raise Conflict("Technician already has an overlapping active assignment.",
                       code="technician_conflict")

    # Site calendars provide operating hours, but technician-specific shifts are
    # not modeled. Surface calendar exceptions and require the planner to confirm.
    return schedule_warnings(work_order)
