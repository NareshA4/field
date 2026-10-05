from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.tenancy import selectors


class LoginThrottleMixin:
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


class TokenObtain(LoginThrottleMixin, TokenObtainPairView):
    authentication_classes: list = []
    permission_classes: list = []


class TokenRefresh(LoginThrottleMixin, TokenRefreshView):
    authentication_classes: list = []
    permission_classes: list = []


class MeOrganizationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    slug = serializers.CharField()
    name = serializers.CharField()


class MeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    full_name = serializers.CharField()
    is_platform_admin = serializers.BooleanField()
    organizations = MeOrganizationSerializer(many=True)


class MeView(APIView):
    """The authenticated identity and the organizations they can act in. Not tenant-scoped."""

    @extend_schema(summary="Current user and organization memberships", responses=MeSerializer)
    def get(self, request):
        u = request.user
        return Response({
            "id": str(u.pk), "email": u.email, "full_name": u.full_name,
            "is_platform_admin": u.is_platform_admin,
            "organizations": [
                {"id": str(m.organization_id), "slug": m.organization.slug, "name": m.organization.name}
                for m in selectors.active_memberships(u)
            ],
        })
