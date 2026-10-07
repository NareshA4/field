from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from apps.assets import selectors as asset_selectors
from apps.core.apiutils import ID_PARAM, paginate, query_param, require_permission
from apps.sites.selectors import scoped_get
from apps.tenancy.api import TenantAPIMixin
from apps.workorders.models import WorkOrder

from . import selectors, services
from .models import Downtime, ServiceRequest
from .workflow import ACTION_PERMISSIONS, REQUEST_STATUS, SYSTEM_ACTIONS


class DowntimeSerializer(serializers.ModelSerializer):
    duration_seconds = serializers.SerializerMethodField()

    class Meta:
        model = Downtime
        fields = ["id", "started_at", "ended_at", "end_source", "duration_seconds"]
        read_only_fields = fields

    def get_duration_seconds(self, obj) -> int | None:
        return int(obj.duration.total_seconds()) if obj.duration is not None else None


class ServiceRequestSerializer(serializers.ModelSerializer):
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    asset_name = serializers.CharField(source="asset.name", read_only=True)
    site_code = serializers.CharField(source="site.code", read_only=True)
    reported_by_name = serializers.CharField(source="reported_by.user.display_name", read_only=True)
    available_actions = serializers.SerializerMethodField()
    work_orders = serializers.SerializerMethodField()
    downtime = serializers.SerializerMethodField()

    class Meta:
        model = ServiceRequest
        fields = ["id", "number", "kind", "title", "description", "severity", "service_impact", "impact_notes",
                  "asset", "asset_tag", "asset_name", "site", "site_code", "status", "reported_by",
                  "reported_by_name", "occurred_at", "triaged_at", "decided_at", "decision_reason", "resolved_at",
                  "confirmed_at", "closed_at", "available_actions", "work_orders", "downtime", "created_at",
                  "updated_at"]
        read_only_fields = fields

    def get_available_actions(self, obj) -> list[str]:
        return [t.action for t in REQUEST_STATUS.available(obj.status) if t.action not in SYSTEM_ACTIONS]

    def get_work_orders(self, obj) -> list[dict]:
        return [{"id": str(w.pk), "number": w.number, "status": w.status} for w in obj.work_orders.all()]

    def get_downtime(self, obj) -> dict | None:
        dt = Downtime.objects.for_organization(obj.organization).filter(request=obj).first()
        return DowntimeSerializer(dt).data if dt else None


class ServiceRequestWriteSerializer(serializers.Serializer):
    asset = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=ServiceRequest.Kind.choices, required=False)
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    severity = serializers.ChoiceField(choices=ServiceRequest.Severity.choices, required=False)
    service_impact = serializers.ChoiceField(choices=ServiceRequest.Impact.choices, required=False)
    impact_notes = serializers.CharField(max_length=500, required=False, allow_blank=True)
    occurred_at = serializers.DateTimeField(required=False)
    downtime_started_at = serializers.DateTimeField(required=False, allow_null=True)


class ServiceRequestPatchSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    severity = serializers.ChoiceField(choices=ServiceRequest.Severity.choices, required=False)
    service_impact = serializers.ChoiceField(choices=ServiceRequest.Impact.choices, required=False)
    impact_notes = serializers.CharField(max_length=500, required=False, allow_blank=True)
    occurred_at = serializers.DateTimeField(required=False)

    def validate(self, attrs):
        extra = set(getattr(self, "initial_data", {})) - set(self.fields)
        if "status" in extra:
            raise serializers.ValidationError({"status": "Status cannot be edited; use POST /service-requests/{id}/transition/."})
        if extra:
            raise serializers.ValidationError({k: "This field cannot be edited." for k in sorted(extra)})
        return attrs


class RequestTransitionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=sorted(ACTION_PERMISSIONS))
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class CreateWorkOrderSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    priority = serializers.ChoiceField(choices=WorkOrder.Priority.choices, required=False)
    planned_start = serializers.DateTimeField(required=False, allow_null=True)
    planned_end = serializers.DateTimeField(required=False, allow_null=True)
    estimated_hours = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)


class DowntimeWriteSerializer(serializers.Serializer):
    started_at = serializers.DateTimeField()
    ended_at = serializers.DateTimeField(required=False, allow_null=True)


class HistorySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    from_status = serializers.CharField()
    to_status = serializers.CharField()
    action = serializers.CharField()
    reason = serializers.CharField()
    source = serializers.CharField()
    changed_by = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    def get_changed_by(self, obj) -> str | None:
        return obj.changed_by.email if obj.changed_by_id else None


class RequestEvidenceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    original_name = serializers.CharField()
    mime_type = serializers.CharField()
    size = serializers.IntegerField()
    description = serializers.CharField()
    uploaded_by = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    def get_uploaded_by(self, obj) -> str:
        return obj.uploaded_by.email


class RequestEvidenceUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    description = serializers.CharField(max_length=200, required=False, allow_blank=True)


def _validated(serializer_cls, request, partial=False):
    ser = serializer_cls(data=request.data, partial=partial)
    ser.is_valid(raise_exception=True)
    return ser.validated_data


@extend_schema(parameters=[ID_PARAM])
class ServiceRequestViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {
        "list": "incident.view", "retrieve": "incident.view", "create": "incident.create",
        "partial_update": "incident.update", "transition": "incident.view", "create_work_order": "work_order.create",
        "history": "incident.view", "downtime:get": "incident.view", "downtime:put": "incident.downtime.manage",
        "evidence:get": "incident.view", "evidence:post": "incident.attach",
    }

    def _qs(self, request):
        return selectors.requests_for(request.membership, request.organization).prefetch_related("work_orders")

    def _obj(self, request, pk, code=None) -> ServiceRequest:
        sr = scoped_get(self._qs(request), pk, "Request")  # 404 outside tenant / site scope
        if code:
            require_permission(request.membership, code, sr.site_id)
        return sr

    @extend_schema(
        parameters=[query_param("site", "site id", OpenApiTypes.UUID), query_param("asset", "asset id",
                    OpenApiTypes.UUID), query_param("status", "request status"), query_param("severity", "severity"),
                    query_param("kind", "INCIDENT | SERVICE_REQUEST"), query_param("q", "number / title / asset"),
                    query_param("ordering", "-created_at, number, severity, status")],
        responses=ServiceRequestSerializer(many=True))
    def list(self, request):
        return paginate(request, selectors.filter_requests(self._qs(request), request.query_params),
                        ServiceRequestSerializer)

    @extend_schema(responses=ServiceRequestSerializer)
    def retrieve(self, request, pk=None):
        return Response(ServiceRequestSerializer(self._obj(request, pk)).data)

    @extend_schema(request=ServiceRequestWriteSerializer, responses={201: ServiceRequestSerializer})
    def create(self, request):
        d = dict(_validated(ServiceRequestWriteSerializer, request))
        asset = asset_selectors.get_asset(request.membership, request.organization, d.pop("asset"))
        require_permission(request.membership, "incident.create", asset.site_id)
        sr = services.create_request(request.organization, asset=asset, reporter=request.membership,
                                     actor=request.user, request=request, **d)
        return Response(ServiceRequestSerializer(self._obj(request, sr.pk)).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ServiceRequestPatchSerializer, responses=ServiceRequestSerializer)
    def partial_update(self, request, pk=None):
        sr = self._obj(request, pk, "incident.update")
        d = _validated(ServiceRequestPatchSerializer, request, partial=True)
        services.update_request(sr, actor=request.user, request=request, **d)
        return Response(ServiceRequestSerializer(self._obj(request, pk)).data)

    @extend_schema(request=RequestTransitionSerializer, responses=ServiceRequestSerializer)
    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        """triage / approve / reject / confirm / reopen / close. Work-order driven steps cannot be called here."""
        sr = self._obj(request, pk)
        d = _validated(RequestTransitionSerializer, request)
        require_permission(request.membership, ACTION_PERMISSIONS[d["action"]], sr.site_id)
        services.transition(sr, action=d["action"], reason=d.get("reason", ""), actor=request.user, request=request)
        return Response(ServiceRequestSerializer(self._obj(request, pk)).data)

    @extend_schema(request=CreateWorkOrderSerializer, responses={201: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="create-work-order")
    def create_work_order(self, request, pk=None):
        """Approved request -> a real M06 work order (DRAFT); the request moves to WORK ORDER CREATED."""
        sr = self._obj(request, pk, "work_order.create")
        d = _validated(CreateWorkOrderSerializer, request)
        wo = services.create_work_order_for_request(sr, actor=request.user, membership=request.membership,
                                                    request=request, **d)
        return Response({"id": str(wo.pk), "number": wo.number, "status": wo.status,
                         "request": ServiceRequestSerializer(self._obj(request, pk)).data},
                        status=status.HTTP_201_CREATED)

    @extend_schema(responses=HistorySerializer(many=True))
    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        sr = self._obj(request, pk)
        return paginate(request, selectors.history_for(request.organization, sr), HistorySerializer)

    @extend_schema(methods=["GET"], responses=DowntimeSerializer)
    @extend_schema(methods=["PUT"], request=DowntimeWriteSerializer, responses=DowntimeSerializer)
    @action(detail=True, methods=["get", "put"])
    def downtime(self, request, pk=None):
        """Downtime record of the request (start required; end blank while the asset is still down)."""
        sr = self._obj(request, pk)
        if request.method == "GET":
            dt = selectors.downtime_for(request.organization, sr)
            return Response(DowntimeSerializer(dt).data if dt else None)
        require_permission(request.membership, "incident.downtime.manage", sr.site_id)
        d = _validated(DowntimeWriteSerializer, request)
        dt = services.set_downtime(sr, started_at=d["started_at"], ended_at=d.get("ended_at"), actor=request.user,
                                   request=request)
        return Response(DowntimeSerializer(dt).data)

    @extend_schema(methods=["GET"], responses=RequestEvidenceSerializer(many=True))
    @extend_schema(methods=["POST"], request={"multipart/form-data": RequestEvidenceUploadSerializer},
                   responses={201: RequestEvidenceSerializer})
    @action(detail=True, methods=["get", "post"], parser_classes=[MultiPartParser, FormParser])
    def evidence(self, request, pk=None):
        sr = self._obj(request, pk)
        if request.method == "GET":
            return paginate(request, selectors.evidence_for(request.organization, sr), RequestEvidenceSerializer)
        require_permission(request.membership, "incident.attach", sr.site_id)
        d = _validated(RequestEvidenceUploadSerializer, request)
        att = services.add_evidence(sr, d["file"], description=d.get("description", ""), actor=request.user,
                                    request=request)
        return Response(RequestEvidenceSerializer(att).data, status=status.HTTP_201_CREATED)
