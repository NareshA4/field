"""M09 REST API (``/api/v1/parts|warehouses|stock-balances|stock-movements|part-reservations|work-order-parts``).

Every view: ``TenantAPIMixin`` (identity -> ACTIVE membership -> organization) + ``permission_map`` (unmapped =
denied) -> object lookup restricted to the caller's organization AND site scope (404) -> permission for the object's
site (403) -> service (validation, locks, ledger, audit)."""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.apiutils import ID_PARAM, paginate, query_param, require_permission
from apps.sites import selectors as site_selectors
from apps.tenancy.api import TenantAPIMixin
from apps.workorders import selectors as wo_selectors

from . import selectors, services
from .models import Part, PartReservation, StockBalance, StockMovement, Warehouse, WorkOrderPart

# --- serializers -------------------------------------------------------------------------------------------------


class PartSerializer(serializers.ModelSerializer):
    class Meta:
        model = Part
        fields = ["id", "part_number", "name", "description", "unit", "min_stock", "max_stock", "reorder_quantity",
                  "is_active", "created_at", "updated_at"]
        read_only_fields = fields


class PartWriteSerializer(serializers.Serializer):
    part_number = serializers.CharField(max_length=60)
    name = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    unit = serializers.CharField(max_length=20, required=False, allow_blank=True)
    min_stock = serializers.DecimalField(max_digits=14, decimal_places=3, required=False, allow_null=True)
    max_stock = serializers.DecimalField(max_digits=14, decimal_places=3, required=False, allow_null=True)
    reorder_quantity = serializers.DecimalField(max_digits=14, decimal_places=3, required=False, allow_null=True)


class PartPatchSerializer(PartWriteSerializer):
    part_number = serializers.CharField(max_length=60, required=False)
    name = serializers.CharField(max_length=200, required=False)


class WarehouseSerializer(serializers.ModelSerializer):
    site_code = serializers.CharField(source="site.code", read_only=True)

    class Meta:
        model = Warehouse
        fields = ["id", "site", "site_code", "code", "name", "description", "is_active", "created_at", "updated_at"]
        read_only_fields = fields


class WarehouseWriteSerializer(serializers.Serializer):
    site = serializers.UUIDField()
    code = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=120)
    description = serializers.CharField(max_length=300, required=False, allow_blank=True)


class WarehousePatchSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=20, required=False)
    name = serializers.CharField(max_length=120, required=False)
    description = serializers.CharField(max_length=300, required=False, allow_blank=True)


class BalanceSerializer(serializers.ModelSerializer):
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    site = serializers.UUIDField(source="warehouse.site_id", read_only=True)
    part_number = serializers.CharField(source="part.part_number", read_only=True)
    part_name = serializers.CharField(source="part.name", read_only=True)
    unit = serializers.CharField(source="part.unit", read_only=True)
    available = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    effective_min = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True, allow_null=True)
    effective_max = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True, allow_null=True)
    effective_reorder_quantity = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True,
                                                          allow_null=True)
    is_low = serializers.BooleanField(read_only=True)

    class Meta:
        model = StockBalance
        fields = ["id", "warehouse", "warehouse_code", "site", "part", "part_number", "part_name", "unit", "on_hand",
                  "reserved", "available", "min_level", "max_level", "reorder_quantity", "effective_min",
                  "effective_max", "effective_reorder_quantity", "is_low", "updated_at"]
        read_only_fields = fields


class StockInSerializer(serializers.Serializer):
    warehouse = serializers.UUIDField()
    part = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3)
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True)
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True)


class AdjustSerializer(serializers.Serializer):
    warehouse = serializers.UUIDField()
    part = serializers.UUIDField()
    delta = serializers.DecimalField(max_digits=14, decimal_places=3, help_text="Signed, non-zero")
    reason = serializers.CharField(max_length=300)


class LevelsSerializer(serializers.Serializer):
    warehouse = serializers.UUIDField()
    part = serializers.UUIDField()
    min_level = serializers.DecimalField(max_digits=14, decimal_places=3, required=False, allow_null=True)
    max_level = serializers.DecimalField(max_digits=14, decimal_places=3, required=False, allow_null=True)
    reorder_quantity = serializers.DecimalField(max_digits=14, decimal_places=3, required=False, allow_null=True)


class TransferSerializer(serializers.Serializer):
    source = serializers.UUIDField(help_text="Source warehouse id")
    target = serializers.UUIDField(help_text="Destination warehouse id")
    part = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3)
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True)


class MovementSerializer(serializers.ModelSerializer):
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    part_number = serializers.CharField(source="part.part_number", read_only=True)
    work_order_number = serializers.CharField(source="work_order.number", read_only=True, default=None)
    actor_email = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = ["id", "balance", "warehouse", "warehouse_code", "part", "part_number", "movement_type", "quantity",
                  "on_hand_delta", "reserved_delta", "on_hand_after", "reserved_after", "work_order",
                  "work_order_number", "part_line", "transfer_ref", "reference", "reason", "actor_email",
                  "created_at"]
        read_only_fields = fields

    def get_actor_email(self, obj) -> str | None:
        return obj.actor.email if obj.actor_id else None


class ReservationSerializer(serializers.ModelSerializer):
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    part_number = serializers.CharField(source="part.part_number", read_only=True)
    work_order_number = serializers.CharField(source="work_order.number", read_only=True)

    class Meta:
        model = PartReservation
        fields = ["id", "part_line", "work_order", "work_order_number", "warehouse", "warehouse_code", "part",
                  "part_number", "quantity", "status", "created_at", "updated_at"]
        read_only_fields = fields


class PartLineSerializer(serializers.ModelSerializer):
    part_number = serializers.CharField(source="part.part_number", read_only=True)
    part_name = serializers.CharField(source="part.name", read_only=True)
    unit = serializers.CharField(source="part.unit", read_only=True)
    work_order_number = serializers.CharField(source="work_order.number", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True, default=None)
    outstanding = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    reserved = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderPart
        fields = ["id", "work_order", "work_order_number", "part", "part_number", "part_name", "unit", "warehouse",
                  "warehouse_code", "status", "quantity_requested", "reserved", "quantity_issued",
                  "quantity_consumed", "quantity_returned", "outstanding", "notes", "created_at", "updated_at"]
        read_only_fields = fields

    def get_reserved(self, obj) -> str:
        res = getattr(obj, "reservation", None)  # reverse one-to-one: absent -> None
        return str(res.quantity if res else 0)


class PartLineCreateSerializer(serializers.Serializer):
    work_order = serializers.UUIDField()
    part = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3)
    notes = serializers.CharField(max_length=300, required=False, allow_blank=True)


class ReserveSerializer(serializers.Serializer):
    warehouse = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3, required=False,
                                        help_text="Default: everything still uncovered")


class ReleaseSerializer(serializers.Serializer):
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3, required=False,
                                        help_text="Default: the whole reservation")


class IssueSerializer(serializers.Serializer):
    warehouse = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3)


class QuantitySerializer(serializers.Serializer):
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3)
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True)


def _validated(serializer_cls, request, partial=False):
    ser = serializer_cls(data=request.data, partial=partial)
    ser.is_valid(raise_exception=True)
    return ser.validated_data


# --- viewsets ------------------------------------------------------------------------------------------------------


@extend_schema(parameters=[ID_PARAM])
class PartViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "inventory.part.view", "retrieve": "inventory.part.view",
                      "create": "inventory.part.manage", "partial_update": "inventory.part.manage",
                      "activate": "inventory.part.manage", "deactivate": "inventory.part.manage",
                      "stock": "inventory.view"}

    @extend_schema(parameters=[query_param("q", "part number / name"), query_param("active", "1 / 0")],
                   responses=PartSerializer(many=True))
    def list(self, request):
        qs = selectors.filter_parts(selectors.parts_for(request.organization), request.query_params)
        return paginate(request, qs, PartSerializer)

    @extend_schema(responses=PartSerializer)
    def retrieve(self, request, pk=None):
        return Response(PartSerializer(selectors.get_part(request.organization, pk)).data)

    @extend_schema(request=PartWriteSerializer, responses={201: PartSerializer})
    def create(self, request):
        d = _validated(PartWriteSerializer, request)
        part = services.create_part(request.organization, actor=request.user, request=request, **d)
        return Response(PartSerializer(part).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=PartPatchSerializer, responses=PartSerializer)
    def partial_update(self, request, pk=None):
        part = selectors.get_part(request.organization, pk)
        d = _validated(PartPatchSerializer, request, partial=True)
        services.update_part(part, actor=request.user, request=request, **d)
        return Response(PartSerializer(selectors.get_part(request.organization, pk)).data)

    @extend_schema(request=None, responses=PartSerializer)
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        part = services.set_part_active(selectors.get_part(request.organization, pk), True, actor=request.user,
                                        request=request)
        return Response(PartSerializer(part).data)

    @extend_schema(request=None, responses=PartSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        part = services.set_part_active(selectors.get_part(request.organization, pk), False, actor=request.user,
                                        request=request)
        return Response(PartSerializer(part).data)

    @extend_schema(responses=BalanceSerializer(many=True))
    @action(detail=True, methods=["get"])
    def stock(self, request, pk=None):
        """Balances of this part over the warehouses the caller may see."""
        part = selectors.get_part(request.organization, pk)
        qs = selectors.balances_for(request.membership, request.organization).filter(part=part)
        return paginate(request, qs, BalanceSerializer)


@extend_schema(parameters=[ID_PARAM])
class WarehouseViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "inventory.view", "retrieve": "inventory.view",
                      "create": "inventory.warehouse.manage", "partial_update": "inventory.warehouse.manage",
                      "activate": "inventory.warehouse.manage", "deactivate": "inventory.warehouse.manage"}

    def _obj(self, request, pk, code=None) -> Warehouse:
        wh = selectors.get_warehouse(request.membership, request.organization, pk)
        if code:
            require_permission(request.membership, code, wh.site_id)
        return wh

    @extend_schema(parameters=[query_param("q", "code / name"), query_param("site", "site id", OpenApiTypes.UUID),
                               query_param("active", "1 / 0")], responses=WarehouseSerializer(many=True))
    def list(self, request):
        qs = selectors.filter_warehouses(selectors.warehouses_for(request.membership, request.organization),
                                         request.query_params)
        return paginate(request, qs, WarehouseSerializer)

    @extend_schema(responses=WarehouseSerializer)
    def retrieve(self, request, pk=None):
        return Response(WarehouseSerializer(self._obj(request, pk)).data)

    @extend_schema(request=WarehouseWriteSerializer, responses={201: WarehouseSerializer})
    def create(self, request):
        d = dict(_validated(WarehouseWriteSerializer, request))
        site = site_selectors.get_site_for(request.membership, request.organization, d.pop("site"),
                                           "inventory.warehouse.manage")
        wh = services.create_warehouse(request.organization, site=site, actor=request.user, request=request, **d)
        return Response(WarehouseSerializer(wh).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=WarehousePatchSerializer, responses=WarehouseSerializer)
    def partial_update(self, request, pk=None):
        wh = self._obj(request, pk, "inventory.warehouse.manage")
        d = _validated(WarehousePatchSerializer, request, partial=True)
        services.update_warehouse(wh, actor=request.user, request=request, **d)
        return Response(WarehouseSerializer(self._obj(request, pk)).data)

    @extend_schema(request=None, responses=WarehouseSerializer)
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        wh = services.set_warehouse_active(self._obj(request, pk, "inventory.warehouse.manage"), True,
                                           actor=request.user, request=request)
        return Response(WarehouseSerializer(wh).data)

    @extend_schema(request=None, responses=WarehouseSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        wh = services.set_warehouse_active(self._obj(request, pk, "inventory.warehouse.manage"), False,
                                           actor=request.user, request=request)
        return Response(WarehouseSerializer(wh).data)


@extend_schema(parameters=[ID_PARAM])
class StockBalanceViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "inventory.view", "retrieve": "inventory.view", "receive": "inventory.receive",
                      "adjust": "inventory.adjust", "levels": "inventory.adjust", "transfer": "inventory.transfer",
                      "movements": "inventory.view"}

    def _wh(self, request, pk, code):
        return selectors.get_warehouse(request.membership, request.organization, pk, code)

    @extend_schema(parameters=[query_param("q", "part number / name"),
                               query_param("warehouse", "warehouse id", OpenApiTypes.UUID),
                               query_param("part", "part id", OpenApiTypes.UUID),
                               query_param("site", "site id", OpenApiTypes.UUID),
                               query_param("low", "1 = at or below the minimum"),
                               query_param("in_stock", "1 = on hand > 0")], responses=BalanceSerializer(many=True))
    def list(self, request):
        qs = selectors.filter_balances(selectors.balances_for(request.membership, request.organization),
                                       request.query_params)
        return paginate(request, qs, BalanceSerializer)

    @extend_schema(responses=BalanceSerializer)
    def retrieve(self, request, pk=None):
        return Response(BalanceSerializer(selectors.get_balance(request.membership, request.organization, pk)).data)

    @extend_schema(responses=MovementSerializer(many=True))
    @action(detail=True, methods=["get"])
    def movements(self, request, pk=None):
        bal = selectors.get_balance(request.membership, request.organization, pk)
        qs = selectors.movements_for(request.membership, request.organization).filter(balance=bal)
        return paginate(request, qs, MovementSerializer)

    @extend_schema(request=StockInSerializer, responses={201: MovementSerializer})
    @action(detail=False, methods=["post"])
    def receive(self, request):
        d = _validated(StockInSerializer, request)
        wh = self._wh(request, d["warehouse"], "inventory.receive")
        part = selectors.get_part(request.organization, d["part"])
        mv = services.receive(wh, part, d["quantity"], actor=request.user, reference=d.get("reference", ""),
                              reason=d.get("reason", ""), request=request)
        return Response(MovementSerializer(mv).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=AdjustSerializer, responses={201: MovementSerializer})
    @action(detail=False, methods=["post"])
    def adjust(self, request):
        d = _validated(AdjustSerializer, request)
        wh = self._wh(request, d["warehouse"], "inventory.adjust")
        part = selectors.get_part(request.organization, d["part"])
        mv = services.adjust(wh, part, d["delta"], reason=d["reason"], actor=request.user, request=request)
        return Response(MovementSerializer(mv).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=LevelsSerializer, responses=BalanceSerializer)
    @action(detail=False, methods=["post"])
    def levels(self, request):
        d = _validated(LevelsSerializer, request)
        wh = self._wh(request, d["warehouse"], "inventory.adjust")
        part = selectors.get_part(request.organization, d["part"])
        bal = services.set_levels(wh, part, min_level=d.get("min_level"), max_level=d.get("max_level"),
                                  reorder_quantity=d.get("reorder_quantity"), actor=request.user, request=request)
        return Response(BalanceSerializer(bal).data)

    @extend_schema(request=TransferSerializer, responses={201: MovementSerializer(many=True)})
    @action(detail=False, methods=["post"])
    def transfer(self, request):
        d = _validated(TransferSerializer, request)
        src = self._wh(request, d["source"], "inventory.transfer")
        dst = self._wh(request, d["target"], "inventory.transfer")
        part = selectors.get_part(request.organization, d["part"])
        out, inn = services.transfer(src, dst, part, d["quantity"], actor=request.user, reason=d.get("reason", ""),
                                     request=request)
        return Response(MovementSerializer([out, inn], many=True).data, status=status.HTTP_201_CREATED)


@extend_schema(parameters=[ID_PARAM])
class StockMovementViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "inventory.view", "retrieve": "inventory.view"}

    @extend_schema(parameters=[query_param("warehouse", "warehouse id", OpenApiTypes.UUID),
                               query_param("part", "part id", OpenApiTypes.UUID),
                               query_param("work_order", "work order id", OpenApiTypes.UUID),
                               query_param("type", "movement type")], responses=MovementSerializer(many=True))
    def list(self, request):
        qs = selectors.filter_movements(selectors.movements_for(request.membership, request.organization),
                                        request.query_params)
        return paginate(request, qs, MovementSerializer)

    @extend_schema(responses=MovementSerializer)
    def retrieve(self, request, pk=None):
        from apps.sites.selectors import scoped_get

        qs = selectors.movements_for(request.membership, request.organization)
        return Response(MovementSerializer(scoped_get(qs, pk, "Movement")).data)


@extend_schema(parameters=[ID_PARAM])
class PartReservationViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "inventory.view", "retrieve": "inventory.view"}

    @extend_schema(parameters=[query_param("work_order", "work order id", OpenApiTypes.UUID),
                               query_param("warehouse", "warehouse id", OpenApiTypes.UUID),
                               query_param("status", "ACTIVE / RELEASED / FULFILLED")],
                   responses=ReservationSerializer(many=True))
    def list(self, request):
        qs = selectors.filter_reservations(selectors.reservations_for(request.membership, request.organization),
                                           request.query_params)
        return paginate(request, qs, ReservationSerializer)

    @extend_schema(responses=ReservationSerializer)
    def retrieve(self, request, pk=None):
        from apps.sites.selectors import scoped_get

        qs = selectors.reservations_for(request.membership, request.organization)
        return Response(ReservationSerializer(scoped_get(qs, pk, "Reservation")).data)


@extend_schema(parameters=[ID_PARAM])
class WorkOrderPartViewSet(TenantAPIMixin, viewsets.ViewSet):
    """Part requirements of work orders (M09 part request) and their stock operations."""

    permission_map = {
        "list": "work_order.view_assigned", "retrieve": "work_order.view_assigned", "create": "inventory.request",
        "reserve": "inventory.reserve", "release": "inventory.reserve", "issue": "inventory.issue",
        "consume": "inventory.consume", "return_stock": "inventory.return", "reconcile": "inventory.reconcile",
        "cancel": "inventory.request", "movements": "inventory.view",
    }

    def _line(self, request, pk, code=None) -> WorkOrderPart:
        line = selectors.get_line(request.membership, request.organization, pk)  # 404 outside tenant / WO scope
        if code:
            require_permission(request.membership, code, line.work_order.site_id)
        return line

    def _warehouse(self, request, pk, code):
        return selectors.get_warehouse(request.membership, request.organization, pk, code)

    def _out(self, request, line):
        return Response(PartLineSerializer(selectors.get_line(request.membership, request.organization, line.pk)).data)

    @extend_schema(parameters=[query_param("work_order", "work order id", OpenApiTypes.UUID),
                               query_param("status", "line status")], responses=PartLineSerializer(many=True))
    def list(self, request):
        qs = selectors.lines_visible(request.membership, request.organization).select_related("reservation")
        qs = selectors._uuid_filter(qs, request.query_params, "work_order", "work_order_id")
        st = (request.query_params.get("status") or "").strip()
        if st:
            qs = qs.filter(status=st)
        return paginate(request, qs.order_by("-created_at"), PartLineSerializer)

    @extend_schema(responses=PartLineSerializer)
    def retrieve(self, request, pk=None):
        return self._out(request, self._line(request, pk))

    @extend_schema(request=PartLineCreateSerializer, responses={201: PartLineSerializer})
    def create(self, request):
        d = _validated(PartLineCreateSerializer, request)
        wo = wo_selectors.get_work_order(request.membership, request.organization, d["work_order"])
        require_permission(request.membership, "inventory.request", wo.site_id)
        part = selectors.get_part(request.organization, d["part"])
        line = services.request_part(wo, part, d["quantity"], actor=request.user, membership=request.membership,
                                     notes=d.get("notes", ""), request=request)
        out = self._out(request, line)
        out.status_code = status.HTTP_201_CREATED
        return out

    @extend_schema(request=ReserveSerializer, responses=PartLineSerializer)
    @action(detail=True, methods=["post"])
    def reserve(self, request, pk=None):
        line = self._line(request, pk, "inventory.reserve")
        d = _validated(ReserveSerializer, request)
        wh = self._warehouse(request, d["warehouse"], "inventory.reserve")
        services.reserve(line, wh, d.get("quantity"), actor=request.user, request=request)
        return self._out(request, line)

    @extend_schema(request=ReleaseSerializer, responses=PartLineSerializer)
    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        line = self._line(request, pk, "inventory.reserve")
        d = _validated(ReleaseSerializer, request)
        services.release(line, d.get("quantity"), actor=request.user, request=request)
        return self._out(request, line)

    @extend_schema(request=IssueSerializer, responses=PartLineSerializer)
    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        line = self._line(request, pk, "inventory.issue")
        d = _validated(IssueSerializer, request)
        wh = self._warehouse(request, d["warehouse"], "inventory.issue")
        services.issue(line, wh, d["quantity"], actor=request.user, request=request)
        return self._out(request, line)

    @extend_schema(request=QuantitySerializer, responses=PartLineSerializer)
    @action(detail=True, methods=["post"])
    def consume(self, request, pk=None):
        line = self._line(request, pk, "inventory.consume")
        d = _validated(QuantitySerializer, request)
        services.consume(line, d["quantity"], actor=request.user, membership=request.membership, request=request)
        return self._out(request, line)

    @extend_schema(request=QuantitySerializer, responses=PartLineSerializer)
    @action(detail=True, methods=["post"], url_path="return")
    def return_stock(self, request, pk=None):
        line = self._line(request, pk, "inventory.return")
        d = _validated(QuantitySerializer, request)
        services.return_stock(line, d["quantity"], reason=d.get("reason", ""), actor=request.user, request=request)
        return self._out(request, line)

    @extend_schema(request=None, responses=PartLineSerializer)
    @action(detail=True, methods=["post"])
    def reconcile(self, request, pk=None):
        line = self._line(request, pk, "inventory.reconcile")
        services.reconcile(line, actor=request.user, request=request)
        return self._out(request, line)

    @extend_schema(request=None, responses=PartLineSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        line = self._line(request, pk)  # the service decides: stores staff, or the requester of an untouched line
        services.cancel_line(line, actor=request.user, membership=request.membership, request=request)
        return self._out(request, line)

    @extend_schema(responses=MovementSerializer(many=True))
    @action(detail=True, methods=["get"])
    def movements(self, request, pk=None):
        line = self._line(request, pk)
        qs = selectors.movements_for(request.membership, request.organization).filter(part_line=line)
        return paginate(request, qs, MovementSerializer)
