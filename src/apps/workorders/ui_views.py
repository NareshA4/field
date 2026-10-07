from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
import json

from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View

from apps.core.exceptions import DomainError, NotFound
from apps.files import services as file_services
from apps.rbac import services as rbac
from apps.tenancy.models import Membership
from apps.ui.mixins import TenantPermissionMixin

from . import services
from .assignment_rules import available_technicians
from .forms import (AssignForm, ChecklistForm, ClosureCommentForm, CompleteForm, CreateWorkOrderForm, EditWorkOrderForm, EvidenceForm, LaborForm,
                    MaterialForm, PlanForm, ReasonForm, ReviewForm, WorkUpdateForm)
from .models import WorkOrder


def _orders(request):
    m = request.membership
    qs = WorkOrder.objects.for_organization(m.organization).select_related("site", "assigned_to__user", "created_by")
    if not rbac.has_permission_anywhere(m, "work_order.view"):
        qs = qs.filter(assigned_to=m)
    from apps.sites.models import Site
    visible = [s.pk for s in Site.objects.for_organization(m.organization)
               if rbac.has_permission(m, "work_order.view", s) or
               rbac.has_permission(m, "work_order.view_assigned", s)]
    return qs.filter(site_id__in=visible)


def _can_view(membership, wo):
    return (rbac.has_permission(membership, "work_order.view", wo.site) or
            (wo.assigned_to_id == membership.pk and
             rbac.has_permission(membership, "work_order.view_assigned", wo.site)))


WORKFLOW_GUIDANCE = {
    WorkOrder.Status.DRAFT: "Next: plan the work and set its scheduled start and end.",
    WorkOrder.Status.PLANNED: "Next: assign an active, site-authorized technician.",
    WorkOrder.Status.ASSIGNED: "Next: dispatch the assigned work to the technician.",
    WorkOrder.Status.DISPATCHED: "Next: the assigned technician or an authorized dispatcher starts work.",
    WorkOrder.Status.IN_PROGRESS: "Next: place the work on hold or complete it with notes and checklist results.",
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
            order.checklist_total = sum(1 for item in order.checklist_items if item.get("required", True))
            order.checklist_done = sum(1 for item in order.checklist_items
                                       if item.get("required", True) and item.get("completed"))
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
        qs = _orders(request).filter(
            status__in=[WorkOrder.Status.PLANNED, WorkOrder.Status.ASSIGNED]).select_related(
                "site", "assigned_to__user").order_by("planned_start", "assigned_to__user__full_name")
        day = request.GET.get("day", "")
        try:
            if day:
                timezone.datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            day = ""
        technician = request.GET.get("technician", "")
        valid_technicians = {str(pk) for pk in Membership.objects.for_organization(request.organization).values_list("pk", flat=True)}
        if technician not in valid_technicians:
            technician = ""
        site_id = request.GET.get("site", "")
        from apps.sites.models import Site
        valid_sites = {str(pk) for pk in Site.objects.for_organization(request.organization).values_list("pk", flat=True)}
        if site_id not in valid_sites:
            site_id = ""
        overdue = request.GET.get("overdue", "")
        if day:
            qs = qs.filter(planned_start__date=day)
        if technician:
            qs = qs.filter(assigned_to_id=technician)
        if site_id:
            qs = qs.filter(site_id=site_id)
        if overdue == "1":
            qs = qs.filter(due_at__lt=timezone.now())
        orders = list(qs)
        conflicts = set()
        for index, first in enumerate(orders):
            if not first.assigned_to_id or not first.planned_start or not first.planned_end:
                continue
            for second in orders[index + 1:]:
                if (second.assigned_to_id == first.assigned_to_id and second.planned_start and second.planned_end
                        and first.planned_start < second.planned_end and first.planned_end > second.planned_start):
                    conflicts.update((first.pk, second.pk))
        grouped = {}
        for order in orders:
            order.schedule_day = timezone.localtime(order.planned_start).date() if order.planned_start else None
            order.overdue = bool(order.due_at and order.due_at < timezone.now()
                                 and order.status not in (WorkOrder.Status.CLOSED, WorkOrder.Status.CANCELLED))
            order.inactive_technician = bool(order.assigned_to_id and not order.assigned_to.is_active)
            order.has_conflict = order.pk in conflicts
            order.can_assign = (order.status == WorkOrder.Status.PLANNED
                                and rbac.has_permission(request.membership, "work_order.assign", order.site))
            order.assignable_technicians = available_technicians(order) if order.can_assign else []
            key = (order.schedule_day, order.assigned_to_id)
            grouped.setdefault(key, {"day": order.schedule_day, "technician": order.assigned_to,
                                     "orders": []})["orders"].append(order)
        schedule_days = sorted(grouped.values(), key=lambda group: (
            group["day"] or timezone.localdate(),
            group["technician"].user.full_name if group["technician"] else "~Unassigned"))
        sites = Site.objects.for_organization(request.organization).filter(
            pk__in=qs.values_list("site_id", flat=True).distinct()).order_by("name")
        technicians = Membership.objects.for_organization(request.organization).filter(
            assigned_work_orders__in=qs).distinct().select_related("user")
        context = {"schedule_days": schedule_days, "sites": sites, "technicians": technicians,
                   "selected_day": day, "selected_technician": technician, "selected_site": site_id,
                   "overdue": overdue, "schedule_error": request.GET.get("error", ""),
                   "schedule_success": request.GET.get("assigned") == "1"}
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
        assigned_successfully = False
        if form.is_valid():
            try:
                services.assign_work_order(wo, membership=request.membership, actor=request.user,
                    assignee_id=form.cleaned_data["assigned_to"].pk,
                    expected_version=form.cleaned_data["expected_version"], request=request)
                assigned_successfully = True
            except DomainError as exc:
                if not request.headers.get("HX-Request"):
                    messages.error(request, exc.message)
                    return redirect("workorders:schedule")
                request.GET._mutable = True
                request.GET["error"] = exc.message
        elif not request.headers.get("HX-Request"):
            messages.error(request, "Select an active technician and refresh the schedule before assigning.")
            return redirect("workorders:schedule")
        elif not form.is_valid():
            request.GET._mutable = True
            request.GET["error"] = "Select an active technician and refresh the schedule before assigning."
        if request.headers.get("HX-Request"):
            request.GET._mutable = True
            if assigned_successfully:
                request.GET["assigned"] = "1"
            return ScheduleBoardView().get(request)
        return redirect("workorders:schedule")


class WorkOrderCreateView(TenantPermissionMixin, View):
    required_permission = "work_order.create"

    def get(self, request):
        form = CreateWorkOrderForm(membership=request.membership)
        return render(request, "workorder/form.html", self._context(request, form))

    @staticmethod
    def _context(request, form):
        source_priority = {"LOW": WorkOrder.Priority.LOW, "MEDIUM": WorkOrder.Priority.MEDIUM,
                           "HIGH": WorkOrder.Priority.HIGH, "CRITICAL": WorkOrder.Priority.CRITICAL}
        sources = {str(sr.pk): {"kind": ("REQUEST" if sr.kind == "SERVICE_REQUEST" else "INCIDENT"),
                    "site": str(sr.site_id), "asset": str(sr.asset_id),
                    "priority": source_priority[sr.severity]}
                   for sr in form.fields["source_request"].queryset}
        return {"form": form, "title": "Create work order", "submit": "Create work order",
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
            checklist = values.pop("checklist")
            try:
                if source_request is not None:
                    from apps.incidents import services as incident_services
                    wo = incident_services.create_work_order_for_request(
                        source_request, actor=request.user, membership=request.membership, request=request,
                        component=component.child if component else None,
                        checklist_items=checklist, **values)
                else:
                    wo = services.create_work_order(request.organization, site_id=site.pk, actor=request.user,
                        membership=request.membership, request=request, checklist_items=checklist,
                        asset=asset, component=component.child if component else None,
                        source_type=source_type, source_reference="", **values)
            except DomainError as exc:
                form.add_error(None, exc.message)
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
        if wo.status not in (WorkOrder.Status.DRAFT, WorkOrder.Status.PLANNED, WorkOrder.Status.ASSIGNED):
            raise PermissionDenied("Work-order details cannot be edited after dispatch.")

    def get(self, request, pk):
        wo = self._get_order(request, pk)
        self._editable(request, wo)
        return render(request, "workorder/form.html", {
            "form": EditWorkOrderForm(membership=request.membership, work_order=wo),
            "wo": wo, "title": f"Edit {wo.order_number}", "submit": "Save changes",
        })

    def post(self, request, pk):
        wo = self._get_order(request, pk)
        self._editable(request, wo)
        form = EditWorkOrderForm(request.POST, membership=request.membership, work_order=wo)
        if form.is_valid():
            values = dict(form.cleaned_data)
            component_link = values.pop("component", None)
            values["component"] = component_link.child if component_link else None
            try:
                updated = services.update_work_order(wo, membership=request.membership, actor=request.user,
                                                     request=request, **values)
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(request, f"Work order {updated.order_number} updated.")
                return redirect("workorders:detail", pk=updated.pk)
        return render(request, "workorder/form.html", {
            "form": form, "wo": wo, "title": f"Edit {wo.order_number}", "submit": "Save changes",
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
    context = {"wo": wo, "permissions": {
        "update": rbac.has_permission(membership, "work_order.update", wo.site),
        "record": rbac.has_permission(membership, "work_order.record", wo.site),
        "attach": rbac.has_permission(membership, "work_order.attach", wo.site),
    }, "can_execute": can_execute,
       "can_edit_checklist": (wo.status in (WorkOrder.Status.DRAFT, WorkOrder.Status.PLANNED,
                                              WorkOrder.Status.ASSIGNED) and
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
    "review": "work_order.review", "cancel": "work_order.cancel",
    "close": "work_order.close", "return_for_rework": "work_order.review",
    "checklist": "work_order.update", "work_update": "work_order.record",
    "labor": "work_order.record", "materials": "work_order.record",
    "evidence": "work_order.attach",
}
ACTION_FORMS = {"plan": PlanForm, "assign": AssignForm, "hold": ReasonForm, "cancel": ReasonForm,
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
        form_type = ACTION_FORMS.get(self.operation)
        if not form_type:
            raise Http404
        kwargs = {}
        if self.operation == "assign":
            kwargs = {"membership": request.membership, "site": wo.site,
                      "work_order": wo,
                      "initial": {"expected_version": wo.version}}
        elif self.operation == "plan":
            kwargs = {"initial": {"work_type": wo.work_type, "priority": wo.priority,
                                   "planned_start": wo.planned_start, "planned_end": wo.planned_end,
                                   "due_at": wo.due_at,
                                   "estimated_hours": wo.estimated_hours if wo.estimated_hours > 0 else ""}}
        elif self.operation == "complete":
            kwargs = {"work_order": wo}
        elif self.operation in ("plan", "hold", "cancel", "complete", "review", "labor", "materials", "evidence", "checklist", "work_update"):
            kwargs = {}
        form = form_type(**kwargs)
        return render(request, "workorder/action.html", {"wo": wo, "form": form,
                       "operation": self.operation.replace("_", " ").title(),
                       "expected_version": wo.version,
                       "is_close_action": self.operation == "close",
                       "scheduled_start": wo.planned_start if self.operation == "assign" else None,
                       "scheduled_end": wo.planned_end if self.operation == "assign" else None,
                       "labor_entries": wo.labor_entries.all(), "material_entries": wo.material_entries.all(),
                       "evidence_count": wo.evidence_links.count()})

    def post(self, request, pk, operation):
        wo = self._get_order(request, pk)
        form_type = ACTION_FORMS.get(self.operation)
        form_kwargs = {"membership": request.membership, "site": wo.site, "work_order": wo} if self.operation == "assign" else {}
        if self.operation == "complete":
            form_kwargs = {"work_order": wo}
        form = form_type(request.POST, **form_kwargs) if form_type else None
        if form is None:
            form = type("ActionForm", (), {"is_valid": lambda self: True, "cleaned_data": {}})()
        elif not form.is_valid():
            return render(request, "workorder/action.html", {"wo": wo, "form": form,
                           "operation": self.operation.replace("_", " ").title(),
                           "expected_version": wo.version,
                           "is_close_action": self.operation == "close"}, status=400)
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
                    assignee_id=data["assigned_to"].pk, expected_version=expected_version, request=request)
            elif self.operation == "dispatch": result = services.dispatch_work_order(wo, membership=request.membership, actor=request.user, expected_version=expected_version, request=request)
            elif self.operation == "start": result = services.start_work_order(wo, membership=request.membership, actor=request.user, expected_version=expected_version, request=request)
            elif self.operation == "hold": result = services.hold_work_order(wo, membership=request.membership, actor=request.user, reason=data["reason"], expected_version=expected_version, request=request)
            elif self.operation == "resume": result = services.resume_work_order(wo, membership=request.membership, actor=request.user, expected_version=expected_version, request=request)
            elif self.operation == "complete": result = services.complete_work_order(wo, membership=request.membership,
                actor=request.user, resolution_notes=data["resolution_notes"], checklist_items=data["checklist_items"],
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
            elif self.operation == "cancel": result = services.cancel_work_order(wo, membership=request.membership,
                actor=request.user, reason=data["reason"], expected_version=expected_version, request=request)
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
