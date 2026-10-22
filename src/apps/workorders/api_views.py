from decimal import Decimal

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.assets import selectors as asset_selectors
from apps.core.apiutils import ID_PARAM, paginate, query_param, require_permission
from apps.core.exceptions import Conflict, NotFound, ValidationFailed
from apps.sites.models import Site
from apps.sites.selectors import scoped_get
from apps.tenancy.api import TenantAPIMixin
from apps.tenancy.models import Membership

from . import selectors, services
from .models import ClosureApproval, WorkOrder, WorkOrderLabor, WorkOrderMaterial
from .serializers import PlanSerializer
from .workflow import ACTION_PERMISSIONS, WORK_ORDER_STATUS


class ClosureApprovalSerializer(serializers.ModelSerializer):
    approver_name = serializers.CharField(source="approver.display_name", read_only=True)

    class Meta:
        model = ClosureApproval
        fields = ["id", "approver", "approver_name", "decision", "comment", "created_at"]


class WorkOrderSerializer(serializers.ModelSerializer):
    number = serializers.CharField(read_only=True)
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    asset_name = serializers.CharField(source="asset.name", read_only=True)
    site_code = serializers.CharField(source="site.code", read_only=True)
    assigned_to_name = serializers.SerializerMethodField()
    source_request_number = serializers.CharField(source="source_request.number", read_only=True, default=None)
    available_actions = serializers.SerializerMethodField()
    total_hours = serializers.SerializerMethodField()
    closure_approvals = ClosureApprovalSerializer(many=True, read_only=True)

    class Meta:
        model = WorkOrder
        fields = ["id", "number", "order_number", "title", "description", "work_type", "priority", "status", "asset", "asset_tag",
                  "asset_name", "asset_reference", "component", "component_reference", "site", "site_code",
                  "source_request", "source_request_number", "source_type", "source_reference",
                  "source_id", "planned_start", "planned_end", "due_at", "estimated_hours",
                  "assigned_to", "assigned_to_name", "dispatched_at", "started_at",
                  "completed_at", "closed_at", "closed_by", "version", "hold_reason", "resolution_notes", "closure_approvals",
                  "available_actions", "total_hours", "created_at", "updated_at"]
        read_only_fields = fields

    def get_assigned_to_name(self, obj) -> str | None:
        return obj.assigned_to.user.display_name if obj.assigned_to_id else None

    def get_available_actions(self, obj) -> list[str]:
        return [t.action for t in WORK_ORDER_STATUS.available(obj.status)]

    def get_total_hours(self, obj) -> str:
        return str(selectors.total_hours(obj.organization, obj))


class WorkOrderWriteSerializer(serializers.Serializer):
    site = serializers.UUIDField(required=False)
    asset = serializers.UUIDField()
    component = serializers.UUIDField(required=False, allow_null=True)
    source_type = serializers.ChoiceField(choices=WorkOrder.SourceType.choices, required=False)
    source_reference = serializers.CharField(max_length=150, required=False, allow_blank=True)
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    work_type = serializers.ChoiceField(choices=WorkOrder.WorkType.choices, required=False, allow_null=True)
    priority = serializers.ChoiceField(choices=WorkOrder.Priority.choices, required=False, allow_null=True)
    planned_start = serializers.DateTimeField(required=False, allow_null=True)
    planned_end = serializers.DateTimeField(required=False, allow_null=True)
    due_at = serializers.DateTimeField(required=False, allow_null=True)
    estimated_hours = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)

    def validate(self, attrs):
        start = attrs.get("planned_start")
        end = attrs.get("planned_end")
        due_at = attrs.get("due_at")
        estimated_hours = attrs.get("estimated_hours")
        if start and end and end <= start:
            raise serializers.ValidationError({"planned_end": ["Planned end must be later than planned start."]})
        if due_at and end and due_at <= end:
            raise serializers.ValidationError({"due_at": ["Due date must be later than planned end."]})
        if estimated_hours is not None and estimated_hours <= Decimal("0"):
            raise serializers.ValidationError({"estimated_hours": ["Estimated hours must be greater than zero."]})
        if start and end and estimated_hours is not None:
            available_hours = Decimal(str((end - start).total_seconds())) / Decimal("3600")
            if estimated_hours > available_hours:
                raise serializers.ValidationError({"estimated_hours": ["Estimated hours cannot exceed the planned start-to-end window."]})
        return attrs


class WorkOrderPatchSerializer(serializers.Serializer):
    site = serializers.UUIDField(required=False)
    asset = serializers.UUIDField(required=False)
    component = serializers.UUIDField(required=False, allow_null=True)
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    work_type = serializers.ChoiceField(choices=WorkOrder.WorkType.choices, required=False, allow_null=True)
    priority = serializers.ChoiceField(choices=WorkOrder.Priority.choices, required=False, allow_null=True)
    planned_start = serializers.DateTimeField(required=False, allow_null=True)
    planned_end = serializers.DateTimeField(required=False, allow_null=True)
    due_at = serializers.DateTimeField(required=False, allow_null=True)
    estimated_hours = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)

    def validate(self, attrs):
        extra = set(getattr(self, "initial_data", {})) - set(self.fields)
        if "status" in extra:
            raise serializers.ValidationError({"status": "Status cannot be edited; use POST /work-orders/{id}/transition/."})
        if extra:
            raise serializers.ValidationError({k: "This field cannot be edited." for k in sorted(extra)})
        start = attrs.get("planned_start")
        end = attrs.get("planned_end")
        due_at = attrs.get("due_at")
        estimated_hours = attrs.get("estimated_hours")
        if start and end and end <= start:
            raise serializers.ValidationError({"planned_end": ["Planned end must be later than planned start."]})
        if due_at and end and due_at <= end:
            raise serializers.ValidationError({"due_at": ["Due date must be later than planned end."]})
        if estimated_hours is not None and estimated_hours <= Decimal("0"):
            raise serializers.ValidationError({"estimated_hours": ["Estimated hours must be greater than zero."]})
        if start and end and estimated_hours is not None:
            available_hours = Decimal(str((end - start).total_seconds())) / Decimal("3600")
            if estimated_hours > available_hours:
                raise serializers.ValidationError({"estimated_hours": ["Estimated hours cannot exceed the planned start-to-end window."]})
        return attrs


class WorkOrderTransitionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=sorted(ACTION_PERMISSIONS))
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)
    planned_start = serializers.DateTimeField(required=False, allow_null=True)
    planned_end = serializers.DateTimeField(required=False, allow_null=True)
    estimated_hours = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    priority = serializers.ChoiceField(choices=WorkOrder.Priority.choices, required=False)
    technician = serializers.UUIDField(required=False, help_text="Membership id (assign)")
    assigned_to = serializers.UUIDField(required=False, help_text="Membership id (assign)")
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
    quantity = serializers.DecimalField(max_digits=10, decimal_places=3, coerce_to_string=True)

    class Meta:
        model = WorkOrderMaterial
        fields = ["id", "description", "part_number", "quantity", "unit", "stock_backed", "created_at"]
        read_only_fields = fields


    def get_stock_backed(self, obj) -> bool:
        return obj.part_line_id is not None


class MaterialWriteSerializer(serializers.Serializer):
    description = serializers.CharField(max_length=200, required=False, allow_blank=True)
    item_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    part_number = serializers.CharField(max_length=60, required=False, allow_blank=True)
    quantity = serializers.DecimalField(max_digits=10, decimal_places=3)
    unit = serializers.CharField(max_length=20, required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs.get("description") and not attrs.get("item_name"):
            raise serializers.ValidationError({"description": "Description or item name is required."})
        return attrs


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
        "plan": "work_order.plan",
        "assign": "work_order.assign", "dispatch_order": "work_order.dispatch", "start": "work_order.start",
        "hold": "work_order.hold", "resume": "work_order.start", "complete": "work_order.complete",
        "submit_for_review": "work_order.complete", "review": "work_order.review",
        "return_for_rework": "work_order.review", "close": "work_order.close",
        "notes": "work_order.record",
        "reassign": "work_order.assign", "events": "work_order.view_assigned",
        "closure": "work_order.view_assigned", "labor:get": "work_order.view_assigned",
        "labor:post": "work_order.record", "materials:get": "work_order.view_assigned",
        "materials:post": "work_order.record", "evidence:get": "work_order.view_assigned",
        "evidence:post": "work_order.attach",
        "checklist_reference": "work_order.update",
        "checklist_status": "work_order.view_assigned",
        "validate_checklist": "work_order.view_assigned",
        "provisional_checklist": "work_order.update",
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
        asset_id = d.pop("asset")
        asset = asset_selectors.get_asset(request.membership, request.organization, asset_id)
        component = None
        if "component" in d and d["component"]:
            from apps.assets.models import Asset
            component = Asset.objects.for_organization(request.organization).filter(pk=d.pop("component")).first()
        elif "component" in d:
            d.pop("component")
        site_id = d.pop("site", None) or asset.site_id
        require_permission(request.membership, "work_order.create", site_id)
        wo = services.create_work_order(request.organization, site_id=site_id, asset=asset, component=component,
                                        actor=request.user, membership=request.membership, request=request,
                                        **d)
        return Response(WorkOrderSerializer(self._obj(request, wo.pk)).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=WorkOrderPatchSerializer, responses=WorkOrderSerializer)
    def partial_update(self, request, pk=None):
        wo = self._obj(request, pk, "work_order.update")
        d = dict(_validated(WorkOrderPatchSerializer, request, partial=True))
        if "site" in d and d["site"]:
            d["site"] = Site.objects.for_organization(request.organization).filter(pk=d["site"]).first()
        if "asset" in d and d["asset"]:
            d["asset"] = asset_selectors.get_asset(request.membership, request.organization, d["asset"])
        if "component" in d:
            from apps.assets.models import Asset
            d["component"] = Asset.objects.for_organization(request.organization).filter(pk=d["component"]).first() if d["component"] else None
        services.update_work_order(wo, membership=request.membership, actor=request.user, request=request, **d)
        return Response(WorkOrderSerializer(self._obj(request, pk)).data)

    @extend_schema(request=WorkOrderTransitionSerializer, responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        return self._transition(request, pk, dict(_validated(WorkOrderTransitionSerializer, request)))

    # HPE API group "Work Orders: /assign/, /start/, /hold/, /complete/": the same guarded transition, one URL per action
    def _shortcut(self, request, pk, act):
        body = {k: v for k, v in (request.data.items() if hasattr(request.data, "items") else [])}
        if "assigned_to" in body and "technician" not in body:
            body["technician"] = body["assigned_to"]
        ser = WorkOrderTransitionSerializer(data={**body, "action": act})
        ser.is_valid(raise_exception=True)
        return self._transition(request, pk, dict(ser.validated_data))

    @extend_schema(request=PlanSerializer, responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"])
    def plan(self, request, pk=None):
        wo = self._obj(request, pk, "work_order.plan")
        ser = PlanSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.plan_work_order(
            wo,
            membership=request.membership,
            actor=request.user,
            request=request,
            **ser.validated_data,
        )
        return Response(WorkOrderSerializer(self._obj(request, pk)).data)

    @extend_schema(request=OpenApiTypes.OBJECT, responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        wo = self._obj(request, pk, "work_order.assign")
        body = {k: v for k, v in (request.data.items() if hasattr(request.data, "items") else [])}
        assignee_id = body.get("assigned_to") or body.get("technician")
        if assignee_id:
            services.assign_work_order(
                wo,
                assignee_id=assignee_id,
                membership=request.membership,
                actor=request.user,
                scheduled_start=body.get("scheduled_start"),
                scheduled_end=body.get("scheduled_end"),
                expected_version=body.get("expected_version"),
                request=request,
            )
            return Response(WorkOrderSerializer(self._obj(request, pk)).data)
        return self._shortcut(request, pk, "assign")

    @extend_schema(request=OpenApiTypes.OBJECT, responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"], url_path="dispatch")
    def dispatch_order(self, request, pk=None):
        return self._shortcut(request, pk, "dispatch")

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
    def resume(self, request, pk=None):
        return self._shortcut(request, pk, "resume")

    @extend_schema(request=OpenApiTypes.OBJECT, responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        wo = self._obj(request, pk, "work_order.complete")
        resolution_notes = request.data.get("resolution_notes") or request.data.get("notes")
        if resolution_notes is not None:
            expected_version = request.data.get("expected_version")
            services.complete_work_order(
                wo,
                membership=request.membership,
                actor=request.user,
                resolution_notes=resolution_notes,
                expected_version=expected_version,
                request=request,
            )
            return Response(WorkOrderSerializer(self._obj(request, pk)).data)
        return self._shortcut(request, pk, "complete")

    @extend_schema(request=OpenApiTypes.OBJECT, responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"])
    def notes(self, request, pk=None):
        wo = self._obj(request, pk, "work_order.record")
        text = request.data.get("text") or request.data.get("update") or ""
        services.record_work_update(
            wo,
            update=text,
            membership=request.membership,
            actor=request.user,
            request=request,
        )
        return Response(WorkOrderSerializer(self._obj(request, pk)).data)

    @extend_schema(request=OpenApiTypes.OBJECT, responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"], url_path="submit-for-review")
    def submit_for_review(self, request, pk=None):
        wo = self._obj(request, pk)
        expected_version = request.data.get("expected_version")
        services.submit_for_review(
            wo,
            membership=request.membership,
            actor=request.user,
            expected_version=expected_version,
            request=request,
        )
        return Response(WorkOrderSerializer(self._obj(request, pk)).data)

    @extend_schema(request=OpenApiTypes.OBJECT, responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"], url_path="review")
    def review(self, request, pk=None):
        wo = self._obj(request, pk)
        approved = request.data.get("approved")
        if approved is None:
            raise ValidationFailed("Approved status is required (boolean).", code="approval_required")
        notes = request.data.get("notes") or request.data.get("comment") or ""
        expected_version = request.data.get("expected_version")
        services.review_work_order(
            wo,
            membership=request.membership,
            actor=request.user,
            approved=bool(approved),
            notes=notes,
            expected_version=expected_version,
            request=request,
        )
        return Response(WorkOrderSerializer(self._obj(request, pk)).data)

    @extend_schema(request=OpenApiTypes.OBJECT, responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"], url_path="return-for-rework")
    def return_for_rework(self, request, pk=None):
        wo = self._obj(request, pk, "work_order.review")
        comment = request.data.get("comment") or request.data.get("notes") or ""
        comment = str(comment).strip()
        if not comment:
            raise ValidationFailed("A comment is required to return a work order for rework.", code="comment_required")
        expected_version = request.data.get("expected_version")
        services.return_for_rework(
            wo,
            membership=request.membership,
            actor=request.user,
            comment=comment,
            expected_version=expected_version,
            request=request,
        )
        return Response(WorkOrderSerializer(self._obj(request, pk)).data)

    @extend_schema(request=OpenApiTypes.OBJECT, responses=WorkOrderSerializer)
    @action(detail=True, methods=["post"], url_path="close")
    def close(self, request, pk=None):
        wo = self._obj(request, pk, "work_order.close")
        comment = request.data.get("comment") or request.data.get("notes") or ""
        expected_version = request.data.get("expected_version")
        services.close_work_order(
            wo,
            membership=request.membership,
            actor=request.user,
            comment=comment,
            expected_version=expected_version,
            request=request,
        )
        return Response(WorkOrderSerializer(self._obj(request, pk)).data)

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
        return paginate(request, wo.events.all().order_by("-created_at"), EventSerializer)

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
        desc = d.get("description") or d.get("item_name") or ""
        row = services.record_material(wo, description=desc, quantity=d["quantity"],
                                       unit=d.get("unit", "pcs"), part_number=d.get("part_number", ""),
                                       actor=request.user, membership=request.membership, request=request)
        return Response(MaterialSerializer(row).data, status=status.HTTP_201_CREATED)

    @extend_schema(methods=["GET"], responses=WorkOrderEvidenceSerializer(many=True))
    @extend_schema(methods=["POST"], responses={201: WorkOrderEvidenceSerializer})
    @action(detail=True, methods=["get", "post"], parser_classes=[MultiPartParser, FormParser, JSONParser])
    def evidence(self, request, pk=None):
        wo = self._obj(request, pk)
        if request.method == "GET":
            return paginate(request, services.evidence_for(wo).select_related("uploaded_by"), WorkOrderEvidenceSerializer)
        require_permission(request.membership, "work_order.attach", wo.site_id)
        if "attachment_id" in request.data:
            att = services.add_evidence(wo, attachment_id=request.data["attachment_id"], actor=request.user,
                                        membership=request.membership, request=request)
            return Response({"id": str(att.pk), "work_order": str(wo.pk)}, status=status.HTTP_201_CREATED)
        d = _validated(WorkOrderEvidenceUploadSerializer, request)
        att = services.add_evidence(wo, d["file"], description=d.get("description", ""), actor=request.user,
                                    membership=request.membership, request=request)
        return Response(WorkOrderEvidenceSerializer(att).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={200: OpenApiTypes.OBJECT}, description="Return M08 checklist references and validation state.")
    @action(detail=True, methods=["get"], url_path="checklist-status")
    def checklist_status(self, request, pk=None):
        wo = self._obj(request, pk)
        from .checklist_adapter import get_checklist_summary
        return Response(get_checklist_summary(wo))

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT,
                   description="Register opaque M08 template and instance references.")
    @action(detail=True, methods=["post"], url_path="checklist-reference")
    def checklist_reference(self, request, pk=None):
        wo = self._obj(request, pk, "work_order.update")
        from .checklist_adapter import get_checklist_summary
        template_ref = request.data.get("template_ref", "")
        instance_ref = request.data.get("instance_ref", "")
        services.register_checklist_reference(wo, membership=request.membership, actor=request.user,
                                              template_ref=template_ref, instance_ref=instance_ref,
                                              request=request)
        return Response(get_checklist_summary(wo))

    @extend_schema(responses={200: OpenApiTypes.OBJECT, 409: OpenApiTypes.OBJECT},
                   description="Evaluate checklist validation status.")
    @action(detail=True, methods=["post"], url_path="validate-checklist")
    def validate_checklist(self, request, pk=None):
        wo = self._obj(request, pk)
        from .checklist_adapter import get_checklist_status, get_checklist_summary
        result = get_checklist_status(wo)
        return Response(
            get_checklist_summary(wo),
            status=status.HTTP_200_OK if result.is_valid else status.HTTP_409_CONFLICT,
        )

    @extend_schema(request=OpenApiTypes.OBJECT, responses=WorkOrderSerializer,
                   description="Store provisional checklist notes.")
    @action(detail=True, methods=["post"], url_path="provisional-checklist")
    def provisional_checklist(self, request, pk=None):
        wo = self._obj(request, pk, "work_order.update")
        items = request.data.get("items", [])
        services.set_checklist(wo, items=items, membership=request.membership, actor=request.user, request=request)
        return Response(WorkOrderSerializer(self._obj(request, pk)).data)

