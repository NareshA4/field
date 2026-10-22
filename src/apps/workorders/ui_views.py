import json
import logging
from datetime import time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from apps.core.exceptions import DomainError, NotFound
from apps.files import services as file_services
from apps.rbac import services as rbac
from apps.tenancy.models import Membership
from apps.ui.mixins import TenantPermissionMixin

from . import services
from .assignment_rules import available_technicians, schedule_warnings
from .checklist_adapter import get_checklist_summary

logger = logging.getLogger(__name__)
from .forms import (
    AssignForm,
    ChecklistForm,
    ClosureCommentForm,
    CompleteForm,
    CreateWorkOrderForm,
    EditWorkOrderForm,
    EvidenceForm,
    LaborForm,
    MaterialForm,
    PlanForm,
    ReasonForm,
    ReviewForm,
    WorkUpdateForm,
)
from .models import WorkOrder, WorkOrderAssignment


def _site_zone(site):
    try:
        return ZoneInfo(site.timezone or settings.TIME_ZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo(settings.TIME_ZONE)


def _orders(request):
    m = request.membership
    qs = WorkOrder.objects.for_organization(m.organization).select_related("site", "assigned_to__user", "created_by")
    if not rbac.has_permission_anywhere(m, "work_order.view"):
        qs = qs.filter(Q(assigned_to=m) | Q(assigned_to__isnull=True))
    from apps.sites.models import Site
    visible = [s.pk for s in Site.objects.for_organization(m.organization)
               if rbac.has_permission(m, "work_order.view", s) or
               rbac.has_permission(m, "work_order.view_assigned", s)]
    return qs.filter(site_id__in=visible)


def _can_view(membership, wo):
    if rbac.has_permission(membership, "work_order.view", wo.site):
        return True
    if rbac.has_permission(membership, "work_order.view_assigned", wo.site):
        if wo.assigned_to_id == membership.pk or wo.assigned_to_id is None:
            return True
        if wo.source_request_id and rbac.has_permission(membership, "incident.view", wo.site):
            return True
    return False


WORKFLOW_GUIDANCE = {
    WorkOrder.Status.DRAFT: "Next: plan the work and set its scheduled start and end.",
    WorkOrder.Status.PLANNED: "Next: assign an active, site-authorized technician.",
    WorkOrder.Status.ASSIGNED: "Next: dispatch the assigned work to the technician.",
    WorkOrder.Status.DISPATCHED: "Next: the assigned technician or an authorized dispatcher starts work.",
    WorkOrder.Status.IN_PROGRESS: "Next: place the work on hold or complete it with notes and validated checklist status.",
    WorkOrder.Status.ON_HOLD: "Next: resume work when the blocker is cleared.",
    WorkOrder.Status.COMPLETED: "Next: submit the completed work for supervisor review.",
    WorkOrder.Status.SUPERVISOR_REVIEW: "Next: approve and close the order, or return it to In Progress.",
    WorkOrder.Status.CLOSED: "This work order is closed.",
    WorkOrder.Status.CANCELLED: "This work order is cancelled.",
}

WORKFLOW_STEPS = tuple((code, label) for code, label in WorkOrder.Status.choices
                       if code != WorkOrder.Status.CANCELLED)
DETAIL_TABS = [
    ("overview", "Overview"), ("assignment", "Assignment"), ("checklist", "Checklist"),
    ("parts", "Parts"), ("labor", "Labor"), ("notes", "Notes"),
    ("attachments", "Attachments"), ("timeline", "Timeline"), ("closure", "Closure"),
]


class WorkOrderListView(TenantPermissionMixin, View):
    template_name = "workorder/list.html"

    def dispatch(self, request, *args, **kwargs):
        m = getattr(request, "membership", None)
        self.required_permission = "work_order.view" if m and rbac.has_permission_anywhere(m, "work_order.view") else "work_order.view_assigned"
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        qs = _orders(request)
        # Keep the distinction between an actually empty visible dataset and a
        # filtered query with no matches. Both remain within tenant/site scope.
        has_visible_work_orders = qs.exists()
        visible_sites = set(qs.values_list("site_id", flat=True).distinct())
        from apps.sites.models import Site
        sites = Site.objects.for_organization(request.organization).filter(pk__in=visible_sites).order_by("name")
        valid_site_ids = {str(site.pk) for site in sites}
        if request.GET.get("site") and request.GET["site"] not in valid_site_ids:
            site_id = ""
        else:
            site_id = request.GET.get("site", "")
        assignees = Membership.objects.for_organization(request.organization).filter(
            assigned_work_orders__in=qs).distinct().select_related("user").order_by("user__full_name")
        valid_assignee_ids = {str(member.pk) for member in assignees}
        assignee_id = request.GET.get("assignee", "")
        if assignee_id not in valid_assignee_ids:
            assignee_id = ""
        state = request.GET.get("status", "")
        if state not in dict(WorkOrder.Status.choices):
            state = ""
        search = request.GET.get("q", "").strip()
        priority = request.GET.get("priority", "")
        work_type = request.GET.get("work_type", "")
        overdue = request.GET.get("overdue", "")
        source = request.GET.get("source", "")
        if priority not in dict(WorkOrder.Priority.choices):
            priority = ""
        if work_type not in dict(WorkOrder.WorkType.choices):
            work_type = ""
        if source not in dict(WorkOrder.SourceType.choices):
            source = ""
        if overdue != "1":
            overdue = ""
        if priority in dict(WorkOrder.Priority.choices):
            qs = qs.filter(priority=priority)
        if work_type in dict(WorkOrder.WorkType.choices):
            qs = qs.filter(work_type=work_type)
        if site_id:
            qs = qs.filter(site_id=site_id)
        if assignee_id:
            qs = qs.filter(assigned_to_id=assignee_id)
        if overdue == "1":
            qs = qs.filter(due_at__lt=timezone.now()).exclude(
                status__in=[WorkOrder.Status.CLOSED, WorkOrder.Status.CANCELLED])
        if source in dict(WorkOrder.SourceType.choices):
            qs = qs.filter(source_type=source)
        if state:
            qs = qs.filter(status=state)
        if search:
            qs = qs.filter(Q(order_number__icontains=search) | Q(title__icontains=search)
                           | Q(asset_reference__icontains=search) | Q(source_reference__icontains=search))
        ordering = request.GET.get("ordering", "-created_at")
        allowed_ordering = {"created_at", "-created_at", "due_at", "-due_at", "priority", "-priority",
                            "planned_start", "-planned_start", "order_number", "-order_number"}
        if ordering not in allowed_ordering:
            ordering = "-created_at"
        qs = qs.order_by(ordering)
        card_statuses = [choice for choice in WorkOrder.Status.choices if choice[0] != WorkOrder.Status.CANCELLED]
        counts = {row["status"]: row["total"] for row in _orders(request).values("status").annotate(total=Count("pk"))}
        query = request.GET.copy()
        query.pop("page", None)
        status_cards = [{"code": code, "label": label, "count": counts.get(code, 0),
                         "selected": state == code}
                        for code, label in card_statuses]
        page_obj = Paginator(qs.select_related("site", "assigned_to__user"), 25).get_page(request.GET.get("page"))
        for order in page_obj.object_list:
            order.can_edit = (order.status in (WorkOrder.Status.DRAFT, WorkOrder.Status.PLANNED,
                                                WorkOrder.Status.ASSIGNED)
                              and rbac.has_permission(request.membership, "work_order.update", order.site))
            order.can_update_work = (order.status in (WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD)
                and rbac.has_permission(request.membership, "work_order.record", order.site)
                and (order.assigned_to_id == request.membership.pk or
                     rbac.has_permission(request.membership, "work_order.dispatch", order.site)))
            order.allowed_actions = services.allowed_actions(order, request.membership)
            order.is_overdue = bool(order.due_at and order.due_at < timezone.now()
                                    and order.status not in (WorkOrder.Status.CLOSED, WorkOrder.Status.CANCELLED))
        return render(request, self.template_name, {
            "page_obj": page_obj, "work_orders": page_obj.object_list, "status_cards": status_cards,
            "statuses": card_statuses, "priorities": WorkOrder.Priority.choices,
            "work_types": WorkOrder.WorkType.choices, "sites": sites, "assignees": assignees,
            "selected_status": state, "selected_priority": priority, "selected_work_type": work_type,
            "selected_site": site_id, "selected_assignee": assignee_id, "overdue": overdue,
            "selected_source": source, "source_types": WorkOrder.SourceType.choices,
              "selected_ordering": ordering, "q": search,
              "querystring": query.urlencode(),
              "has_visible_work_orders": has_visible_work_orders,
              "can_create": rbac.has_permission_anywhere(request.membership, "work_order.create"),
            "workflow_steps": WORKFLOW_STEPS})


class MyJobsView(TenantPermissionMixin, View):
    required_permission = "work_order.view_assigned"
    template_name = "workorder/mine.html"

    def get(self, request):
        qs = _orders(request).filter(assigned_to=request.membership).exclude(
            status__in=[WorkOrder.Status.CLOSED, WorkOrder.Status.CANCELLED])
        state = request.GET.get("status", "")
        if state in dict(WorkOrder.Status.choices):
            qs = qs.filter(status=state)
        orders = list(qs.select_related("site", "assigned_to__user").order_by("due_at", "planned_start")[:200])
        for order in orders:
            order.allowed_actions = services.allowed_actions(order, request.membership)
            order.overdue = bool(order.due_at and order.due_at < timezone.now())
            order.checklist_summary = get_checklist_summary(order)
            order.evidence_count = order.evidence_links.count()
            order.can_record_work = (order.status in (WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD)
                and rbac.has_permission(request.membership, "work_order.record", order.site))
        return render(request, self.template_name, {
            "work_orders": orders,
            "statuses": WorkOrder.Status.choices, "selected_status": state,
        })


class ScheduleBoardView(TenantPermissionMixin, View):
    template_name = "workorder/schedule.html"

    def dispatch(self, request, *args, **kwargs):
        m = getattr(request, "membership", None)
        self.required_permission = "work_order.view" if m and rbac.has_permission_anywhere(m, "work_order.view") else "work_order.view_assigned"
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        permitted_orders = _orders(request)
        from apps.sites.models import Site
        visible_site_list = [site for site in Site.objects.for_organization(request.organization)
                             if rbac.has_permission(request.membership, "work_order.view", site) or
                             rbac.has_permission(request.membership, "work_order.view_assigned", site)]
        site_ids = [site.pk for site in visible_site_list]
        board_statuses = [WorkOrder.Status.PLANNED, WorkOrder.Status.ASSIGNED,
                          WorkOrder.Status.DISPATCHED, WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD]
        default_statuses = [WorkOrder.Status.PLANNED, WorkOrder.Status.ASSIGNED, WorkOrder.Status.DISPATCHED]
        base_qs = permitted_orders.filter(status__in=board_statuses).select_related(
            "site", "asset", "assigned_to__user", "assignment").annotate(
                schedule_start=Coalesce("assignment__scheduled_start", "planned_start"),
                schedule_end=Coalesce("assignment__scheduled_end", "planned_end")
            ).order_by("schedule_start", "assigned_to__user__full_name")
        day = request.GET.get("day", "")
        try:
            selected_date = timezone.datetime.strptime(day, "%Y-%m-%d").date() if day else timezone.localdate()
        except ValueError:
            selected_date = timezone.localdate()
        day = selected_date.isoformat()
        technician = request.GET.get("technician", "")
        valid_technicians = {str(pk) for pk in Membership.objects.for_organization(request.organization)
                             .filter(membership_roles__role__system_key="technician").values_list("pk", flat=True)}
        if technician not in valid_technicians:
            technician = ""
        site_id = request.GET.get("site", "")
        valid_sites = {str(pk) for pk in site_ids}
        if site_id not in valid_sites:
            site_id = ""
        overdue = request.GET.get("overdue", "")
        status = request.GET.get("status", "")
        if status not in board_statuses:
            status = ""
        priority = request.GET.get("priority", "")
        if priority not in dict(WorkOrder.Priority.choices):
            priority = ""
        search = request.GET.get("q", "").strip()[:100]
        qs = base_qs.filter(status=status) if status else base_qs.filter(status__in=default_statuses)
        selected_site_ids = [site.pk for site in visible_site_list if not site_id or str(site.pk) == site_id]
        date_scope = Q(pk__in=[])
        for site in visible_site_list:
            if site.pk not in selected_site_ids:
                continue
            site_zone = _site_zone(site)
            local_start = timezone.make_aware(timezone.datetime.combine(selected_date, time.min), site_zone)
            local_end = timezone.make_aware(timezone.datetime.combine(selected_date + timedelta(days=1), time.min), site_zone)
            date_scope |= Q(site_id=site.pk, schedule_start__lt=local_end, schedule_end__gt=local_start)
        qs = qs.filter(date_scope)
        if technician:
            qs = qs.filter(assigned_to_id=technician)
        if site_id:
            qs = qs.filter(site_id=site_id)
        if priority:
            qs = qs.filter(priority=priority)
        if search:
            qs = qs.filter(
                Q(order_number__icontains=search) | Q(title__icontains=search)
                | Q(asset_reference__icontains=search) | Q(asset__asset_tag__icontains=search)
                | Q(assigned_to__user__full_name__icontains=search)
                | Q(assigned_to__user__email__icontains=search)
            )
        if overdue == "1":
            qs = qs.filter(due_at__lt=timezone.now())
        orders = list(qs)
        unassigned_orders = list(qs.filter(status=WorkOrder.Status.PLANNED, assigned_to__isnull=True))
        conflicts = set()
        assigned_ids = {order.assigned_to_id for order in orders if order.assigned_to_id}
        conflict_orders = list(permitted_orders.filter(
            status__in=board_statuses, assigned_to_id__in=assigned_ids,
            assignment__scheduled_start__isnull=False, assignment__scheduled_end__isnull=False
        ).select_related(None).annotate(
            conflict_start=Coalesce("assignment__scheduled_start", "planned_start"),
            conflict_end=Coalesce("assignment__scheduled_end", "planned_end"),
        ).only("pk", "assigned_to_id"))
        for index, first in enumerate(conflict_orders):
            if not first.assigned_to_id or not first.conflict_start or not first.conflict_end:
                continue
            for second in conflict_orders[index + 1:]:
                if (second.assigned_to_id == first.assigned_to_id and second.conflict_start and second.conflict_end
                        and first.conflict_start < second.conflict_end and first.conflict_end > second.conflict_start):
                    conflicts.update((first.pk, second.pk))
        grouped = {}
        for order in orders:
            order.schedule_day = selected_date
            order.overdue = bool(order.due_at and order.due_at < timezone.now()
                                 and order.status not in (WorkOrder.Status.CLOSED, WorkOrder.Status.CANCELLED))
            order.inactive_technician = bool(order.assigned_to_id and not order.assigned_to.is_active)
            order.has_conflict = order.pk in conflicts
            order.can_assign = (order.status == WorkOrder.Status.PLANNED
                                and rbac.has_permission(request.membership, "work_order.assign", order.site))
            order.can_dispatch = (order.status == WorkOrder.Status.ASSIGNED
                                  and rbac.has_permission(request.membership, "work_order.dispatch", order.site))
            order.can_reschedule = (order.status == WorkOrder.Status.ASSIGNED
                                    and rbac.has_permission(request.membership, "work_order.assign", order.site))
            order.can_start = (order.status == WorkOrder.Status.DISPATCHED and
                               any(item["operation"] == "start" for item in
                                   services.allowed_actions(order, request.membership)))
            order.has_active_assignment = hasattr(order, "assignment")
            order.assignable_technicians = available_technicians(order) if (order.can_assign or order.can_reschedule) else []
            order.schedule_warnings = schedule_warnings(order)
            order.due_display = (timezone.localtime(order.due_at, _site_zone(order.site)).strftime("%d %b %Y, %H:%M")
                                 if order.due_at else "—")
            if order.schedule_start and order.schedule_end:
                site_zone = _site_zone(order.site)
                day_start = timezone.make_aware(timezone.datetime.combine(selected_date, time.min), site_zone)
                day_end = timezone.make_aware(timezone.datetime.combine(selected_date + timedelta(days=1), time.min), site_zone)
                visible_start = max(order.schedule_start, day_start)
                visible_end = min(order.schedule_end, day_end)
                local_start = timezone.localtime(visible_start, site_zone)
                local_end = timezone.localtime(visible_end, site_zone)
                start_minutes = local_start.hour * 60 + local_start.minute
                end_minutes = 1440 if visible_end == day_end else local_end.hour * 60 + local_end.minute
                order.timeline_left = max(0, min(100, start_minutes / 1440 * 100))
                order.timeline_width = max(0.5, min(100 - order.timeline_left,
                                                    max(0, end_minutes - start_minutes) / 1440 * 100))
                order.timeline_start_label = local_start.strftime("%H:%M")
                order.timeline_end_label = local_end.strftime("%H:%M")
                scheduled_start_local = timezone.localtime(order.schedule_start, site_zone)
                scheduled_end_local = timezone.localtime(order.schedule_end, site_zone)
                order.schedule_start_display = scheduled_start_local.strftime("%d %b %Y, %H:%M")
                order.schedule_end_display = scheduled_end_local.strftime("%d %b %Y, %H:%M")
                order.schedule_start_input = scheduled_start_local.strftime("%Y-%m-%dT%H:%M")
                order.schedule_end_input = scheduled_end_local.strftime("%Y-%m-%dT%H:%M")
            else:
                order.timeline_left = order.timeline_width = 0
                order.timeline_start_label = order.timeline_end_label = ""
                order.schedule_start_display = order.schedule_end_display = "Not scheduled"
                order.schedule_start_input = order.schedule_end_input = ""
            key = (order.schedule_day, order.assigned_to_id)
            if order.status == WorkOrder.Status.PLANNED and not order.assigned_to_id:
                continue
            grouped.setdefault(key, {"day": order.schedule_day, "technician": order.assigned_to,
                                     "orders": []})["orders"].append(order)
        schedule_days = sorted(grouped.values(), key=lambda group: (
            group["day"] or timezone.localdate(),
            group["technician"].user.full_name if group["technician"] else "~Unassigned"))
        for group in schedule_days:
            lane_ends = []
            for order in sorted(group["orders"], key=lambda item: item.schedule_start or timezone.now()):
                if not order.schedule_start or not order.schedule_end:
                    order.timeline_lane = 0
                    continue
                start_at = order.schedule_start
                end_at = order.schedule_end
                lane = next((index for index, lane_end in enumerate(lane_ends) if lane_end <= start_at),
                            len(lane_ends))
                if lane == len(lane_ends):
                    lane_ends.append(end_at)
                else:
                    lane_ends[lane] = end_at
                order.timeline_lane = lane
            group["timeline_height"] = max(2, len(lane_ends)) * 1.65
        sites = Site.objects.for_organization(request.organization).filter(pk__in=site_ids).order_by("name")
        from apps.sites.models import OperatingCalendar
        visible_site_ids = selected_site_ids
        assigned_members = Membership.objects.for_organization(request.organization).filter(
            assigned_work_orders__in=base_qs).values_list("pk", flat=True)
        assigned_member_ids = set(assigned_members)
        technician_members = Membership.objects.for_organization(request.organization).filter(
            membership_roles__role__system_key="technician").filter(
            Q(pk__in=assigned_members) | Q(status=Membership.Status.ACTIVE, user__is_active=True)
        ).distinct().select_related("user")
        workload_rows = WorkOrder.objects.for_organization(request.organization).filter(
            assigned_to_id__in=technician_members.values_list("pk", flat=True),
            status__in=[WorkOrder.Status.ASSIGNED, WorkOrder.Status.DISPATCHED,
                        WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD],
        ).values("assigned_to_id").annotate(active_jobs=Count("pk"), workload_hours=Sum("estimated_hours"))
        workload_by_member = {row["assigned_to_id"]: row for row in workload_rows}
        visible_sites = Site.objects.for_organization(request.organization).filter(pk__in=visible_site_ids)
        busy_elsewhere = set(WorkOrder.objects.for_organization(request.organization).filter(
            assigned_to_id__in=[member.pk for member in technician_members],
            status__in=[WorkOrder.Status.ASSIGNED, WorkOrder.Status.DISPATCHED,
                        WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD],
        ).exclude(pk__in=[order.pk for order in orders]).values_list("assigned_to_id", flat=True))
        can_assign_anywhere = rbac.has_permission_anywhere(request.membership, "work_order.assign")
        technicians = [member for member in technician_members if member.pk in assigned_member_ids or (can_assign_anywhere and
            member.status == Membership.Status.ACTIVE and member.user.is_active and
            any(rbac.has_permission(member, "work_order.start", site)
                                                    for site in visible_sites))]
        for member in technicians:
            member.busy_elsewhere = member.pk in busy_elsewhere
            workload = workload_by_member.get(member.pk, {})
            member.active_job_count = workload.get("active_jobs", 0)
            member.workload_hours = workload.get("workload_hours") or 0
        calendars = OperatingCalendar.objects.filter(site_id__in=visible_site_ids, is_default=True).select_related("site")
        operating_calendars = {calendar.site_id: calendar for calendar in calendars}
        for order in orders:
            order.operating_calendar = operating_calendars.get(order.site_id)
        for order in unassigned_orders:
            order.can_assign = rbac.has_permission(request.membership, "work_order.assign", order.site)
            order.assignable_technicians = available_technicians(order) if order.can_assign else []
            for member in order.assignable_technicians:
                workload = workload_by_member.get(member.pk, {})
                member.active_job_count = workload.get("active_jobs", 0)
                member.workload_hours = workload.get("workload_hours") or 0
            order.overdue = bool(order.due_at and order.due_at < timezone.now())
            order.schedule_warnings = schedule_warnings(order)
            order.operating_calendar = operating_calendars.get(order.site_id)
            order.due_display = (timezone.localtime(order.due_at, _site_zone(order.site)).strftime("%d %b %Y, %H:%M")
                                 if order.due_at else "—")
            order.timeline_lane = 0
            order.overlapping_assignments = list(WorkOrderAssignment.objects.filter(
                organization=request.organization,
                work_order__in=permitted_orders,
                work_order__status__in=[WorkOrder.Status.ASSIGNED, WorkOrder.Status.DISPATCHED,
                                        WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD],
                scheduled_start__lt=order.schedule_end, scheduled_end__gt=order.schedule_start,
            ).exclude(work_order=order).select_related("work_order", "technician__user"))
        count_qs = base_qs.filter(date_scope)
        if technician:
            count_qs = count_qs.filter(assigned_to_id=technician)
        if site_id:
            count_qs = count_qs.filter(site_id=site_id)
        if priority:
            count_qs = count_qs.filter(priority=priority)
        if search:
            count_qs = count_qs.filter(
                Q(order_number__icontains=search) | Q(title__icontains=search)
                | Q(asset_reference__icontains=search) | Q(asset__asset_tag__icontains=search)
                | Q(assigned_to__user__full_name__icontains=search)
                | Q(assigned_to__user__email__icontains=search)
            )
        if overdue == "1":
            count_qs = count_qs.filter(due_at__lt=timezone.now())
        count_rows = {row["status"]: row["total"] for row in count_qs.filter(
            status__in=default_statuses).values("status").annotate(total=Count("pk"))}
        status_cards = [{"code": code, "label": label, "count": count_rows.get(code, 0)}
                        for code, label in WorkOrder.Status.choices if code in default_statuses]
        display_day = selected_date
        for member in technicians:
            if technician and str(member.pk) != technician:
                continue
            key = (display_day, member.pk)
            if key not in grouped:
                grouped[key] = {"day": display_day, "technician": member, "orders": [], "timeline_height": 3.3}
        schedule_days = sorted(grouped.values(), key=lambda group: (
            group["day"] or timezone.localdate(),
            group["technician"].user.full_name if group["technician"] else "~Unassigned"))
        for group in schedule_days:
            group["job_count"] = len(group["orders"])
            group["workload_hours"] = sum(float(order.estimated_hours or 0) for order in group["orders"])
            group["availability"] = ("Inactive" if group["technician"] and not group["technician"].is_active
                                      else "Scheduled" if group["orders"]
                                      else "Busy" if group["technician"] and group["technician"].busy_elsewhere
                                      else "Available")
            calendar_map = {order.operating_calendar.pk: order.operating_calendar
                            for order in group["orders"] if order.operating_calendar}
            if not calendar_map and len(visible_site_ids) == 1:
                calendar = operating_calendars.get(visible_site_ids[0])
                if calendar:
                    calendar_map[calendar.pk] = calendar
            group["operating_calendars"] = list(calendar_map.values())
            group["operating_note"] = ("Operating hours vary by site" if not calendar_map and len(visible_site_ids) > 1
                                        else "")
        today = timezone.localdate()
        filter_values = {key: value for key, value in {"technician": technician, "site": site_id,
                        "status": status, "priority": priority, "q": search, "overdue": overdue}.items() if value}
        status_filter_values = {key: value for key, value in filter_values.items() if key != "status"}
        context = {"schedule_days": schedule_days, "sites": sites, "technicians": technicians,
                   "selected_day": day, "selected_technician": technician, "selected_site": site_id,
                   "selected_status": status, "selected_priority": priority, "search": search,
                   "filter_values": filter_values, "previous_day": display_day - timezone.timedelta(days=1),
                   "status_filter_values": status_filter_values,
                   "next_day": display_day + timezone.timedelta(days=1), "today_day": today,
                   "overdue": overdue, "unassigned_orders": unassigned_orders,
                   "assigned_orders": [order for order in orders if order.assigned_to_id and
                       order.status in (WorkOrder.Status.ASSIGNED, WorkOrder.Status.DISPATCHED,
                                        WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD)],
                   "can_view_mine": rbac.has_permission_anywhere(request.membership, "work_order.view_assigned"),
                   "can_create": rbac.has_permission_anywhere(request.membership, "work_order.create"),
                   "status_choices": [(code, label) for code, label in WorkOrder.Status.choices if code in board_statuses],
                   "priority_choices": WorkOrder.Priority.choices,
                   "status_cards": status_cards,
                   "schedule_htmx_update": bool(request.headers.get("HX-Request")),
                   "schedule_error": request.GET.get("error", ""),
                   "schedule_success": ("Technician assigned and schedule refreshed." if request.GET.get("assigned") == "1"
                                        else "Work order dispatched and schedule refreshed." if request.GET.get("dispatched") == "1" else "")}
        template = "workorder/schedule_table.html" if request.headers.get("HX-Request") else self.template_name
        return render(request, template, context)


class ScheduleAssignmentView(TenantPermissionMixin, View):
    required_permission = "work_order.assign"

    def post(self, request, pk):
        try:
            wo = services.get_work_order(request.membership, pk)
        except NotFound as exc:
            raise Http404 from exc
        form = AssignForm(request.POST, membership=request.membership, site=wo.site, work_order=wo)
        # Keep the board's quick assignment action compatible: it uses the
        # planned window; the full Assign page lets an authorized planner adjust it.
        if "scheduled_start" not in request.POST and wo.planned_start and wo.planned_end:
            post_data = request.POST.copy()
            zone = _site_zone(wo.site)
            post_data["scheduled_start"] = timezone.localtime(wo.planned_start, zone).strftime("%Y-%m-%dT%H:%M")
            post_data["scheduled_end"] = timezone.localtime(wo.planned_end, zone).strftime("%Y-%m-%dT%H:%M")
            form = AssignForm(post_data, membership=request.membership, site=wo.site, work_order=wo)
        assigned_successfully = False
        filter_keys = ("day", "technician", "site", "status", "priority", "overdue", "q")
        filters = QueryDict("", mutable=True)
        for key in filter_keys:
            if request.POST.get(key):
                filters[key] = request.POST.get(key)
        if form.is_valid():
            try:
                zone = _site_zone(wo.site)
                scheduled_start = timezone.make_aware(timezone.datetime.strptime(
                    request.POST.get("scheduled_start") or form.data.get("scheduled_start"), "%Y-%m-%dT%H:%M"), zone)
                scheduled_end = timezone.make_aware(timezone.datetime.strptime(
                    request.POST.get("scheduled_end") or form.data.get("scheduled_end"), "%Y-%m-%dT%H:%M"), zone)
                services.assign_work_order(wo, membership=request.membership, actor=request.user,
                    assignee_id=form.cleaned_data["assigned_to"].pk,
                    scheduled_start=scheduled_start,
                    scheduled_end=scheduled_end,
                    expected_version=form.cleaned_data["expected_version"], request=request)
                assigned_successfully = True
                if not request.headers.get("HX-Request"):
                    messages.success(request, "Technician assigned and schedule refreshed.")
            except DomainError as exc:
                if not request.headers.get("HX-Request"):
                    messages.error(request, exc.message)
                    return redirect(f"{reverse('workorders:schedule')}?{filters.urlencode()}" if filters else reverse("workorders:schedule"))
                filters["error"] = exc.message
        elif not request.headers.get("HX-Request"):
            messages.error(request, "Select an active technician and refresh the schedule before assigning.")
            return redirect(f"{reverse('workorders:schedule')}?{filters.urlencode()}" if filters else reverse("workorders:schedule"))
        elif not form.is_valid():
            filters["error"] = "Select an active technician and refresh the schedule before assigning."
        if request.headers.get("HX-Request"):
            request.GET = filters
            if assigned_successfully:
                request.GET["assigned"] = "1"
            return ScheduleBoardView().get(request)
        return redirect(f"{reverse('workorders:schedule')}?{filters.urlencode()}" if filters else
                        reverse("workorders:schedule"))


class ScheduleRescheduleView(TenantPermissionMixin, View):
    required_permission = "work_order.assign"

    def post(self, request, pk):
        try:
            wo = services.get_work_order(request.membership, pk)
            if not _can_view(request.membership, wo):
                raise Http404
        except NotFound as exc:
            raise Http404 from exc
        filters = QueryDict("", mutable=True)
        for key in ("day", "technician", "site", "status", "priority", "overdue", "q"):
            if request.POST.get(key):
                filters[key] = request.POST.get(key)
        form = AssignForm(request.POST, membership=request.membership, site=wo.site, work_order=wo)
        if not form.is_valid():
            messages.error(request, "Check the technician and scheduled start and end times.")
            return redirect(f"{reverse('workorders:schedule')}?{filters.urlencode()}" if filters else
                            reverse("workorders:schedule"))
        zone = _site_zone(wo.site)
        start = timezone.make_aware(timezone.datetime.strptime(
            request.POST["scheduled_start"], "%Y-%m-%dT%H:%M"), zone)
        end = timezone.make_aware(timezone.datetime.strptime(
            request.POST["scheduled_end"], "%Y-%m-%dT%H:%M"), zone)
        try:
            services.reschedule_assigned_work_order(
                wo, membership=request.membership, actor=request.user,
                assignee_id=form.cleaned_data["assigned_to"].pk,
                scheduled_start=start, scheduled_end=end,
                expected_version=form.cleaned_data["expected_version"], request=request)
        except DomainError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, f"{wo.order_number} was rescheduled.")
        return redirect(f"{reverse('workorders:schedule')}?{filters.urlencode()}" if filters else
                        reverse("workorders:schedule"))


class ScheduleDispatchView(TenantPermissionMixin, View):
    required_permission = "work_order.dispatch"

    def post(self, request, pk):
        filter_keys = ("day", "technician", "site", "status", "priority", "overdue", "q")
        filters = QueryDict("", mutable=True)
        for key in filter_keys:
            if request.POST.get(key):
                filters[key] = request.POST.get(key)
        try:
            wo = services.get_work_order(request.membership, pk)
            if not _can_view(request.membership, wo):
                raise Http404
            services.dispatch_work_order(wo, membership=request.membership, actor=request.user,
                                         expected_version=request.POST.get("expected_version"), request=request)
        except NotFound as exc:
            raise Http404 from exc
        except DomainError as exc:
            messages.error(request, exc.message)
            return redirect(f"{reverse('workorders:schedule')}?{filters.urlencode()}" if filters else reverse("workorders:schedule"))
        if not request.headers.get("HX-Request"):
            messages.success(request, "Work order dispatched and schedule refreshed.")
        if request.headers.get("HX-Request"):
            request.GET = filters
            request.GET["dispatched"] = "1"
            return ScheduleBoardView().get(request)
        return redirect(f"{reverse('workorders:schedule')}?{filters.urlencode()}" if filters else reverse("workorders:schedule"))


class WorkOrderCreateView(TenantPermissionMixin, View):
    required_permission = "work_order.create"

    def get(self, request):
        form = CreateWorkOrderForm(membership=request.membership)
        return render(request, "workorder/form.html", self._context(request, form))

    @staticmethod
    def _context(request, form):
        sources = {str(sr.pk): {"kind": ("REQUEST" if sr.kind == "SERVICE_REQUEST" else "INCIDENT"),
                    "site": str(sr.site_id), "asset": str(sr.asset_id), "title": sr.title,
                    "description": sr.description,
                    "requester": (sr.reported_by.user.full_name or sr.reported_by.user.email)}
                   for sr in form.fields["source_request"].queryset.select_related("reported_by__user")}
        return {"form": form, "title": "Create work order", "submit": "Save Draft",
                "source_metadata": sources,
                "has_sites": form.fields["site"].queryset.exists(),
                "has_assets": form.fields["asset"].queryset.exists(),
                "has_source_requests": form.fields["source_request"].queryset.exists(),
                "can_create_site": rbac.has_permission_anywhere(request.membership, "site.create"),
                "can_create_asset": rbac.has_permission_anywhere(request.membership, "asset.create"),
                "can_create_incident": rbac.has_permission_anywhere(request.membership, "incident.create")}

    def post(self, request):
        form = CreateWorkOrderForm(request.POST, membership=request.membership)
        if form.is_valid():
            values = dict(form.cleaned_data)
            source_type = values.pop("source_type")
            source_request = values.pop("source_request")
            site = values.pop("site")
            asset = values.pop("asset")
            component = values.pop("component")
            try:
                if source_request is not None:
                    from apps.incidents import services as incident_services
                    wo = incident_services.create_work_order_for_request(
                        source_request, actor=request.user, membership=request.membership, request=request,
                        component=component.child if component else None, **values)
                else:
                    wo = services.create_work_order(request.organization, site_id=site.pk, actor=request.user,
                        membership=request.membership, request=request,
                        asset=asset, component=component.child if component else None,
                        source_type=source_type, **values)
            except DomainError as exc:
                form.add_error(None, exc.message)
            except ValidationError as exc:
                if hasattr(exc, "message_dict"):
                    for field, msgs in exc.message_dict.items():
                        field_name = field if field in form.fields else None
                        for msg in msgs:
                            form.add_error(field_name, msg)
                elif hasattr(exc, "messages"):
                    for msg in exc.messages:
                        form.add_error(None, msg)
                else:
                    form.add_error(None, str(exc))
            except IntegrityError as exc:
                logger.exception("Database integrity error creating work order: %s", exc)
                exc_str = str(exc)
                if "source_request" in exc_str.lower() or "source_id" in exc_str.lower():
                    form.add_error(None, "Could not create work order: a work order is already linked to this incident.")
                elif "uniq_wo_number_org" in exc_str.lower() or "order_number" in exc_str.lower() or "uniq_work_order_number_per_org" in exc_str.lower():
                    form.add_error(None, "Could not create work order: sequence number conflict. Please try saving again.")
                else:
                    form.add_error(None, f"Could not create work order: {exc_str.splitlines()[0] if exc_str else 'database integrity constraint error'}")
            except Exception as exc:
                logger.exception("Unexpected error creating work order: %s", exc)
                form.add_error(None, f"An unexpected error occurred while creating the work order: {exc}")
            else:
                messages.success(request, f"Work order {wo.order_number} created as Draft.")
                return redirect("workorders:detail", pk=wo.pk)
        return render(request, "workorder/form.html", self._context(request, form), status=400)


class WorkOrderEditView(TenantPermissionMixin, View):
    required_permission = "work_order.update"

    def _get_order(self, request, pk):
        try:
            return services.get_work_order(request.membership, pk)
        except NotFound as exc:
            raise Http404 from exc

    def _editable(self, request, wo):
        if not _can_view(request.membership, wo):
            raise Http404
        if not rbac.has_permission(request.membership, "work_order.update", wo.site):
            raise PermissionDenied
        if wo.status not in (WorkOrder.Status.DRAFT, WorkOrder.Status.PLANNED):
            raise PermissionDenied("Only Draft or Planned work orders can be edited here.")

    def get(self, request, pk):
        wo = self._get_order(request, pk)
        self._editable(request, wo)
        return render(request, "workorder/form.html", {
            "form": EditWorkOrderForm(membership=request.membership, work_order=wo),
            "wo": wo, "title": f"Edit {wo.order_number}", "submit": "Save Changes",
            "source_type_display": wo.get_source_type_display(),
            "source_reference_display": wo.source_request.number if wo.source_request_id else wo.source_reference,
            "source_record": wo.source_request,
            "checklist_summary": get_checklist_summary(wo),
        })

    def post(self, request, pk):
        wo = self._get_order(request, pk)
        self._editable(request, wo)
        form = EditWorkOrderForm(request.POST, membership=request.membership, work_order=wo)
        if form.is_valid():
            values = dict(form.cleaned_data)
            if "component" in values:
                component_link = values.pop("component")
                values["component"] = component_link.child if component_link else None
            try:
                updated = services.update_work_order(wo, membership=request.membership, actor=request.user,
                                                     request=request, **values)
            except DomainError as exc:
                form.add_error(None, exc.message)
            except ValidationError as exc:
                if hasattr(exc, "message_dict"):
                    for field, msgs in exc.message_dict.items():
                        field_name = field if field in form.fields else None
                        for msg in msgs:
                            form.add_error(field_name, msg)
                elif hasattr(exc, "messages"):
                    for msg in exc.messages:
                        form.add_error(None, msg)
                else:
                    form.add_error(None, str(exc))
            except IntegrityError as exc:
                logger.exception("Database integrity error updating work order: %s", exc)
                form.add_error(None, "Could not update work order: a database conflict occurred.")
            except Exception as exc:
                logger.exception("Unexpected error updating work order: %s", exc)
                form.add_error(None, f"An unexpected error occurred while updating the work order: {exc}")
            else:
                messages.success(request, f"Work order {updated.order_number} updated.")
                return redirect("workorders:detail", pk=updated.pk)
        return render(request, "workorder/form.html", {
            "form": form, "wo": wo, "title": f"Edit {wo.order_number}", "submit": "Save Changes",
            "source_type_display": wo.get_source_type_display(),
            "source_reference_display": wo.source_request.number if wo.source_request_id else wo.source_reference,
            "source_record": wo.source_request,
            "checklist_summary": get_checklist_summary(wo),
        }, status=400)


class WorkOrderDetailView(TenantPermissionMixin, View):
    def dispatch(self, request, *args, **kwargs):
        m = getattr(request, "membership", None)
        self.required_permission = "work_order.view" if m and rbac.has_permission_anywhere(m, "work_order.view") else "work_order.view_assigned"
        response = super().dispatch(request, *args, **kwargs)
        return response

    def get(self, request, pk):
        try:
            wo = services.get_work_order(request.membership, pk)
        except NotFound as exc:
            raise Http404 from exc
        if not _can_view(request.membership, wo):
            raise Http404
        active_tab = request.GET.get("tab", "overview")
        if active_tab not in TAB_TEMPLATES:
            active_tab = "overview"
        context = _work_order_detail_context(request, wo, active_tab)
        return render(request, "workorder/detail.html", context)


TAB_TEMPLATES = {key: f"workorder/tabs/{key}.html" for key, _ in DETAIL_TABS}


def _work_order_detail_context(request, wo, active_tab="overview"):
    if active_tab not in TAB_TEMPLATES:
        active_tab = "overview"
    can_record = rbac.has_permission(request.membership, "work_order.record", wo.site)
    can_execute = (wo.assigned_to_id == request.membership.pk or
                   rbac.has_permission(request.membership, "work_order.dispatch", wo.site))
    context = {"wo": wo, "detail_tabs": DETAIL_TABS, "active_tab": active_tab,
        "tab_template": TAB_TEMPLATES[active_tab],
        "workflow_guidance": WORKFLOW_GUIDANCE.get(wo.status, "No further workflow action is available."),
        "allowed_actions": services.allowed_actions(wo, request.membership),
        "can_update_work": (can_execute and can_record and
                            wo.status in (WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD)),
    }
    status_events = set(wo.events.exclude(to_status="").values_list("to_status", flat=True))
    next_statuses = {action.get("next_status") for action in context["allowed_actions"]}
    context["workflow_steps"] = [{"code": code, "label": label,
        "current": wo.status == code,
        "completed": code in status_events and code != wo.status,
        "next": code in next_statuses}
        for code, label in WORKFLOW_STEPS]
    context.update(_work_order_tab_context(wo, request.membership, active_tab))
    return context


def _work_order_tab_context(wo, membership, tab):
    """Build each detail tab from the same scoped work-order record."""
    can_execute = wo.assigned_to_id == membership.pk or rbac.has_permission(
        membership, "work_order.dispatch", wo.site)
    pm_cycle = None
    can_view_pm = False
    if wo.source_type in ("PREVENTIVE_MAINTENANCE", "PM"):
        from apps.maintenance.models import MaintenanceCycle
        pm_cycle = (
            MaintenanceCycle.objects.for_organization(wo.organization)
            .filter(work_order=wo)
            .select_related("schedule__plan")
            .first()
        )
        if pm_cycle:
            can_view_pm = rbac.has_permission(membership, "maintenance.view", wo.site)
    context = {"wo": wo, "pm_cycle": pm_cycle, "can_view_pm": can_view_pm, "permissions": {
        "update": rbac.has_permission(membership, "work_order.update", wo.site),
        "record": rbac.has_permission(membership, "work_order.record", wo.site),
        "attach": rbac.has_permission(membership, "work_order.attach", wo.site),
    }, "can_execute": can_execute,
       "checklist_summary": get_checklist_summary(wo),
       "provisional_checklist_enabled": getattr(settings, "WORKORDER_PROVISIONAL_CHECKLIST_ENABLED", False),
        "can_edit_checklist": (wo.status in (WorkOrder.Status.DRAFT, WorkOrder.Status.PLANNED) and
                              rbac.has_permission(membership, "work_order.update", wo.site)),
       "can_record_work": (wo.status in (WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.ON_HOLD) and
                           can_execute and rbac.has_permission(membership, "work_order.record", wo.site)),
       "can_attach_evidence": (wo.status not in (WorkOrder.Status.CLOSED, WorkOrder.Status.CANCELLED) and
                               rbac.has_permission(membership, "work_order.attach", wo.site)),
       "is_closed": wo.status == WorkOrder.Status.CLOSED,
       "due_is_overdue": bool(wo.due_at and wo.due_at < timezone.now() and
                               wo.status not in (WorkOrder.Status.CLOSED, WorkOrder.Status.CANCELLED))}
    if tab in ("assignment", "timeline", "closure"):
        context["events"] = wo.events.select_related("actor", "assigned_to__user")
    if tab == "checklist":
        from apps.checklists.models import ACTIVE, ChecklistTemplate, Inspection
        from apps.checklists import selectors as checklist_selectors

        insp = (
            Inspection.objects.for_organization(wo.organization)
            .filter(work_order=wo)
            .select_related("template", "started_by__user", "completed_by__user")
            .order_by("-started_at")
            .first()
        )
        context["inspection"] = insp
        can_execute_checklist = (
            wo.status == WorkOrder.Status.IN_PROGRESS
            and can_execute
            and rbac.has_permission(membership, "inspection.execute", wo.site)
        )
        context["can_execute_checklist"] = can_execute_checklist

        template = None
        if insp:
            template = insp.template
        elif wo.checklist_template_ref:
            template = ChecklistTemplate.objects.for_organization(wo.organization).filter(
                status=ACTIVE, name__icontains=wo.checklist_template_ref
            ).first()
            if not template:
                template = ChecklistTemplate.objects.for_organization(wo.organization).filter(
                    status=ACTIVE, work_type=wo.work_type
                ).first()
        context["checklist_template"] = template

        if template:
            items = list(checklist_selectors.items_for(wo.organization, template))
            responses = checklist_selectors.responses_for(wo.organization, insp) if insp else {}
            findings_by_item = {f.item_id: f for f in insp.findings.all()} if insp else {}
            rows = [{
                "item": it,
                "response": responses.get(it.pk),
                "finding": findings_by_item.get(it.pk),
            } for it in items]
            context["checklist_rows"] = rows
    if tab == "assignment":
        context["assignment_events"] = wo.events.filter(action="assigned").select_related(
            "actor", "assigned_to__user")
    elif tab == "parts":
        context["material_entries"] = wo.material_entries.all()
    elif tab == "labor":
        context["labor_entries"] = wo.labor_entries.select_related("technician__user")
        context["labor_total"] = wo.labor_entries.aggregate(total=Sum("hours"))["total"] or 0
    elif tab == "notes":
        context["work_updates"] = wo.events.filter(action="work_update").select_related("actor")
    elif tab == "attachments":
        context["evidence"] = wo.completion_evidence
    elif tab == "closure":
        context["closure_events"] = wo.events.filter(
            action__in=("closed", "review_rejected")).select_related("actor")
        context["approvals"] = wo.closure_approvals.select_related("approver")
    return context


class WorkOrderChecklistExecuteView(TenantPermissionMixin, View):
    """Initiates or resumes M08 checklist execution for a work order."""

    required_permission = "work_order.view_assigned"

    def dispatch(self, request, *args, **kwargs):
        m = getattr(request, "membership", None)
        self.required_permission = (
            "work_order.view"
            if m and rbac.has_permission_anywhere(m, "work_order.view")
            else "work_order.view_assigned"
        )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        return self._execute(request, pk)

    def post(self, request, pk):
        return self._execute(request, pk)

    def _execute(self, request, pk):
        try:
            wo = services.get_work_order(request.membership, pk)
        except NotFound as exc:
            raise Http404 from exc
        if not _can_view(request.membership, wo):
            raise Http404

        can_execute = (
            wo.assigned_to_id == request.membership.pk
            or rbac.has_permission(request.membership, "work_order.dispatch", wo.site)
        )
        if not can_execute:
            raise PermissionDenied("Only the assigned technician or a dispatcher can execute the checklist.")

        if not rbac.has_permission(request.membership, "inspection.execute", wo.site):
            raise PermissionDenied("You do not have permission to execute inspections at this site.")

        if wo.status != WorkOrder.Status.IN_PROGRESS:
            messages.warning(request, f"Work order must be In Progress to execute the checklist (currently {wo.get_status_display()}).")
            return redirect("workorders:detail", pk=wo.pk)

        from apps.checklists.models import ACTIVE, ChecklistTemplate, Inspection
        from apps.checklists import services as checklist_services

        insp = (
            Inspection.objects.for_organization(request.organization)
            .filter(work_order=wo)
            .order_by("-started_at")
            .first()
        )

        if not insp:
            template = None
            if wo.checklist_template_ref:
                template = ChecklistTemplate.objects.for_organization(request.organization).filter(
                    status=ACTIVE, name__icontains=wo.checklist_template_ref
                ).first()
                if not template:
                    try:
                        import uuid
                        template_uuid = uuid.UUID(wo.checklist_template_ref)
                        template = ChecklistTemplate.objects.for_organization(request.organization).filter(
                            status=ACTIVE, key=template_uuid
                        ).first()
                    except (ValueError, TypeError):
                        pass

            if not template:
                template = ChecklistTemplate.objects.for_organization(request.organization).filter(
                    status=ACTIVE, work_type=wo.work_type
                ).first()

            if not template:
                template = checklist_services.required_templates(wo).first()

            if not template:
                messages.error(request, f"No active checklist template found matching '{wo.checklist_template_ref or wo.work_type}'.")
                return redirect(f"{reverse('workorders:detail', args=[wo.pk])}?tab=checklist")

            try:
                insp = checklist_services.start_inspection(
                    request.organization,
                    template=template,
                    membership=request.membership,
                    actor=request.user,
                    work_order=wo,
                    request=request,
                )
            except DomainError as exc:
                messages.error(request, exc.message)
                return redirect(f"{reverse('workorders:detail', args=[wo.pk])}?tab=checklist")

        if wo.checklist_instance_ref != str(insp.pk) or wo.checklist_status != insp.status:
            wo.checklist_instance_ref = str(insp.pk)
            wo.checklist_status = insp.status
            wo.save(update_fields=["checklist_instance_ref", "checklist_status", "updated_at"])

        if insp.status == "IN_PROGRESS":
            return redirect("checklists:inspection_run", pk=insp.pk)
        return redirect("checklists:inspection", pk=insp.pk)


class WorkOrderTabView(TenantPermissionMixin, View):
    required_permission = "work_order.view"

    def dispatch(self, request, *args, **kwargs):
        m = getattr(request, "membership", None)
        self.required_permission = "work_order.view" if m and rbac.has_permission_anywhere(m, "work_order.view") else "work_order.view_assigned"
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk, tab):
        template_name = TAB_TEMPLATES.get(tab)
        if not template_name:
            raise Http404
        try:
            wo = services.get_work_order(request.membership, pk)
        except NotFound as exc:
            raise Http404 from exc
        if not _can_view(request.membership, wo):
            raise Http404
        context = _work_order_tab_context(wo, request.membership, tab)
        return render(request, template_name, context)


ACTION_PERMISSION = {
    "plan": "work_order.plan", "assign": "work_order.assign", "dispatch": "work_order.dispatch",
    "start": "work_order.start", "hold": "work_order.hold", "resume": "work_order.start",
    "complete": "work_order.complete", "submit_for_review": "work_order.complete",
    "review": "work_order.review",
    "close": "work_order.close", "return_for_rework": "work_order.review",
    "checklist": "work_order.update", "work_update": "work_order.record",
    "labor": "work_order.record", "materials": "work_order.record",
    "evidence": "work_order.attach",
}
ACTION_FORMS = {"plan": PlanForm, "assign": AssignForm, "hold": ReasonForm,
                "complete": CompleteForm, "review": ReviewForm, "checklist": ChecklistForm,
                "close": ClosureCommentForm, "return_for_rework": ClosureCommentForm,
                "work_update": WorkUpdateForm, "labor": LaborForm, "materials": MaterialForm, "evidence": EvidenceForm}


class WorkOrderActionView(TenantPermissionMixin, View):
    def dispatch(self, request, *args, **kwargs):
        self.operation = kwargs["operation"]
        self.required_permission = ACTION_PERMISSION.get(self.operation)
        if self.required_permission is None:
            raise Http404
        response = super().dispatch(request, *args, **kwargs)
        return response

    def _get_order(self, request, pk):
        try:
            wo = services.get_work_order(request.membership, pk)
        except NotFound as exc:
            raise Http404 from exc
        if not _can_view(request.membership, wo):
            raise Http404
        if not rbac.has_permission(request.membership, self.required_permission, wo.site):
            raise PermissionDenied
        return wo

    def get(self, request, pk, operation):
        wo = self._get_order(request, pk)
        if self.operation == "plan" and wo.status != WorkOrder.Status.DRAFT:
            raise PermissionDenied("Only a Draft work order can be planned.")
        form_type = ACTION_FORMS.get(self.operation)
        if not form_type:
            raise Http404
        kwargs = {}
        if self.operation == "assign":
            kwargs = {"membership": request.membership, "site": wo.site,
                      "work_order": wo,
                      "initial": {"expected_version": wo.version}}
        elif self.operation == "plan":
            kwargs = {"work_order": wo, "initial": {"work_type": wo.work_type, "priority": wo.priority,
                                   "planned_start": wo.planned_start, "planned_end": wo.planned_end,
                                   "due_at": wo.due_at,
                                   "estimated_hours": wo.estimated_hours if wo.estimated_hours and wo.estimated_hours > 0 else ""}}
        elif self.operation in ("plan", "hold", "complete", "review", "labor", "materials", "evidence", "checklist", "work_update"):
            kwargs = {}
        form = form_type(**kwargs)
        return render(request, "workorder/action.html", self._action_context(request, wo, form) | {
                       "wo": wo, "form": form,
                       "operation": self.operation.replace("_", " ").title(),
                       "expected_version": wo.version,
                       "is_close_action": self.operation == "close",
                       "scheduled_start": wo.planned_start if self.operation == "assign" else None,
                       "scheduled_end": wo.planned_end if self.operation == "assign" else None,
                       "labor_entries": wo.labor_entries.all(), "material_entries": wo.material_entries.all(),
                       "evidence_count": wo.evidence_links.count()})

    def _action_context(self, request, wo, form):
        evidence_count = wo.evidence_links.count()
        evidence_required = wo.work_type in (WorkOrder.WorkType.CORRECTIVE, WorkOrder.WorkType.INSTALLATION)
        checklist_summary = get_checklist_summary(wo)
        return {"evidence_count": evidence_count, "evidence_required": evidence_required,
                "checklist_summary": checklist_summary,
                "provisional_checklist_enabled": getattr(settings, "WORKORDER_PROVISIONAL_CHECKLIST_ENABLED", False),
                "return_to_complete": self.operation == "evidence" and request.GET.get("return_to") == "complete"}

    def post(self, request, pk, operation):
        wo = self._get_order(request, pk)
        form_type = ACTION_FORMS.get(self.operation)
        form_kwargs = {"membership": request.membership, "site": wo.site, "work_order": wo} if self.operation == "assign" else {}
        action_data = request.POST
        if (self.operation == "assign" and "scheduled_start" not in request.POST and
                wo.planned_start and wo.planned_end):
            action_data = request.POST.copy()
            action_data["scheduled_start"] = timezone.localtime(wo.planned_start).strftime("%Y-%m-%dT%H:%M")
            action_data["scheduled_end"] = timezone.localtime(wo.planned_end).strftime("%Y-%m-%dT%H:%M")
        form = (form_type(action_data, request.FILES if self.operation == "evidence" else None,
                          **form_kwargs) if form_type else None)
        if form is None:
            form = type("ActionForm", (), {"is_valid": lambda self: True, "cleaned_data": {}})()
        elif not form.is_valid():
            return render(request, "workorder/action.html", self._action_context(request, wo, form) | {
                           "wo": wo, "form": form,
                           "operation": self.operation.replace("_", " ").title(),
                           "expected_version": wo.version,
                           "is_close_action": self.operation == "close",
                           "scheduled_start": wo.planned_start if self.operation == "assign" else None,
                           "scheduled_end": wo.planned_end if self.operation == "assign" else None,
                           "labor_entries": wo.labor_entries.all(),
                           "material_entries": wo.material_entries.all(),
                           "evidence_count": wo.evidence_links.count()}, status=400)
        data = getattr(form, "cleaned_data", {})
        raw_version = data.pop("expected_version", request.POST.get("expected_version"))
        try:
            expected_version = int(raw_version) if raw_version not in (None, "") else None
            if expected_version is not None and expected_version < 1:
                raise ValueError
        except (TypeError, ValueError):
            messages.error(request, "Refresh this work order and retry the action.")
            return redirect("workorders:detail", pk=wo.pk)
        try:
            if self.operation == "plan":
                result = services.plan_work_order(wo, membership=request.membership, actor=request.user,
                    expected_version=expected_version, request=request, **data)
            elif self.operation == "assign":
                result = services.assign_work_order(wo, membership=request.membership, actor=request.user,
                    assignee_id=data["assigned_to"].pk, scheduled_start=data["scheduled_start"],
                    scheduled_end=data["scheduled_end"], expected_version=expected_version, request=request)
            elif self.operation == "dispatch": result = services.dispatch_work_order(wo, membership=request.membership, actor=request.user, expected_version=expected_version, request=request)
            elif self.operation == "start": result = services.start_work_order(wo, membership=request.membership, actor=request.user, expected_version=expected_version, request=request)
            elif self.operation == "hold": result = services.hold_work_order(wo, membership=request.membership, actor=request.user, reason=data["reason"], expected_version=expected_version, request=request)
            elif self.operation == "resume": result = services.resume_work_order(wo, membership=request.membership, actor=request.user, expected_version=expected_version, request=request)
            elif self.operation == "complete": result = services.complete_work_order(wo, membership=request.membership,
                actor=request.user, resolution_notes=data["resolution_notes"],
                expected_version=expected_version, request=request)
            elif self.operation == "submit_for_review": result = services.submit_for_review(
                wo, membership=request.membership, actor=request.user, expected_version=expected_version, request=request)
            elif self.operation == "review": result = services.review_work_order(wo, membership=request.membership,
                actor=request.user, approved=data["approved"], notes=data["notes"],
                expected_version=expected_version, request=request)
            elif self.operation == "close": result = services.close_work_order(
                wo, membership=request.membership, actor=request.user, comment=data["comment"],
                expected_version=expected_version, request=request)
            elif self.operation == "return_for_rework": result = services.return_for_rework(
                wo, membership=request.membership, actor=request.user, comment=data["comment"],
                expected_version=expected_version, request=request)
            elif self.operation == "checklist": result = services.set_checklist(wo, membership=request.membership,
                actor=request.user, items=data["items"], request=request)
            elif self.operation == "labor": result = services.record_labor(wo, membership=request.membership,
                actor=request.user, request=request, **data)
            elif self.operation == "work_update": result = services.record_work_update(
                wo, membership=request.membership, actor=request.user, update=data["update"], request=request)
            elif self.operation == "materials": result = services.record_material(wo, membership=request.membership,
                actor=request.user, request=request, **data)
            elif self.operation == "evidence":
                att = file_services.attach(request.FILES["file"], target=wo, organization=request.organization,
                    user=request.user, description=data.get("description", ""), read_permission="work_order.view", request=request)
                result = services.add_evidence(wo, membership=request.membership, actor=request.user,
                    attachment_id=att.pk, request=request)
            else: raise Http404
        except DomainError as exc:
            if request.headers.get("HX-Request"):
                response = HttpResponse("")
                response["HX-Reswap"] = "none"
                response["HX-Trigger"] = json.dumps({"workOrderActionError": {"message": exc.message}})
                return response
            if self.operation in ("plan", "complete", "labor", "materials"):
                # Keep entered work details on screen when a backend guard blocks
                # the action. Completion notes are committed with the transition;
                # labor/material rows are committed only after validation passes.
                form.add_error(None, exc.message)
                label = {"plan": "Plan", "complete": "Complete", "labor": "Labor", "materials": "Materials"}[self.operation]
                return render(request, "workorder/action.html", self._action_context(request, wo, form) | {
                    "wo": wo, "form": form,
                    "operation": label,
                    "expected_version": wo.version,
                    "is_close_action": False,
                    "labor_entries": wo.labor_entries.all(),
                    "material_entries": wo.material_entries.all(),
                    "evidence_count": wo.evidence_links.count(),
                }, status=400)
            messages.error(request, exc.message)
            return redirect("workorders:detail", pk=wo.pk)
        if self.operation == "assign":
            event = result.events.order_by("-created_at").first()
            for warning in (event.metadata.get("warnings", []) if event else []):
                messages.warning(request, warning)
        if isinstance(result, WorkOrder):
            success_message = f"Work order updated: {result.get_status_display()}."
        else:
            success_message = {
                "labor": "Labor entry recorded.",
                "materials": "Material usage recorded.",
                "evidence": "Evidence attached to the work order.",
            }.get(self.operation, "Work order record saved.")
        messages.success(request, success_message)
        if self.operation in ("labor", "materials"):
            tab = "labor" if self.operation == "labor" else "parts"
            return redirect(f"{reverse('workorders:detail', args=[wo.pk])}?tab={tab}")
        if self.operation == "evidence" and request.POST.get("return_to") == "complete":
            return redirect("workorders:action", pk=wo.pk, operation="complete")
        if request.headers.get("HX-Request") and isinstance(result, WorkOrder):
            current = services.get_work_order(request.membership, result.pk)
            target = request.headers.get("HX-Target", "")
            response_context = _work_order_detail_context(request, current)
            if target.startswith("workorder-"):
                current.allowed_actions = services.allowed_actions(current, request.membership)
                current.is_overdue = bool(current.due_at and current.due_at < timezone.now()
                    and current.status not in (WorkOrder.Status.CLOSED, WorkOrder.Status.CANCELLED))
                response = render(request, "workorder/_list_row.html", {"wo": current})
            else:
                response = render(request, "workorder/_workflow_panel.html", response_context)
            response["HX-Trigger"] = json.dumps({"workOrderChanged": {
                "id": str(current.pk), "status": current.status,
                "message": f"{current.order_number} updated: {current.get_status_display()}.",
            }})
            return response
        return redirect("workorders:detail", pk=wo.pk)
