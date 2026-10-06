from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from apps.assets import selectors as asset_selectors
from apps.core.apiutils import ID_PARAM, paginate, query_param, require_permission
from apps.core.exceptions import NotFound
from apps.sites.selectors import scoped_get
from apps.tenancy.api import TenantAPIMixin
from apps.tenancy.models import Membership

from . import selectors, services
from .models import WorkOrder, WorkOrderLabor, WorkOrderMaterial
from .workflow import ACTION_PERMISSIONS, WORK_ORDER_STATUS


class WorkOrderSerializer(serializers.ModelSerializer):
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    asset_name = serializers.CharField(source="asset.name", read_only=True)
    site_code = serializers.CharField(source="site.code", read_only=True)
    assigned_to_name = serializers.SerializerMethodField()
    source_request_number = serializers.CharField(source="source_request.number", read_only=True, default=None)
    available_actions = serializers.SerializerMethodField()
    total_hours = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrder
        fields = ["id", "number", "title", "description", "work_type", "priority", "status", "asset", "asset_tag",
                  "asset_name", "site", "site_code", "source_request", "source_request_number", "source_type",
                  "source_id", "planned_start",
                  "planned_end", "estimated_hours", "assigned_to", "assigned_to_name", "dispatched_at", "started_at",
                  "completed_at", "review_started_at", "closed_at", "hold_reason", "resolution_notes",
                  "available_actions", "total_hours", "created_at", "updated_at"]
        read_only_fields = fields

    def get_assigned_to_name(self, obj) -> str | None:
        return obj.assigned_to.user.display_name if obj.assigned_to_id else None

    def get_available_actions(self, obj) -> list[str]:
        return [t.action for t in WORK_ORDER_STATUS.available(obj.status)]

    def get_total_hours(self, obj) -> str:
        return str(selectors.total_hours(obj.organization, obj))


class WorkOrderWriteSerializer(serializers.Serializer):
    asset = serializers.UUIDField()
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    work_type = serializers.ChoiceField(choices=WorkOrder.WorkType.choices, required=False)
    priority = serializers.ChoiceField(choices=WorkOrder.Priority.choices, required=False)
    planned_start = serializers.DateTimeField(required=False, allow_null=True)
    planned_end = serializers.DateTimeField(required=False, allow_null=True)
    estimated_hours = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)


class WorkOrderPatchSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    work_type = serializers.ChoiceField(choices=WorkOrder.WorkType.choices, required=False)
    priority = serializers.ChoiceField(choices=WorkOrder.Priority.choices, required=False)
    planned_start = serializers.DateTimeField(required=False, allow_null=True)
    planned_end = serializers.DateTimeField(required=False, allow_null=True)
    estimated_hours = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)

    def validate(self, attrs):
        extra = set(getattr(self, "initial_data", {})) - set(self.fields)
        if "status" in extra:
            raise serializers.ValidationError({"status": "Status cannot be edited; use POST /work-orders/{id}/transition/."})
        if extra:
            raise serializers.ValidationError({k: "This field cannot be edited." for k in sorted(extra)})
        return attrs


class WorkOrderTransitionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=sorted(ACTION_PERMISSIONS))
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)
    planned_start = serializers.DateTimeField(required=False, allow_null=True)
    planned_end = serializers.DateTimeField(required=False, allow_null=True)
    estimated_hours = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    priority = serializers.ChoiceField(choices=WorkOrder.Priority.choices, required=False)
    technician = serializers.UUIDField(required=False, help_text="Membership id (assign)")
    resolution_notes = serializers.CharField(required=False, allow_blank=True, help_text="Required to complete")


class ReassignSerializer(serializers.Serializer):
    technician = serializers.UUIDField()
    reason = serializers.CharField(max_length=500)


class LaborSerializer(serializers.ModelSerializer):
    technician_name = serializers.CharField(source="technician.user.display_name", read_only=True)

    class Meta:
        model = WorkOrderLabor
        fields = ["id", "technician", "technician_name", "work_date", "hours", "notes", "created_at"]
        read_only_fields = fields


class LaborWriteSerializer(serializers.Serializer):
    technician = serializers.UUIDField(required=False, help_text="Membership id; defaults to the caller")
    work_date = serializers.DateField()
    hours = serializers.DecimalField(max_digits=5, decimal_places=2)
    notes = serializers.CharField(max_length=300, required=False, allow_blank=True)


class MaterialSerializer(serializers.ModelSerializer):
    stock_backed = serializers.SerializerMethodField(help_text="True when written by M09 consumption of issued stock")

    class Meta:
        model = WorkOrderMaterial
        fields = ["id", "description", "part_number", "quantity", "unit", "stock_backed", "created_at"]
        read_only_fields = fields


    def get_stock_backed(self, obj) -> bool:
        return obj.part_line_id is not None


class MaterialWriteSerializer(serializers.Serializer):
    description = serializers.CharField(max_length=200)
    part_number = serializers.CharField(max_length=60, required=False, allow_blank=True)
    quantity = serializers.DecimalField(max_digits=10, decimal_places=3)
    unit = serializers.CharField(max_length=20, required=False, allow_blank=True)


class EventSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    action = serializers.CharField()
    from_status = serializers.CharField()
    to_status = serializers.CharField()
    reason = serializers.CharField()
    actor = serializers.SerializerMethodField()
    assigned_to = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    def get_actor(self, obj) -> str | None:
        return obj.actor.email if obj.actor_id else None

    def get_assigned_to(self, obj) -> str | None:
        return obj.assigned_to.user.display_name if obj.assigned_to_id else None


class WorkOrderEvidenceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    original_name = serializers.CharField()
    mime_type = serializers.CharField()
    size = serializers.IntegerField()
    description = serializers.CharField()
    uploaded_by = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    def get_uploaded_by(self, obj) -> str:
        return obj.uploaded_by.email


class WorkOrderEvidenceUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    description = serializers.CharField(max_length=200, required=False, allow_blank=True)


def _validated(serializer_cls, request, partial=False):
    ser = serializer_cls(data=request.data, partial=partial)
    ser.is_valid(raise_exception=True)
    return ser.validated_data


def _member(org, pk, what="Technician") -> Membership:
    try:
        return Membership.objects.for_organization(org).select_related("user").get(pk=pk)
    except Membership.DoesNotExist as exc:
        raise NotFound(f"{what} not found.") from exc


@extend_schema(parameters=[ID_PARAM])
class WorkOrderViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {
        "list": "work_order.view_assigned", "retrieve": "work_order.view_assigned", "create": "work_order.create",
        "partial_update": "work_order.update", "transition": "work_order.view_assigned",
        "assign": "work_order.view_assigned", "start": "work_order.view_assigned",
        "hold": "work_order.view_assigned", "complete": "work_order.view_assigned",
        "reassign": "work_order.assign", "events": "work_order.view_assigned",
        "closure": "work_order.view_assigned", "labor:get": "work_order.view_assigned",
        "labor:post": "work_order.record", "materials:get": "work_order.view_assigned",
        "materials:post": "work_order.record", "evidence:get": "work_order.view_assigned",
        "evidence:post": "work_order.attach",
    }

    def _qs(self, request):
        return selectors.work_orders_for(request.membership, request.organization)

    def _obj(self, request, pk, code=None) -> WorkOrder:
        wo = scoped_get(self._qs(request), pk, "Work order")  # 404 outside tenant / site / assignment scope
        if code:
            require_permission(request.membership, code, wo.site_id)
        return wo

    @extend_schema(
        parameters=[query_param("site", "site id", OpenApiTypes.UUID), query_param("asset", "asset id",
                    OpenApiTypes.UUID), query_param("status", "work-order status"), query_param("priority", "priority"),
                    query_param("work_type", "work type"), query_param("assigned_to", "membership id",
                    OpenApiTypes.UUID), query_param("mine", "1 = only my assignments"),
                    query_param("q", "number / title / asset"), query_param("ordering", "-created_at, priority, ...")],
        responses=WorkOrderSerializer(many=True))
    def list(self, request):
        qs = selectors.filter_work_orders(self._qs(request), request.query_params, request.membership)
        return paginate(request, qs, WorkOrderSerializer)

    @extend_schema(responses=WorkOrderSerializer)
    def retrieve(self, request, pk=None):
        return Response(WorkOrderSerializer(self._obj(request, pk)).data)

    @extend_schema(request=WorkOrderWriteSerializer, responses={201: WorkOrderSerializer})
    def create(self, request):
        d = dict(_validated(WorkOrderWriteSerializer, request))
        asset = asset_selectors.get_asset(request.membership, request.organization, d.pop("asset"))
        require_permission(request.membership, "work_order.create", asset.site_id)
        wo = services.create_work_order(request.organization, asset=asset, actor=request.user, request=request,
                                        **d)
        return Response(WorkOrderSerializer(self._obj(request, wo.pk)).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=WorkOrderPatchSerializer, responses=WorkOrderSerializer)
    def partial_update(self, request, pk=None):
        wo = self._obj(request, pk, "work_order.update")
        d = _validated(WorkOrderPatchSerializer, request, partial=True)
        services.update_work_order(wo, actor=request.user, request=request, **d)
        return Response(WorkOrderSerializer(self._obj(request, pk)).data)

    @extend_schema(request=WorkOrderTransitionSerializer, responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        return self._transition(request, pk, dict(_validated(WorkOrderTransitionSerializer, request)))

    # HPE API group "Work Orders: /assign/, /start/, /hold/, /complete/": the same guarded transition, one URL per action
    def _shortcut(self, request, pk, act):
        body = {k: v for k, v in (request.data.items() if hasattr(request.data, "items") else [])}
        ser = WorkOrderTransitionSerializer(data={**body, "action": act})
        ser.is_valid(raise_exception=True)
        return self._transition(request, pk, dict(ser.validated_data))

    @extend_schema(request=OpenApiTypes.OBJECT, responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        return self._shortcut(request, pk, "assign")

    @extend_schema(request=OpenApiTypes.OBJECT, responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        return self._shortcut(request, pk, "start")

    @extend_schema(request=OpenApiTypes.OBJECT, responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"])
    def hold(self, request, pk=None):
        return self._shortcut(request, pk, "hold")

    @extend_schema(request=OpenApiTypes.OBJECT, responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        return self._shortcut(request, pk, "complete")

    def _transition(self, request, pk, d):
        wo = self._obj(request, pk)
        act = d.pop("action")
        require_permission(request.membership, ACTION_PERMISSIONS[act], wo.site_id)
        reason = d.pop("reason", "")
        if "technician" in d:
            d["technician"] = _member(request.organization, d["technician"])
        services.transition(wo, action=act, reason=reason, actor=request.user, membership=request.membership,
                            request=request, **d)
        return Response(WorkOrderSerializer(self._obj(request, pk)).data)

    @extend_schema(request=ReassignSerializer, responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"])
    def reassign(self, request, pk=None):
        wo = self._obj(request, pk, "work_order.assign")
        d = _validated(ReassignSerializer, request)
        tech = _member(request.organization, d["technician"])
        services.reassign(wo, technician=tech, reason=d["reason"], actor=request.user, request=request)
        return Response(WorkOrderSerializer(self._obj(request, pk)).data)

    @extend_schema(responses=EventSerializer(many=True))
    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        wo = self._obj(request, pk)
        return paginate(request, selectors.events_for(request.organization, wo), EventSerializer)

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["get"])
    def closure(self, request, pk=None):
        """Why the order cannot be closed yet (empty list = closable)."""
        wo = self._obj(request, pk)
        blockers = services.closure_blockers(wo)
        return Response({"closable": not blockers and wo.status == "SUPERVISOR_REVIEW", "blockers": blockers})

    @extend_schema(methods=["GET"], responses=LaborSerializer(many=True))
    @extend_schema(methods=["POST"], request=LaborWriteSerializer, responses={201: LaborSerializer})
    @action(detail=True, methods=["get", "post"])
    def labor(self, request, pk=None):
        wo = self._obj(request, pk)
        if request.method == "GET":
            return paginate(request, selectors.labor_for(request.organization, wo), LaborSerializer)
        require_permission(request.membership, "work_order.record", wo.site_id)
        d = _validated(LaborWriteSerializer, request)
        tech = _member(request.organization, d["technician"]) if d.get("technician") else request.membership
        row = services.record_labor(wo, technician=tech, work_date=d["work_date"], hours=d["hours"],
                                    notes=d.get("notes", ""), actor=request.user, membership=request.membership,
                                    request=request)
        return Response(LaborSerializer(row).data, status=status.HTTP_201_CREATED)

    @extend_schema(methods=["GET"], responses=MaterialSerializer(many=True))
    @extend_schema(methods=["POST"], request=MaterialWriteSerializer, responses={201: MaterialSerializer})
    @action(detail=True, methods=["get", "post"])
    def materials(self, request, pk=None):
        wo = self._obj(request, pk)
        if request.method == "GET":
            return paginate(request, selectors.materials_for(request.organization, wo), MaterialSerializer)
        require_permission(request.membership, "work_order.record", wo.site_id)
        d = _validated(MaterialWriteSerializer, request)
        row = services.record_material(wo, description=d["description"], quantity=d["quantity"],
                                       unit=d.get("unit", "pcs"), part_number=d.get("part_number", ""),
                                       actor=request.user, membership=request.membership, request=request)
        return Response(MaterialSerializer(row).data, status=status.HTTP_201_CREATED)

    @extend_schema(methods=["GET"], responses=WorkOrderEvidenceSerializer(many=True))
    @extend_schema(methods=["POST"], request={"multipart/form-data": WorkOrderEvidenceUploadSerializer},
                   responses={201: WorkOrderEvidenceSerializer})
    @action(detail=True, methods=["get", "post"], parser_classes=[MultiPartParser, FormParser])
    def evidence(self, request, pk=None):
        wo = self._obj(request, pk)
        if request.method == "GET":
            return paginate(request, services.evidence_for(wo).select_related("uploaded_by"), WorkOrderEvidenceSerializer)
        require_permission(request.membership, "work_order.attach", wo.site_id)
        d = _validated(WorkOrderEvidenceUploadSerializer, request)
        att = services.add_evidence(wo, d["file"], description=d.get("description", ""), actor=request.user,
                                    membership=request.membership, request=request)
        return Response(WorkOrderEvidenceSerializer(att).data, status=status.HTTP_201_CREATED)
