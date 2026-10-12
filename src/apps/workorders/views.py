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
