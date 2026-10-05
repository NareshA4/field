from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from . import selectors, services
from .api import TenantAPIMixin
from .models import Membership

ORG_FIELDS = ["name", "legal_name", "timezone", "country", "contact_email", "contact_phone", "address"]


class OrganizationSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    slug = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    name = serializers.CharField(max_length=150)
    legal_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    timezone = serializers.CharField(max_length=64, required=False)
    country = serializers.CharField(max_length=2, required=False, allow_blank=True)
    contact_email = serializers.EmailField(required=False, allow_blank=True)
    contact_phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)


class OrganizationViewSet(TenantAPIMixin, viewsets.ViewSet):
    """The caller's active organization."""

    permission_map = {"retrieve": "organization.view", "partial_update": "organization.update"}

    @extend_schema(responses=OrganizationSerializer)
    def retrieve(self, request):
        return Response(OrganizationSerializer(request.organization).data)

    @extend_schema(request=OrganizationSerializer, responses=OrganizationSerializer)
    def partial_update(self, request):
        ser = OrganizationSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        org = services.update_organization(
            request.organization, actor=request.user, request=request, **ser.validated_data
        )
        return Response(OrganizationSerializer(org).data)


class MemberSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    roles = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = ["id", "email", "full_name", "job_title", "status", "roles", "invited_at", "activated_at"]

    def get_roles(self, obj) -> list[dict]:
        return [{"id": str(mr.role_id), "name": mr.role.name, "assignment_id": str(mr.pk),
                 "site_id": str(mr.site_id) if mr.site_id else None,
                 "site_code": mr.site.code if mr.site_id else None}
                for mr in obj.membership_roles.all()]


class RoleAssignmentSerializer(serializers.Serializer):
    role_id = serializers.UUIDField()
    site_id = serializers.UUIDField(required=False, allow_null=True, default=None)


class _AssignmentInput(serializers.Serializer):
    """Either ``role_ids`` (+ optional ``site_ids`` limiting every listed role to those sites) or explicit
    ``role_assignments`` [{role_id, site_id|null}]. No site = organization-wide."""

    role_ids = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=False)
    site_ids = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=False)
    role_assignments = RoleAssignmentSerializer(many=True, required=False, allow_empty=False)

    def validate(self, attrs):
        if "role_assignments" in attrs and ("role_ids" in attrs or "site_ids" in attrs):
            raise serializers.ValidationError("Use either role_assignments or role_ids/site_ids, not both.")
        if "site_ids" in attrs and "role_ids" not in attrs:
            raise serializers.ValidationError("site_ids requires role_ids.")
        return attrs


class InviteSerializer(_AssignmentInput):
    email = serializers.EmailField()
    full_name = serializers.CharField(max_length=150, required=False, allow_blank=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if "role_ids" not in attrs and "role_assignments" not in attrs:
            raise serializers.ValidationError("role_ids or role_assignments is required.")
        return attrs


class MemberUpdateSerializer(_AssignmentInput):
    full_name = serializers.CharField(max_length=150, required=False)
    job_title = serializers.CharField(max_length=100, required=False, allow_blank=True)


@extend_schema(parameters=[OpenApiParameter("id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
class MemberViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {
        "list": "user.view", "retrieve": "user.view", "create": "user.invite",
        "partial_update": "user.update", "deactivate": "user.deactivate",
        "reactivate": "user.deactivate", "resend": "user.invite",
    }

    def _qs(self, request):
        return selectors.organization_members(request.organization)

    @extend_schema(responses=MemberSerializer(many=True))
    def list(self, request):
        qs = self._qs(request).order_by("user__full_name")
        status_f = request.query_params.get("status")
        if status_f in Membership.Status.values:
            qs = qs.filter(status=status_f)
        from apps.core.api import StandardPagination

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(MemberSerializer(page, many=True).data)

    @extend_schema(responses=MemberSerializer)
    def retrieve(self, request, pk=None):
        return Response(MemberSerializer(services.get_membership(request.organization, pk)).data)

    @extend_schema(request=InviteSerializer, responses={201: MemberSerializer})
    def create(self, request):
        ser = InviteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        m = services.invite_member(request.organization, email=d["email"], full_name=d.get("full_name", ""),
                                   role_ids=d.get("role_ids"), site_ids=d.get("site_ids"),
                                   assignments=d.get("role_assignments"), actor=request.user, request=request)
        return Response(MemberSerializer(m).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=MemberUpdateSerializer, responses=MemberSerializer)
    def partial_update(self, request, pk=None):
        m = services.get_membership(request.organization, pk)
        ser = MemberUpdateSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        services.update_member(m, actor=request.user, request=request, full_name=d.get("full_name"),
                               job_title=d.get("job_title"), role_ids=d.get("role_ids"),
                               site_ids=d.get("site_ids"), assignments=d.get("role_assignments"))
        return Response(MemberSerializer(services.get_membership(request.organization, pk)).data)

    @extend_schema(request=None, responses=MemberSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        m = services.get_membership(request.organization, pk)
        services.set_member_active(m, active=False, actor=request.user, request=request)
        return Response(MemberSerializer(services.get_membership(request.organization, pk)).data)

    @extend_schema(request=None, responses=MemberSerializer)
    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        m = services.get_membership(request.organization, pk)
        services.set_member_active(m, active=True, actor=request.user, request=request)
        return Response(MemberSerializer(services.get_membership(request.organization, pk)).data)

    @extend_schema(request=None, responses={202: None})
    @action(detail=True, methods=["post"])
    def resend(self, request, pk=None):
        m = services.get_membership(request.organization, pk)
        services.resend_invitation(m, actor=request.user, request=request)
        return Response(status=status.HTTP_202_ACCEPTED)
