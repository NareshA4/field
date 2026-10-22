from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.assets import selectors as asset_selectors
from apps.core.apiutils import ID_PARAM, paginate, query_param, require_permission
from apps.core.exceptions import ValidationFailed
from apps.tenancy.api import TenantAPIMixin

from . import selectors, services
from .models import AssetIdentifier, ScanEvent


class IdentifierSerializer(serializers.ModelSerializer):
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    asset_name = serializers.CharField(source="asset.name", read_only=True)

    class Meta:
        model = AssetIdentifier
        fields = [
            "id",
            "asset",
            "asset_tag",
            "asset_name",
            "kind",
            "token",
            "is_active",
            "revoked_at",
            "revoked_reason",
            "created_at",
        ]
        read_only_fields = fields


class IdentifierWriteSerializer(serializers.Serializer):
    asset = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=AssetIdentifier.Kind.choices, default=AssetIdentifier.Kind.QR)


class IdentifierActionSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300)


class ScanEventSerializer(serializers.ModelSerializer):
    scanned_by = serializers.EmailField(source="scanned_by.email", read_only=True, default=None)
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)

    class Meta:
        model = ScanEvent
        fields = ["id", "asset", "asset_tag", "identifier", "scanned_by", "notes", "created_at"]
        read_only_fields = fields


@extend_schema(parameters=[ID_PARAM])
class IdentifierViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {
        "list": "asset.view",
        "retrieve": "asset.view",
        "create": "asset.update",
        "replace": "asset.update",
        "revoke": "asset.update",
        "resolve": "asset.view",
    }

    def _ident(self, request, pk, code=None) -> AssetIdentifier:
        ident = selectors.get_identifier(request.organization, pk)
        if code:
            require_permission(request.membership, code, ident.asset.site_id)
        return ident

    @extend_schema(
        parameters=[
            query_param("asset", "asset id", OpenApiTypes.UUID),
            query_param("kind", "QR or BARCODE"),
            query_param("active", "true/false"),
        ],
        responses=IdentifierSerializer(many=True),
    )
    def list(self, request):
        import uuid

        qs = AssetIdentifier.objects.for_organization(request.organization).select_related("asset")
        asset_id = request.query_params.get("asset")
        if asset_id:
            try:
                qs = qs.filter(asset_id=uuid.UUID(asset_id))
            except ValueError:
                qs = qs.none()
        kind = request.query_params.get("kind")
        if kind:
            qs = qs.filter(kind=kind.upper())
        active = request.query_params.get("active")
        if active is not None:
            qs = qs.filter(is_active=active.lower() in ("1", "true", "yes"))
        return paginate(request, qs, IdentifierSerializer)

    @extend_schema(responses=IdentifierSerializer)
    def retrieve(self, request, pk=None):
        return Response(IdentifierSerializer(self._ident(request, pk)).data)

    @extend_schema(request=IdentifierWriteSerializer, responses={201: IdentifierSerializer})
    def create(self, request):
        ser = IdentifierWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        asset = asset_selectors.get_asset(request.membership, request.organization, d["asset"])
        require_permission(request.membership, "asset.update", asset.site_id)
        ident = services.generate_identifier(asset, kind=d["kind"], actor=request.user, request=request)
        return Response(IdentifierSerializer(ident).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=IdentifierActionSerializer, responses=IdentifierSerializer)
    @action(detail=True, methods=["post"])
    def replace(self, request, pk=None):
        ident = self._ident(request, pk, "asset.update")
        ser = IdentifierActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        new_ident = services.replace_identifier(
            ident, reason=ser.validated_data["reason"], actor=request.user, request=request
        )
        return Response(IdentifierSerializer(new_ident).data)

    @extend_schema(request=IdentifierActionSerializer, responses=IdentifierSerializer)
    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        ident = self._ident(request, pk, "asset.update")
        ser = IdentifierActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.revoke_identifier(ident, reason=ser.validated_data["reason"], actor=request.user, request=request)
        ident.refresh_from_db()
        return Response(IdentifierSerializer(ident).data)

    @extend_schema(
        parameters=[
            query_param("token", "label token string"),
            query_param("work_order", "optional work order UUID to verify asset match"),
        ],
        responses={200: OpenApiTypes.OBJECT},
    )
    @action(detail=False, methods=["get"])
    def resolve(self, request):
        token = request.query_params.get("token", "")
        if not token:
            raise ValidationFailed("Token parameter is required.")
        ident, asset, is_active = services.resolve_token(
            request.membership, token, actor=request.user, request=request
        )

        work_order_id = request.query_params.get("work_order")
        matching_job = None
        if work_order_id:
            from apps.workorders.models import WorkOrder
            wo = WorkOrder.objects.filter(organization=request.organization, pk=work_order_id).first()
            if wo:
                matches = (wo.asset_id == asset.pk)
                matching_job = {
                    "work_order_id": str(wo.pk),
                    "order_number": wo.order_number,
                    "matches_asset": matches,
                    "target_asset_id": str(wo.asset_id) if wo.asset_id else None,
                }
        else:
            from apps.workorders.models import WorkOrder
            my_wo = WorkOrder.objects.filter(
                organization=request.organization,
                asset=asset,
                assigned_to=request.membership,
                status__in=[WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.DISPATCHED, WorkOrder.Status.PLANNED],
            ).order_by("-started_at", "-created_at").first()
            if my_wo:
                matching_job = {
                    "work_order_id": str(my_wo.pk),
                    "order_number": my_wo.order_number,
                    "status": my_wo.status,
                    "matches_asset": True,
                }

        return Response(
            {
                "identifier": IdentifierSerializer(ident).data,
                "asset_id": str(asset.pk),
                "asset_tag": asset.asset_tag,
                "asset_name": asset.name,
                "is_active": is_active,
                "site_code": asset.site.code,
                "zone_name": asset.zone.name if asset.zone else None,
                "matching_job": matching_job,
            }
        )
