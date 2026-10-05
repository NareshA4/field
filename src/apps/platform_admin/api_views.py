from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.exceptions import NotFound
from apps.tenancy import services
from apps.tenancy.api import PlatformAPIMixin
from apps.tenancy.models import Organization


class OrgSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "slug", "status", "suspended_reason", "timezone", "country", "created_at"]


class OrgCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    slug = serializers.CharField(max_length=60, required=False, allow_blank=True)
    timezone = serializers.CharField(max_length=64, default="UTC")
    country = serializers.CharField(max_length=2, required=False, allow_blank=True, default="")
    owner_email = serializers.EmailField()
    owner_name = serializers.CharField(max_length=150)


class SuspendSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300)


@extend_schema(parameters=[OpenApiParameter("id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
class PlatformOrganizationViewSet(PlatformAPIMixin, viewsets.ViewSet):
    def _get(self, pk):
        try:
            return Organization.objects.get(pk=pk)
        except (Organization.DoesNotExist, ValueError, Exception) as exc:
            raise NotFound("Organization not found.") from exc

    @extend_schema(responses=OrgSerializer(many=True))
    def list(self, request):
        from apps.core.api import StandardPagination

        p = StandardPagination()
        page = p.paginate_queryset(Organization.objects.all(), request)
        return p.get_paginated_response(OrgSerializer(page, many=True).data)

    @extend_schema(responses=OrgSerializer)
    def retrieve(self, request, pk=None):
        return Response(OrgSerializer(self._get(pk)).data)

    @extend_schema(request=OrgCreateSerializer, responses={201: OrgSerializer})
    def create(self, request):
        ser = OrgCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        org, _ = services.create_organization(
            name=d["name"], slug=d.get("slug") or None, timezone_name=d["timezone"], country=d["country"],
            owner_email=d["owner_email"], owner_name=d["owner_name"], actor=request.user, request=request)
        return Response(OrgSerializer(org).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=SuspendSerializer, responses=OrgSerializer)
    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        ser = SuspendSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        org = self._get(pk)
        services.set_organization_status(org, active=False, actor=request.user, reason=ser.validated_data["reason"],
                                         request=request)
        return Response(OrgSerializer(self._get(pk)).data)

    @extend_schema(request=None, responses=OrgSerializer)
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        org = self._get(pk)
        services.set_organization_status(org, active=True, actor=request.user, request=request)
        return Response(OrgSerializer(self._get(pk)).data)
