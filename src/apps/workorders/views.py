<<<<<<< HEAD
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Q
from django.utils import timezone
from uuid import UUID

from apps.core.api import StandardPagination
from apps.rbac import services as rbac
from apps.sites.models import Site
from apps.tenancy.api import TenantAPIMixin

from . import services
from .models import WorkOrder, WorkOrderEvent
from .serializers import (AssignSerializer, ProvisionalChecklistSerializer, CompleteSerializer, CreateWorkOrderSerializer,
                         EvidenceSerializer, LaborInputSerializer, LaborSerializer, MaterialInputSerializer,
                         MaterialSerializer, PlanSerializer, WorkOrderReasonSerializer, ReviewSerializer,
                         ClosureCommentSerializer, WorkNoteSerializer, WorkOrderEventSerializer, WorkOrderSerializer)
from .serializers import ChecklistReferenceSerializer
from .serializers import ExpectedVersionSerializer
from .serializers import UpdateWorkOrderSerializer


class WorkOrderViewSet(TenantAPIMixin, viewsets.GenericViewSet):
    queryset = WorkOrder.objects.none()
    pagination_class = StandardPagination
    permission_map = {
        "create": "work_order.create", "partial_update": "work_order.update",
        "plan": "work_order.plan", "assign": "work_order.assign",
        "dispatch_order": "work_order.dispatch", "start": "work_order.start", "hold": "work_order.hold",
        "resume": "work_order.start", "complete": "work_order.complete",
        "validate_checklist": "work_order.complete",
        "submit_for_review": "work_order.complete", "review": "work_order.review",
        "close": "work_order.close", "return_for_rework": "work_order.review",
        "provisional_checklist": "work_order.update", "checklist_reference": "work_order.update", "labor": "work_order.record",
        "materials": "work_order.record", "evidence": "work_order.attach", "notes": "work_order.record",
    }

    def get_required_permission(self):
        if getattr(self, "action", None) in ("list", "retrieve", "events", "checklist_status", "validate_checklist"):
            m = getattr(self.request, "membership", None)
            if m and rbac.has_permission_anywhere(m, "work_order.view"):
                return "work_order.view"
            return "work_order.view_assigned"
        return super().get_required_permission()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return WorkOrder.objects.none()
        m = self.request.membership
        if not m:
            return WorkOrder.objects.none()
        qs = WorkOrder.objects.for_organization(m.organization).select_related("site", "assigned_to__user", "created_by")
        if not rbac.has_permission_anywhere(m, "work_order.view"):
            qs = qs.filter(assigned_to=m)
        site_ids = [site.pk for site in Site.objects.for_organization(m.organization)
                    if rbac.has_permission(m, "work_order.view", site) or
                    rbac.has_permission(m, "work_order.view_assigned", site)]
        return qs.filter(site_id__in=site_ids)

    def get_serializer_class(self):
        if self.action == "create":
            return CreateWorkOrderSerializer
        if self.action == "partial_update":
            return UpdateWorkOrderSerializer
        if self.action == "plan":
            return PlanSerializer
        if self.action == "assign":
            return AssignSerializer
        if self.action == "hold":
            return WorkOrderReasonSerializer
        if self.action in ("close", "return_for_rework"):
            return ClosureCommentSerializer
        if self.action == "notes":
            return WorkNoteSerializer
        if self.action == "complete":
            return CompleteSerializer
        if self.action == "review":
            return ReviewSerializer
        if self.action == "labor":
            return LaborInputSerializer
        if self.action == "materials":
            return MaterialInputSerializer
        if self.action == "provisional_checklist":
            return ProvisionalChecklistSerializer
        if self.action == "evidence":
            return EvidenceSerializer
        return WorkOrderSerializer

    @extend_schema(parameters=[
        OpenApiParameter("status", OpenApiTypes.STR, enum=[key for key, _ in WorkOrder.Status.choices]),
        OpenApiParameter("priority", OpenApiTypes.STR, enum=[key for key, _ in WorkOrder.Priority.choices]),
        OpenApiParameter("site", OpenApiTypes.UUID),
        OpenApiParameter("assignee", OpenApiTypes.UUID, description="Technician membership id"),
        OpenApiParameter("work_type", OpenApiTypes.STR, enum=[key for key, _ in WorkOrder.WorkType.choices]),
        OpenApiParameter("source", OpenApiTypes.STR, enum=[key for key, _ in WorkOrder.SourceType.choices]),
        OpenApiParameter("overdue", OpenApiTypes.BOOL),
        OpenApiParameter("q", OpenApiTypes.STR, description="Search number, title, asset, or source reference"),
        OpenApiParameter("ordering", OpenApiTypes.STR, description="Allowed ordering: created_at, due_at, priority, planned_start, order_number; prefix with - for descending"),
    ])
    def list(self, request):
        qs = self.get_queryset()
        state = request.query_params.get("status")
        if state in dict(WorkOrder.Status.choices):
            qs = qs.filter(status=state)
        priority = request.query_params.get("priority")
        if priority in dict(WorkOrder.Priority.choices):
            qs = qs.filter(priority=priority)
        work_type = request.query_params.get("work_type")
        if work_type in dict(WorkOrder.WorkType.choices):
            qs = qs.filter(work_type=work_type)
        source_type = request.query_params.get("source")
        if source_type in dict(WorkOrder.SourceType.choices):
            qs = qs.filter(source_type=source_type)
        site_id = request.query_params.get("site")
        if site_id:
            try:
                qs = qs.filter(site_id=UUID(site_id))
            except (ValueError, TypeError):
                return Response({"site": ["Enter a valid site id."]}, status=status.HTTP_400_BAD_REQUEST)
        assignee_id = request.query_params.get("assignee")
        if assignee_id:
            try:
                qs = qs.filter(assigned_to_id=UUID(assignee_id))
            except (ValueError, TypeError):
                return Response({"assignee": ["Enter a valid membership id."]}, status=status.HTTP_400_BAD_REQUEST)
        if request.query_params.get("overdue") in ("1", "true", "yes"):
            qs = qs.filter(due_at__lt=timezone.now()).exclude(
                status__in=[WorkOrder.Status.CLOSED, WorkOrder.Status.CANCELLED])
        search = request.query_params.get("q", "").strip()
        if search:
            qs = qs.filter(Q(order_number__icontains=search) | Q(title__icontains=search)
                           | Q(asset_reference__icontains=search) | Q(source_reference__icontains=search))
        ordering = request.query_params.get("ordering", "-created_at")
        allowed_ordering = {"created_at", "-created_at", "due_at", "-due_at", "priority", "-priority",
                            "planned_start", "-planned_start", "order_number", "-order_number"}
        if ordering in allowed_ordering:
            qs = qs.order_by(ordering)
        page = self.paginate_queryset(qs.distinct())
        return self.get_paginated_response(WorkOrderSerializer(page, many=True).data)

    def partial_update(self, request, pk=None):
        wo = self.get_object()
        serializer = self.get_serializer(wo, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = services.update_work_order(wo, membership=request.membership, actor=request.user,
                                             request=request, **serializer.validated_data)
        return Response(WorkOrderSerializer(updated).data)

    def retrieve(self, request, pk=None):
        wo = self.get_object()
        return Response(WorkOrderSerializer(wo).data)

    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        site = data.pop("site")
        asset = data.pop("asset")
        component = data.pop("component", None)
        source_type = data.pop("source_type")
        source_request = data.pop("source_request", None)
        if source_request is not None:
            from apps.incidents import services as incident_services
            wo = incident_services.create_work_order_for_request(
                source_request, actor=request.user, membership=request.membership, request=request,
                component=component, **data)
        else:
            data.pop("source_reference", None)
            wo = services.create_work_order(request.organization, site_id=site.pk, asset=asset,
                component=component, source_type=source_type, source_reference="",
                actor=request.user, membership=request.membership, request=request, **data)
        return Response(WorkOrderSerializer(wo).data, status=status.HTTP_201_CREATED)

    def _action(self, pk, fn, serializer=None):
        wo = self.get_object()
        data = {}
        input_serializer = serializer or ExpectedVersionSerializer
        s = input_serializer(data=self.request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data
        result = fn(wo, membership=self.request.membership, actor=self.request.user, request=self.request, **data)
        if isinstance(result, WorkOrder):
            return Response(WorkOrderSerializer(result).data)
        return Response({"id": str(result.pk), "work_order": str(wo.pk)}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def plan(self, request, pk=None):
        return self._action(pk, services.plan_work_order, PlanSerializer)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        return self._action(pk, lambda wo, **kw: services.assign_work_order(
            wo, assignee_id=kw.pop("assigned_to"), **kw), AssignSerializer)

    @action(detail=True, methods=["post"], url_path="dispatch")
    def dispatch_order(self, request, pk=None):
        return self._action(pk, services.dispatch_work_order)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        return self._action(pk, services.start_work_order)

    @action(detail=True, methods=["post"])
    def hold(self, request, pk=None):
        return self._action(pk, services.hold_work_order, WorkOrderReasonSerializer)

    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        return self._action(pk, services.resume_work_order)

    @extend_schema(description="IN_PROGRESS -> COMPLETED. Saves resolution notes after M08 checklist validation and work-type evidence requirements pass.")
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        return self._action(pk, services.complete_work_order, CompleteSerializer)

    @extend_schema(description="Assigned technician only: COMPLETED -> SUPERVISOR_REVIEW after checklist, notes, and evidence validation.")
    @action(detail=True, methods=["post"], url_path="submit-for-review")
    def submit_for_review(self, request, pk=None):
        return self._action(pk, services.submit_for_review)

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        """Compatibility decision endpoint; approved=true closes, false returns SUPERVISOR_REVIEW to IN_PROGRESS.

        New clients should call /close/ or /return-for-rework/ explicitly. All decisions share review_work_order().
        """
        return self._action(pk, services.review_work_order, ReviewSerializer)

    @extend_schema(request=ClosureCommentSerializer,
        description="Approve a SUPERVISOR_REVIEW work order and close it. Safe to retry; repeated close returns the existing closed record.",
        responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        return self._action(pk, services.close_work_order, ClosureCommentSerializer)

    @extend_schema(request=ClosureCommentSerializer,
        description="Return a SUPERVISOR_REVIEW work order to IN_PROGRESS with a required rework comment.",
        responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"], url_path="return-for-rework")
    def return_for_rework(self, request, pk=None):
        return self._action(pk, services.return_for_rework, ClosureCommentSerializer)

    @extend_schema(request=WorkNoteSerializer, responses=WorkOrderSerializer,
                   description="Append a technician progress note to the work-order event history.")
    @action(detail=True, methods=["post"])
    def notes(self, request, pk=None):
        return self._action(pk, lambda wo, **kw: services.record_work_update(
            wo, update=kw.pop("text"), **kw), WorkNoteSerializer)

    @extend_schema(description="Store M06 provisional development notes only; never used as M08 checklist answers or completion evidence.")
    @action(detail=True, methods=["post"], url_path="provisional-checklist")
    def provisional_checklist(self, request, pk=None):
        return self._action(pk, lambda wo, **kw: services.set_checklist(wo, items=kw.pop("items"), **kw),
                            ProvisionalChecklistSerializer)

    @extend_schema(responses=WorkOrderSerializer,
                   description="Register opaque M08 template and instance references. This leaves checklist status pending; it does not mark checklist completion.")
    @action(detail=True, methods=["post"], url_path="checklist-reference")
    def checklist_reference(self, request, pk=None):
        return self._action(pk, services.register_checklist_reference, ChecklistReferenceSerializer)

    @extend_schema(responses={200: dict}, description="Return M08 checklist references and validation state. This endpoint is read-only.")
    @action(detail=True, methods=["get"], url_path="checklist-status")
    def checklist_status(self, request, pk=None):
        wo = self.get_object()
        from .checklist_adapter import get_checklist_summary
        return Response(get_checklist_summary(wo))

    @extend_schema(responses={200: dict, 409: dict},
                   description="Evaluate the local adapter contract without contacting M08. Returns valid or pending metadata; it never creates checklist answers.")
    @action(detail=True, methods=["post"], url_path="validate-checklist")
    def validate_checklist(self, request, pk=None):
        wo = self.get_object()
        from .checklist_adapter import get_checklist_status, get_checklist_summary
        result = get_checklist_status(wo)
        return Response(get_checklist_summary(wo),
            status=status.HTTP_200_OK if result.is_valid else status.HTTP_409_CONFLICT)

    @action(detail=True, methods=["post"])
    def labor(self, request, pk=None):
        return self._action(pk, services.record_labor, LaborInputSerializer)

    @action(detail=True, methods=["post"])
    def materials(self, request, pk=None):
        return self._action(pk, services.record_material, MaterialInputSerializer)

    @action(detail=True, methods=["post"])
    def evidence(self, request, pk=None):
        return self._action(pk, services.add_evidence, EvidenceSerializer)

    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        wo = self.get_object()
        return Response(WorkOrderEventSerializer(wo.events.all(), many=True).data)
=======
"""M06 HTML views. Chain: login -> membership -> permission gate -> organization + site/assignment-scope restricted
lookup (404) -> permission for the order's site (403) -> service (state machine, validation, audit)."""
from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views import View

from apps.assets import selectors as asset_selectors
from apps.core.exceptions import DomainError
from apps.rbac import services as rbac
from apps.sites import selectors as site_selectors
from apps.sites.views import form_page, need, or404
from apps.tenancy.models import Membership
from apps.ui.mixins import TenantPermissionMixin

from . import selectors, services
from .forms import (
    CompleteForm,
    EvidenceForm,
    LaborForm,
    MaterialForm,
    PlanForm,
    TechnicianForm,
    WorkOrderForm,
)
from .models import WorkOrder
from .workflow import (
    ACTION_PERMISSIONS,
    BUTTON_STYLES,
    EXECUTION_ACTIONS,
    REASON_REQUIRED,
    TERMINAL_STATES,
    WORK_ORDER_STATUS,
)

TABS = [("overview", "Overview"), ("work", "Labor & material"), ("evidence", "Evidence"), ("history", "History")]
CONFIRMS = {"cancel": "Cancel this work order?", "close": "Close this work order?"}
FORM_KIND = {"plan": "plan", "assign": "assign", "complete": "complete"}


def _wo(request, pk, code=None) -> WorkOrder:
    wo = or404(selectors.get_work_order, request.membership, request.organization, pk)
    if code:
        need(request, code, wo.site_id)
    return wo


def _technicians(org, wo):
    """Active members who may execute work at the order's site."""
    members = Membership.objects.for_organization(org).filter(status=Membership.Status.ACTIVE).select_related("user")
    ids = [m.pk for m in members if rbac.has_permission(m, "work_order.start", wo.site_id)]
    return members.filter(pk__in=ids).order_by("user__full_name")


class WorkOrderBase(TenantPermissionMixin, View):
    required_permission = "work_order.view_assigned"


class WorkOrderListView(WorkOrderBase):
    def get(self, request):
        m, org = request.membership, request.organization
        qs = selectors.filter_work_orders(selectors.work_orders_for(m, org), request.GET, m)
        page = Paginator(qs, 20).get_page(request.GET.get("page"))
        return render(request, "workorders/list.html", {
            "page": page, "filters": request.GET, "statuses": WorkOrder.Status.choices,
            "priorities": WorkOrder.Priority.choices, "types": WorkOrder.WorkType.choices,
            "sites": site_selectors.sites_for(m, org, "work_order.view_assigned").order_by("code"),
            "can_create": rbac.has_permission_anywhere(m, "work_order.create")})


class WorkOrderCreateView(WorkOrderBase):
    required_permission = "work_order.create"
    crumbs = [("Work orders", "/app/work-orders/"), ("New", None)]

    def _assets(self, request):
        scope = rbac.site_scope(request.membership, "work_order.create")
        return scope.filter(asset_selectors.assets_for(request.membership, request.organization), "site_id")

    def get(self, request):
        form = WorkOrderForm(assets=self._assets(request), initial={"asset": request.GET.get("asset")})
        return form_page(request, title="New work order", form=form, submit="Create work order",
                         cancel_url="/app/work-orders/", crumbs=self.crumbs,
                         subtitle="Work from a breakdown request is created on the request page.")

    def post(self, request):
        form = WorkOrderForm(request.POST, assets=self._assets(request))
        if form.is_valid():
            d = dict(form.cleaned_data)
            asset = d.pop("asset")
            try:
                wo = services.create_work_order(request.organization, asset=asset, actor=request.user,
                                                request=request, **{k: v for k, v in d.items() if v not in (None, "")})
                messages.success(request, f"{wo.number} created.")
                return redirect("workorders:detail", pk=wo.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return form_page(request, title="New work order", form=form, submit="Create work order",
                         cancel_url="/app/work-orders/", crumbs=self.crumbs, status=400)


class WorkOrderEditView(WorkOrderBase):
    required_permission = "work_order.update"

    def _page(self, request, wo, form, status=200):
        return form_page(request, title=f"Edit {wo.number}", form=form, submit="Save", status=status,
                         cancel_url=f"/app/work-orders/{wo.pk}/",
                         crumbs=[("Work orders", "/app/work-orders/"), (wo.number, f"/app/work-orders/{wo.pk}/"),
                                 ("Edit", None)])

    def get(self, request, pk):
        wo = _wo(request, pk, "work_order.update")
        initial = {f: getattr(wo, f) for f in ("title", "description", "work_type", "priority", "planned_start",
                                                "planned_end", "estimated_hours")}
        return self._page(request, wo, WorkOrderForm(initial=initial, editing=True))

    def post(self, request, pk):
        wo = _wo(request, pk, "work_order.update")
        form = WorkOrderForm(request.POST, editing=True)
        if form.is_valid():
            try:
                services.update_work_order(wo, actor=request.user, request=request, **form.cleaned_data)
                messages.success(request, "Saved.")
                return redirect("workorders:detail", pk=wo.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self._page(request, wo, form, status=400)


def build_actions(request, wo) -> list[dict]:
    """The lifecycle buttons this caller may press right now (the server re-checks every one on POST)."""
    m = request.membership
    dispatcher = rbac.has_permission(m, "work_order.dispatch", wo.site_id)
    out = []
    for t in WORK_ORDER_STATUS.available(wo.status):
        if not rbac.has_permission(m, ACTION_PERMISSIONS[t.action], wo.site_id):
            continue
        if t.action in EXECUTION_ACTIONS and wo.assigned_to_id != m.pk and not dispatcher:
            continue
        out.append({"action": t.action, "label": t.label, "style": BUTTON_STYLES.get(t.action, "primary"),
                    "kind": FORM_KIND.get(t.action, "reason" if t.action in REASON_REQUIRED else "simple"),
                    "confirm": CONFIRMS.get(t.action), "url": f"/app/work-orders/{wo.pk}/transition/{t.action}/"})
    return out


class WorkOrderDetailView(WorkOrderBase):
    def get(self, request, pk):
        m, org = request.membership, request.organization
        wo = _wo(request, pk)
        tab = request.GET.get("tab", "overview")
        if tab not in dict(TABS):
            tab = "overview"
        can = {k: rbac.has_permission(m, code, wo.site_id) for k, code in (
            ("update", "work_order.update"), ("assign", "work_order.assign"), ("record", "work_order.record"),
            ("attach", "work_order.attach"), ("dispatch", "work_order.dispatch"),
            ("view_request", "incident.view"), ("view_pm", "maintenance.view"),
            ("export_evidence", "audit.export"))}
        actions = build_actions(request, wo)
        ctx = {"wo": wo, "tab": tab, "tabs": TABS, "can": can, "actions": actions,
               "editable": wo.status in ("DRAFT", "PLANNED"), "terminal": wo.status in TERMINAL_STATES}
        techs = _technicians(org, wo) if any(a["kind"] == "assign" for a in actions) or (
            can["assign"] and wo.status in ("ASSIGNED", "DISPATCHED")) else None
        for a in actions:
            if a["kind"] == "plan":
                a["form"] = PlanForm(initial={"planned_start": wo.planned_start, "planned_end": wo.planned_end,
                                              "estimated_hours": wo.estimated_hours, "priority": wo.priority})
            elif a["kind"] == "assign":
                a["form"] = TechnicianForm(technicians=techs)
            elif a["kind"] == "complete":
                a["form"] = CompleteForm(initial={"resolution_notes": wo.resolution_notes})
        if can["assign"] and wo.status in ("ASSIGNED", "DISPATCHED"):
            ctx["reassign_form"] = TechnicianForm(technicians=techs)
        if tab == "overview":
            from apps.sla import selectors as sla_selectors

            ctx["sla"] = sla_selectors.tracking_for_work_order(m, org, wo)
            if wo.source_type == "PREVENTIVE_MAINTENANCE":
                from apps.maintenance.models import MaintenanceCycle

                ctx["pm_cycle"] = MaintenanceCycle.objects.for_organization(org).filter(work_order=wo).select_related(
                    "schedule__plan").first()
            if wo.status in ("COMPLETED", "SUPERVISOR_REVIEW"):
                ctx["blockers"] = services.closure_blockers(wo)
            ctx["total_hours"] = selectors.total_hours(org, wo)
        elif tab == "work":
            dispatcher_or_reviewer = can["dispatch"] or rbac.has_permission(m, "work_order.review", wo.site_id)
            ctx["labor"] = selectors.labor_for(org, wo)
            ctx["materials"] = selectors.materials_for(org, wo)
            ctx["total_hours"] = selectors.total_hours(org, wo)
            ctx["labor_form"] = LaborForm(
                technicians=_technicians(org, wo) if dispatcher_or_reviewer else None)
            ctx["material_form"] = MaterialForm()
            ctx["recordable"] = can["record"] and wo.status in ("IN_PROGRESS", "ON_HOLD", "COMPLETED",
                                                                 "SUPERVISOR_REVIEW")
        elif tab == "evidence":
            ctx["evidence"] = services.evidence_for(wo).select_related("uploaded_by")
            ctx["evidence_form"] = EvidenceForm()
        elif tab == "history":
            ctx["events"] = selectors.events_for(org, wo)
        return render(request, "workorders/detail.html", ctx)


class _Post(WorkOrderBase):
    http_method_names = ["post"]
    tab = "overview"

    def done(self, request, wo, ok: str | None = None):
        if ok:
            messages.success(request, ok)
        return redirect(f"/app/work-orders/{wo.pk}/?tab={self.tab}")

    def fail(self, request, wo, exc):
        messages.error(request, exc.message if isinstance(exc, DomainError) else str(exc))
        return self.done(request, wo)


class WorkOrderTransitionView(_Post):
    def post(self, request, pk, action):
        wo = _wo(request, pk)
        if action not in ACTION_PERMISSIONS:
            messages.error(request, "Unknown action.")
            return self.done(request, wo)
        need(request, ACTION_PERMISSIONS[action], wo.site_id)
        data = {}
        try:
            if action == "plan":
                form = PlanForm(request.POST)
                if not form.is_valid():
                    messages.error(request, "Enter valid planned start / end and hours.")
                    return self.done(request, wo)
                data = {k: v for k, v in form.cleaned_data.items() if v not in (None, "")}
            elif action == "assign":
                form = TechnicianForm(request.POST, technicians=Membership.objects.for_organization(
                    request.organization).select_related("user"))
                if not form.is_valid():
                    messages.error(request, "Choose a technician.")
                    return self.done(request, wo)
                data = {"technician": form.cleaned_data["technician"]}
            elif action == "complete":
                form = CompleteForm(request.POST)
                if not form.is_valid():
                    messages.error(request, "Resolution notes (at least 10 characters) are required.")
                    return self.done(request, wo)
                data = {"resolution_notes": form.cleaned_data["resolution_notes"]}
            services.transition(wo, action=action, reason=request.POST.get("reason", ""), actor=request.user,
                                membership=request.membership, request=request, **data)
            return self.done(request, wo, "Work order updated.")
        except DomainError as exc:
            return self.fail(request, wo, exc)


class WorkOrderReassignView(_Post):
    def post(self, request, pk):
        wo = _wo(request, pk, "work_order.assign")
        form = TechnicianForm(request.POST, technicians=Membership.objects.for_organization(
            request.organization).select_related("user"))
        if not form.is_valid():
            messages.error(request, "Choose a technician and give a reason.")
            return self.done(request, wo)
        try:
            services.reassign(wo, technician=form.cleaned_data["technician"], reason=form.cleaned_data["reason"],
                              actor=request.user, request=request)
            return self.done(request, wo, "Technician changed.")
        except DomainError as exc:
            return self.fail(request, wo, exc)


class WorkOrderLaborView(_Post):
    tab = "work"

    def post(self, request, pk):
        wo = _wo(request, pk, "work_order.record")
        m = request.membership
        dispatcher_or_reviewer = rbac.has_permission(m, "work_order.dispatch", wo.site_id) or rbac.has_permission(
            m, "work_order.review", wo.site_id)
        form = LaborForm(request.POST, technicians=Membership.objects.for_organization(
            request.organization).select_related("user") if dispatcher_or_reviewer else None)
        if not form.is_valid():
            messages.error(request, "Enter a valid date and hours (0.01 - 24).")
            return self.done(request, wo)
        d = form.cleaned_data
        try:
            services.record_labor(wo, technician=d.get("technician") or m, work_date=d["work_date"],
                                  hours=d["hours"], notes=d["notes"], actor=request.user, membership=m,
                                  request=request)
            return self.done(request, wo, "Labor recorded.")
        except DomainError as exc:
            return self.fail(request, wo, exc)


class WorkOrderMaterialView(_Post):
    tab = "work"

    def post(self, request, pk):
        wo = _wo(request, pk, "work_order.record")
        form = MaterialForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Describe the material and enter a quantity above zero.")
            return self.done(request, wo)
        d = form.cleaned_data
        try:
            services.record_material(wo, description=d["description"], quantity=d["quantity"], unit=d["unit"],
                                     part_number=d["part_number"], actor=request.user, membership=request.membership,
                                     request=request)
            return self.done(request, wo, "Material recorded.")
        except DomainError as exc:
            return self.fail(request, wo, exc)


class WorkOrderEvidenceView(_Post):
    tab = "evidence"

    def post(self, request, pk):
        wo = _wo(request, pk, "work_order.attach")
        form = EvidenceForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Choose a file to upload.")
            return self.done(request, wo)
        try:
            services.add_evidence(wo, form.cleaned_data["file"], description=form.cleaned_data["description"],
                                  actor=request.user, membership=request.membership, request=request)
            return self.done(request, wo, "Evidence uploaded.")
        except DomainError as exc:
            return self.fail(request, wo, exc)
>>>>>>> ba3f639 (feat(m08): implement Inspection & Checklist Engine with Work Order integration)
