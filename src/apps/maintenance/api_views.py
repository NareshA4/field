"""M04 REST API (``/api/v1/maintenance-plans|maintenance-schedules|maintenance-cycles``).

``TenantAPIMixin`` + ``permission_map`` (unmapped = denied) -> object lookup restricted to organization AND site
scope (404) -> permission for the plan's site (403) -> service."""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.assets import selectors as asset_selectors
from apps.assets.models import AssetMeter
from apps.core.apiutils import ID_PARAM, paginate, query_param, require_permission
from apps.core.exceptions import NotFound
from apps.sites.selectors import scoped_get
from apps.tenancy.api import TenantAPIMixin
from apps.workorders.models import WorkOrder

from . import selectors, services
from .models import MaintenanceCycle, MaintenancePlan, MaintenanceSchedule

# --- serializers -------------------------------------------------------------------------------------------------


class ScheduleSerializer(serializers.ModelSerializer):
    plan_name = serializers.CharField(source="plan.name", read_only=True)
    asset_tag = serializers.CharField(source="plan.asset.asset_tag", read_only=True)
    site = serializers.UUIDField(source="plan.site_id", read_only=True)
    description_text = serializers.SerializerMethodField()
    state = serializers.SerializerMethodField(help_text="DISABLED | DUE | SCHEDULED")
    meter_name = serializers.CharField(source="meter.name", read_only=True, default=None)

    class Meta:
        model = MaintenanceSchedule
        fields = ["id", "plan", "plan_name", "asset_tag", "site", "trigger_type", "frequency", "interval_count",
                  "start_date", "meter", "meter_name", "interval_value", "start_value", "lead_days",
                  "window_start_time", "window_hours", "reminder_days", "is_active", "next_sequence",
                  "next_due_date", "next_due_value", "state", "description_text", "last_run_at", "last_error",
                  "created_at", "updated_at"]
        read_only_fields = fields

    def get_description_text(self, obj) -> str:
        return obj.describe()

    def get_state(self, obj) -> str:
        return selectors.schedule_state(obj)


class PlanSerializer(serializers.ModelSerializer):
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    asset_name = serializers.CharField(source="asset.name", read_only=True)
    site_code = serializers.CharField(source="site.code", read_only=True)

    class Meta:
        model = MaintenancePlan
        fields = ["id", "name", "description", "asset", "asset_tag", "asset_name", "site", "site_code", "priority",
                  "estimated_hours", "checklist_key", "is_active", "created_at", "updated_at"]
        read_only_fields = fields


class PlanWriteSerializer(serializers.Serializer):
    asset = serializers.UUIDField()
    name = serializers.CharField(max_length=150)
    description = serializers.CharField(required=False, allow_blank=True)
    priority = serializers.ChoiceField(choices=WorkOrder.Priority.choices, required=False)
    estimated_hours = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    checklist_key = serializers.CharField(max_length=60, required=False, allow_blank=True)


class PlanPatchSerializer(PlanWriteSerializer):
    asset = None
    name = serializers.CharField(max_length=150, required=False)


class ScheduleWriteSerializer(serializers.Serializer):
    trigger_type = serializers.ChoiceField(choices=MaintenanceSchedule.Trigger.choices)
    frequency = serializers.ChoiceField(choices=MaintenanceSchedule.Frequency.choices, required=False)
    interval_count = serializers.IntegerField(required=False, min_value=1)
    start_date = serializers.DateField(required=False)
    meter = serializers.UUIDField(required=False)
    interval_value = serializers.DecimalField(max_digits=16, decimal_places=3, required=False)
    start_value = serializers.DecimalField(max_digits=16, decimal_places=3, required=False)
    lead_days = serializers.IntegerField(required=False, min_value=0, max_value=60)
    window_start_time = serializers.TimeField(required=False)
    window_hours = serializers.IntegerField(required=False, min_value=1, max_value=72)
    reminder_days = serializers.IntegerField(required=False, min_value=0, max_value=60)


class SchedulePatchSerializer(ScheduleWriteSerializer):
    trigger_type = serializers.ChoiceField(choices=MaintenanceSchedule.Trigger.choices, required=False)

    def validate(self, attrs):
        if "trigger_type" in attrs:
            raise serializers.ValidationError({"trigger_type": "The trigger type cannot be changed."})
        return attrs


class CycleSerializer(serializers.ModelSerializer):
    plan = serializers.UUIDField(source="schedule.plan_id", read_only=True)
    plan_name = serializers.CharField(source="schedule.plan.name", read_only=True)
    asset_tag = serializers.CharField(source="schedule.plan.asset.asset_tag", read_only=True)
    work_order_number = serializers.CharField(source="work_order.number", read_only=True, default=None)
    work_order_status = serializers.CharField(source="work_order.status", read_only=True, default=None)
    state = serializers.SerializerMethodField(help_text="GENERATED | ASSIGNED | COMPLETED | VERIFIED | CANCELLED")

    class Meta:
        model = MaintenanceCycle
        fields = ["id", "schedule", "plan", "plan_name", "asset_tag", "sequence", "due_date", "due_value", "skipped",
                  "work_order", "work_order_number", "work_order_status", "state", "trigger", "created_at"]
        read_only_fields = fields

    def get_state(self, obj) -> str:
        return selectors.cycle_state(obj)


def _validated(serializer_cls, request, partial=False):
    ser = serializer_cls(data=request.data, partial=partial)
    ser.is_valid(raise_exception=True)
    return dict(ser.validated_data)


def _resolve_meter(request, plan, value):
    try:
        meter = AssetMeter.objects.for_organization(request.organization).get(pk=value)
    except (AssetMeter.DoesNotExist, ValueError) as exc:
        raise NotFound("Meter not found.") from exc
    # the meter's asset must be visible to the caller (site scope), like any asset read
    asset_selectors.get_asset(request.membership, request.organization, meter.asset_id)
    return meter


# --- viewsets ------------------------------------------------------------------------------------------------------


@extend_schema(parameters=[ID_PARAM])
class MaintenancePlanViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {
        "list": "maintenance.view", "retrieve": "maintenance.view", "create": "maintenance.create",
        "partial_update": "maintenance.update", "enable": "maintenance.update", "disable": "maintenance.update",
        "schedules:get": "maintenance.view", "schedules:post": "maintenance.create",
    }

    def _plan(self, request, pk, code=None) -> MaintenancePlan:
        plan = selectors.get_plan(request.membership, request.organization, pk)
        if code:
            require_permission(request.membership, code, plan.site_id)
        return plan

    @extend_schema(parameters=[query_param("q", "name / asset"), query_param("site", "site id", OpenApiTypes.UUID),
                               query_param("asset", "asset id", OpenApiTypes.UUID), query_param("active", "1 / 0")],
                   responses=PlanSerializer(many=True))
    def list(self, request):
        qs = selectors.filter_plans(selectors.plans_for(request.membership, request.organization),
                                    request.query_params)
        return paginate(request, qs, PlanSerializer)

    @extend_schema(responses=PlanSerializer)
    def retrieve(self, request, pk=None):
        return Response(PlanSerializer(self._plan(request, pk)).data)

    @extend_schema(request=PlanWriteSerializer, responses={201: PlanSerializer})
    def create(self, request):
        d = _validated(PlanWriteSerializer, request)
        asset = asset_selectors.get_asset(request.membership, request.organization, d.pop("asset"))
        require_permission(request.membership, "maintenance.create", asset.site_id)
        plan = services.create_plan(request.organization, asset=asset, actor=request.user, request=request, **d)
        return Response(PlanSerializer(plan).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=PlanPatchSerializer, responses=PlanSerializer)
    def partial_update(self, request, pk=None):
        plan = self._plan(request, pk, "maintenance.update")
        d = _validated(PlanPatchSerializer, request, partial=True)
        services.update_plan(plan, actor=request.user, request=request, **d)
        return Response(PlanSerializer(self._plan(request, pk)).data)

    @extend_schema(request=None, responses=PlanSerializer)
    @action(detail=True, methods=["post"])
    def enable(self, request, pk=None):
        plan = services.set_plan_active(self._plan(request, pk, "maintenance.update"), True, actor=request.user,
                                        request=request)
        return Response(PlanSerializer(plan).data)

    @extend_schema(request=None, responses=PlanSerializer)
    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        plan = services.set_plan_active(self._plan(request, pk, "maintenance.update"), False, actor=request.user,
                                        request=request)
        return Response(PlanSerializer(plan).data)

    @extend_schema(methods=["GET"], responses=ScheduleSerializer(many=True))
    @extend_schema(methods=["POST"], request=ScheduleWriteSerializer, responses={201: ScheduleSerializer})
    @action(detail=True, methods=["get", "post"])
    def schedules(self, request, pk=None):
        plan = self._plan(request, pk)
        if request.method == "GET":
            qs = selectors.schedules_for(request.membership, request.organization).filter(plan=plan)
            return paginate(request, qs, ScheduleSerializer)
        require_permission(request.membership, "maintenance.create", plan.site_id)
        d = _validated(ScheduleWriteSerializer, request)
        if "meter" in d:
            d["meter"] = _resolve_meter(request, plan, d["meter"])
        sch = services.create_schedule(plan, actor=request.user, request=request, **d)
        return Response(ScheduleSerializer(sch).data, status=status.HTTP_201_CREATED)


@extend_schema(parameters=[ID_PARAM])
class MaintenanceScheduleViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {
        "list": "maintenance.view", "retrieve": "maintenance.view", "partial_update": "maintenance.update",
        "enable": "maintenance.update", "disable": "maintenance.update", "generate": "maintenance.generate",
        "cycles": "maintenance.view",
    }

    def _sch(self, request, pk, code=None) -> MaintenanceSchedule:
        sch = selectors.get_schedule(request.membership, request.organization, pk)
        if code:
            require_permission(request.membership, code, sch.plan.site_id)
        return sch

    @extend_schema(parameters=[query_param("plan", "plan id", OpenApiTypes.UUID),
                               query_param("state", "DISABLED | DUE | SCHEDULED")],
                   responses=ScheduleSerializer(many=True))
    def list(self, request):
        qs = selectors.schedules_for(request.membership, request.organization)
        plan = (request.query_params.get("plan") or "").strip()
        if plan:
            parsed = selectors._uuid_or_none(plan)
            qs = qs.filter(plan_id=parsed) if parsed else qs.none()
        state = (request.query_params.get("state") or "").strip()
        if state:
            ids = [s.pk for s in qs if selectors.schedule_state(s) == state]
            qs = qs.filter(pk__in=ids)
        return paginate(request, qs.order_by("next_due_date", "created_at"), ScheduleSerializer)

    @extend_schema(responses=ScheduleSerializer)
    def retrieve(self, request, pk=None):
        return Response(ScheduleSerializer(self._sch(request, pk)).data)

    @extend_schema(request=SchedulePatchSerializer, responses=ScheduleSerializer)
    def partial_update(self, request, pk=None):
        sch = self._sch(request, pk, "maintenance.update")
        d = _validated(SchedulePatchSerializer, request, partial=True)
        if "meter" in d:
            d["meter"] = _resolve_meter(request, sch.plan, d["meter"])
        services.update_schedule(sch, actor=request.user, request=request, **d)
        return Response(ScheduleSerializer(self._sch(request, pk)).data)

    @extend_schema(request=None, responses=ScheduleSerializer)
    @action(detail=True, methods=["post"])
    def enable(self, request, pk=None):
        sch = services.set_schedule_active(self._sch(request, pk, "maintenance.update"), True, actor=request.user,
                                           request=request)
        return Response(ScheduleSerializer(sch).data)

    @extend_schema(request=None, responses=ScheduleSerializer)
    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        sch = services.set_schedule_active(self._sch(request, pk, "maintenance.update"), False, actor=request.user,
                                           request=request)
        return Response(ScheduleSerializer(sch).data)

    @extend_schema(request=None, responses={201: CycleSerializer, 200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"])
    def generate(self, request, pk=None):
        """Generates the next occurrence now (even ahead of its date). Exactly once per occurrence."""
        sch = self._sch(request, pk, "maintenance.generate")
        cycle = services.generate_cycle(sch, actor=request.user, manual=True, request=request)
        if cycle is None:
            return Response({"generated": False}, status=status.HTTP_200_OK)
        cycle = scoped_get(selectors.cycles_for(request.membership, request.organization), cycle.pk, "Cycle")
        return Response(CycleSerializer(cycle).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses=CycleSerializer(many=True))
    @action(detail=True, methods=["get"])
    def cycles(self, request, pk=None):
        sch = self._sch(request, pk)
        qs = selectors.cycles_for(request.membership, request.organization).filter(schedule=sch)
        return paginate(request, qs.order_by("-created_at"), CycleSerializer)


@extend_schema(parameters=[ID_PARAM])
class MaintenanceCycleViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "maintenance.view", "retrieve": "maintenance.view"}

    @extend_schema(parameters=[query_param("plan", "plan id", OpenApiTypes.UUID),
                               query_param("schedule", "schedule id", OpenApiTypes.UUID),
                               query_param("asset", "asset id", OpenApiTypes.UUID)],
                   responses=CycleSerializer(many=True))
    def list(self, request):
        qs = selectors.filter_cycles(selectors.cycles_for(request.membership, request.organization),
                                     request.query_params)
        return paginate(request, qs, CycleSerializer)

    @extend_schema(responses=CycleSerializer)
    def retrieve(self, request, pk=None):
        qs = selectors.cycles_for(request.membership, request.organization)
        return Response(CycleSerializer(scoped_get(qs, pk, "Cycle")).data)
