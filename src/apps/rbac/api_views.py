import uuid

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.response import Response

from apps.core.exceptions import NotFound
from apps.tenancy.api import TenantAPIMixin

from . import catalog, services
from .models import Permission, Role


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["code", "module", "description"]


class RoleSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()
    is_system = serializers.BooleanField(read_only=True)

    class Meta:
        model = Role
        fields = ["id", "name", "description", "is_system", "is_owner", "permissions"]

    def get_permissions(self, obj) -> list[str]:
        if obj.is_owner:
            return sorted(catalog.all_codes())
        return sorted(rp.permission.code for rp in obj.role_permissions.all())


class RoleWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=80)
    description = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    permissions = serializers.ListField(child=serializers.CharField(max_length=80), required=False, default=list)


class PermissionViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "role.view"}

    @extend_schema(responses=PermissionSerializer(many=True))
    def list(self, request):
        return Response(PermissionSerializer(Permission.objects.all(), many=True).data)


@extend_schema(parameters=[OpenApiParameter("id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
class RoleViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "role.view", "retrieve": "role.view", "create": "role.manage",
                      "update": "role.manage", "destroy": "role.manage"}

    def _roles(self, request):
        return Role.objects.for_organization(request.organization).prefetch_related("role_permissions__permission")

    def _get(self, request, pk):
        try:
            return self._roles(request).get(pk=uuid.UUID(str(pk)))
        except (Role.DoesNotExist, ValueError) as exc:
            raise NotFound("Role not found.") from exc

    @extend_schema(responses=RoleSerializer(many=True))
    def list(self, request):
        return Response(RoleSerializer(self._roles(request), many=True).data)

    @extend_schema(responses=RoleSerializer)
    def retrieve(self, request, pk=None):
        return Response(RoleSerializer(self._get(request, pk)).data)

    @extend_schema(request=RoleWriteSerializer, responses={201: RoleSerializer})
    def create(self, request):
        ser = RoleWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        role = services.create_role(request.organization, name=d["name"], description=d["description"],
                                    permission_codes=d["permissions"], actor=request.user, request=request)
        return Response(RoleSerializer(self._get(request, role.pk)).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=RoleWriteSerializer, responses=RoleSerializer)
    def update(self, request, pk=None):
        role = self._get(request, pk)
        ser = RoleWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        services.update_role(role, name=d["name"], description=d["description"], permission_codes=d["permissions"],
                             actor=request.user, request=request)
        return Response(RoleSerializer(self._get(request, pk)).data)

    @extend_schema(responses={204: None})
    def destroy(self, request, pk=None):
        services.delete_role(self._get(request, pk), actor=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)
