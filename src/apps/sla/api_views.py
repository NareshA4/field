"""M11 REST API (``/api/v1/sla-profiles|sla-trackings|sla-breaches|sla-metrics``).

``TenantAPIMixin`` + ``permission_map`` (unmapped = denied) -> object lookup restricted to organization AND site
scope (404) -> permission for the object's site (403) -> service. Configuration needs ``sla.manage``; running the
monitor needs ``sla.process``."""
from datetime import datetime

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.apiutils import ID_PARAM, paginate, query_param, require_permission
from apps.core.exceptions import NotFound, ValidationFailed
from apps.rbac.models import Role
from apps.sites import selectors as site_selectors
from apps.tenancy.api import TenantAPIMixin

from . import selectors, services
from .models import EscalationRule, SLABreach, SLAEvent, SLAProfile, SLATarget, SLATracking

# --- serializers -------------------------------------------------------------------------------------------------


class TargetSerializer(serializers.ModelSerializer):
    class Meta:
        model = SLATarget
        fields = ["id", "priority", "response_minutes", "resolution_minutes", "warning_percent"]
        read_only_fields = fields


class RuleSerializer(serializers.ModelSerializer):
    notify_role_name = serializers.CharField(source="notify_role.name", read_only=True, default=None)

    class Meta:
        model = EscalationRule
        fields = ["id", "target_kind", "trigger", "after_minutes", "level", "notify_role", "notify_role_name",
                  "notify_assignee"]
        read_only_fields = fields


class ProfileSerializer(serializers.ModelSerializer):
    site_code = serializers.CharField(source="site.code", read_only=True, default=None)
    targets = TargetSerializer(many=True, read_only=True)
    rules = RuleSerializer(many=True, read_only=True)

    class Meta:
        model = SLAProfile
        fields = ["id", "name", "description", "applies_to", "site", "site_code", "work_type", "pause_states",
                  "is_active", "coverage_only", "targets", "rules", "created_at", "updated_at"]
        read_only_fields = fields


class ProfileWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    applies_to = serializers.ChoiceField(choices=SLAProfile.AppliesTo.choices)
    description = serializers.CharField(max_length=300, required=False, allow_blank=True)
    site = serializers.UUIDField(required=False, allow_null=True)
    work_type = serializers.CharField(max_length=20, required=False, allow_blank=True)
    coverage_only = serializers.BooleanField(required=False)
    pause_states = serializers.ListField(child=serializers.CharField(), required=False)


class ProfilePatchSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False)
    description = serializers.CharField(max_length=300, required=False, allow_blank=True)
    pause_states = serializers.ListField(child=serializers.CharField(), required=False)


class TargetWriteSerializer(serializers.Serializer):
    priority = serializers.CharField(max_length=10)
    response_minutes = serializers.IntegerField()
    resolution_minutes = serializers.IntegerField()
    warning_percent = serializers.IntegerField(required=False)


class RuleWriteSerializer(serializers.Serializer):
    target_kind = serializers.ChoiceField(choices=[("RESPONSE", "Response"), ("RESOLUTION", "Resolution")])
    trigger = serializers.ChoiceField(choices=EscalationRule.Trigger.choices)
    after_minutes = serializers.IntegerField(required=False)
    level = serializers.IntegerField(required=False)
    notify_role = serializers.UUIDField(required=False, allow_null=True)
    notify_assignee = serializers.BooleanField(required=False)


class RemoveSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    priority = serializers.CharField(max_length=10, required=False)


class TrackingSerializer(serializers.ModelSerializer):
    subject_type = serializers.CharField(read_only=True)
    subject_id = serializers.SerializerMethodField()
    subject_number = serializers.SerializerMethodField()
    subject_title = serializers.SerializerMethodField()
    profile_name = serializers.CharField(source="profile.name", read_only=True)
    site_code = serializers.CharField(source="site.code", read_only=True)

    class Meta:
        model = SLATracking
        fields = ["id", "subject_type", "subject_id", "subject_number", "subject_title", "profile", "profile_name",
                  "site", "site_code", "priority", "response_minutes", "resolution_minutes", "warning_percent",
                  "started_at", "response_due_at", "resolution_due_at", "response_met_at", "resolution_met_at",
                  "response_state", "resolution_state", "status", "paused_at", "paused_seconds", "last_checked_at"]
        read_only_fields = fields

    def get_subject_id(self, obj) -> str:
        return str(obj.request_id or obj.work_order_id)

    def get_subject_number(self, obj) -> str:
        return obj.subject.number

    def get_subject_title(self, obj) -> str:
        return obj.subject.title


class SLAEventSerializer(serializers.ModelSerializer):
    actor_email = serializers.SerializerMethodField()

    class Meta:
        model = SLAEvent
        fields = ["id", "event_type", "target_kind", "at", "detail", "notified", "actor_email"]
        read_only_fields = fields

    def get_actor_email(self, obj) -> str | None:
        return obj.actor.email if obj.actor_id else None


class BreachSerializer(serializers.ModelSerializer):
    subject_type = serializers.CharField(source="tracking.subject_type", read_only=True)
    subject_number = serializers.SerializerMethodField()
    profile_name = serializers.CharField(source="profile.name", read_only=True)
    site_code = serializers.CharField(source="site.code", read_only=True)

    class Meta:
        model = SLABreach
        fields = ["id", "tracking", "subject_type", "subject_number", "profile", "profile_name", "site", "site_code",
                  "target_kind", "priority", "severity", "breached_at", "detected_at", "status", "escalation_level",
                  "escalated_at", "notified_at", "acknowledged_at", "closed_at", "closed_reason"]
        read_only_fields = fields

    def get_subject_number(self, obj) -> str:
        return obj.tracking.subject.number


def _validated(serializer_cls, request, partial=False):
    ser = serializer_cls(data=request.data, partial=partial)
    ser.is_valid(raise_exception=True)
    return dict(ser.validated_data)


# --- viewsets ------------------------------------------------------------------------------------------------------


@extend_schema(parameters=[ID_PARAM])
class SLAProfileViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "sla.view", "retrieve": "sla.view", "create": "sla.manage",
                      "partial_update": "sla.manage", "activate": "sla.manage", "deactivate": "sla.manage",
                      "targets": "sla.manage", "remove_target": "sla.manage", "rules": "sla.manage",
                      "remove_rule": "sla.manage"}

    def _profile(self, request, pk) -> SLAProfile:
        return selectors.get_profile(request.organization, pk)

    def _out(self, request, pk):
        return Response(ProfileSerializer(self._profile(request, pk)).data)

    @extend_schema(responses=ProfileSerializer(many=True))
    def list(self, request):
        return paginate(request, selectors.profiles_for(request.organization).prefetch_related("targets", "rules"),
                        ProfileSerializer)

    @extend_schema(responses=ProfileSerializer)
    def retrieve(self, request, pk=None):
        return self._out(request, pk)

    @extend_schema(request=ProfileWriteSerializer, responses={201: ProfileSerializer})
    def create(self, request):
        d = _validated(ProfileWriteSerializer, request)
        site = None
        if d.get("site"):
            site = site_selectors.get_site(request.membership, request.organization, d.pop("site"))
        d.pop("site", None)
        profile = services.create_profile(request.organization, site=site, actor=request.user, request=request, **d)
        return Response(ProfileSerializer(profile).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ProfilePatchSerializer, responses=ProfileSerializer)
    def partial_update(self, request, pk=None):
        d = _validated(ProfilePatchSerializer, request, partial=True)
        services.update_profile(self._profile(request, pk), actor=request.user, request=request, **d)
        return self._out(request, pk)

    @extend_schema(request=None, responses=ProfileSerializer)
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        services.set_profile_active(self._profile(request, pk), True, actor=request.user, request=request)
        return self._out(request, pk)

    @extend_schema(request=None, responses=ProfileSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        services.set_profile_active(self._profile(request, pk), False, actor=request.user, request=request)
        return self._out(request, pk)

    @extend_schema(request=TargetWriteSerializer, responses=ProfileSerializer)
    @action(detail=True, methods=["post"])
    def targets(self, request, pk=None):
        d = _validated(TargetWriteSerializer, request)
        services.set_target(self._profile(request, pk), actor=request.user, request=request, **d)
        return self._out(request, pk)

    @extend_schema(request=RemoveSerializer, responses=ProfileSerializer)
    @action(detail=True, methods=["post"], url_path="targets/remove")
    def remove_target(self, request, pk=None):
        d = _validated(RemoveSerializer, request)
        profile = self._profile(request, pk)
        target = SLATarget.objects.for_organization(request.organization).filter(
            profile=profile, priority=d.get("priority")).first()
        if target is None:
            raise NotFound("Target not found.")
        services.remove_target(target, actor=request.user, request=request)
        return self._out(request, pk)

    @extend_schema(request=RuleWriteSerializer, responses=ProfileSerializer)
    @action(detail=True, methods=["post"])
    def rules(self, request, pk=None):
        d = _validated(RuleWriteSerializer, request)
        role = None
        if d.get("notify_role"):
            try:
                role = Role.objects.for_organization(request.organization).get(pk=d.pop("notify_role"))
            except Role.DoesNotExist as exc:
                raise NotFound("Role not found.") from exc
        d.pop("notify_role", None)
        services.create_rule(self._profile(request, pk), notify_role=role, actor=request.user, request=request, **d)
        return self._out(request, pk)

    @extend_schema(request=RemoveSerializer, responses=ProfileSerializer)
    @action(detail=True, methods=["post"], url_path="rules/remove")
    def remove_rule(self, request, pk=None):
        d = _validated(RemoveSerializer, request)
        profile = self._profile(request, pk)
        if not d.get("id"):
            raise ValidationFailed("A rule id is required.", code="rule_required")
        rule = EscalationRule.objects.for_organization(request.organization).filter(profile=profile,
                                                                                    pk=d["id"]).first()
        if rule is None:
            raise NotFound("Rule not found.")
        services.delete_rule(rule, actor=request.user, request=request)
        return self._out(request, pk)


@extend_schema(parameters=[ID_PARAM])
class SLATrackingViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "sla.view", "retrieve": "sla.view", "events": "sla.view", "process": "sla.process"}

    @extend_schema(parameters=[query_param("site", "site id", OpenApiTypes.UUID),
                               query_param("status", "ACTIVE | PAUSED | COMPLETED | CANCELLED"),
                               query_param("priority", "priority"), query_param("subject", "REQUEST | WORK_ORDER"),
                               query_param("breached", "1 = any target missed"), query_param("q", "number / title"),
                               query_param("request", "request id", OpenApiTypes.UUID),
                               query_param("work_order", "work order id", OpenApiTypes.UUID)],
                   responses=TrackingSerializer(many=True))
    def list(self, request):
        qs = selectors.filter_trackings(selectors.trackings_for(request.membership, request.organization),
                                        request.query_params)
        return paginate(request, qs, TrackingSerializer)

    @extend_schema(responses=TrackingSerializer)
    def retrieve(self, request, pk=None):
        return Response(TrackingSerializer(selectors.get_tracking(request.membership, request.organization, pk)).data)

    @extend_schema(responses=SLAEventSerializer(many=True))
    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        tracking = selectors.get_tracking(request.membership, request.organization, pk)
        return paginate(request, selectors.events_for(request.organization, tracking), SLAEventSerializer)

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["post"])
    def process(self, request):
        """Runs the SLA monitor for the caller's organization now (the same code the Celery task runs)."""
        require_permission(request.membership, "sla.process")
        return Response(services.process_organization(request.organization))


@extend_schema(parameters=[ID_PARAM])
class SLABreachViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "sla.view", "retrieve": "sla.view", "acknowledge": "sla.acknowledge"}

    def _breach(self, request, pk, code=None) -> SLABreach:
        breach = selectors.get_breach(request.membership, request.organization, pk)
        if code:
            require_permission(request.membership, code, breach.site_id)
        return breach

    @extend_schema(parameters=[query_param("site", "site id", OpenApiTypes.UUID),
                               query_param("status", "OPEN | ACKNOWLEDGED | CLOSED"),
                               query_param("target", "RESPONSE | RESOLUTION")],
                   responses=BreachSerializer(many=True))
    def list(self, request):
        qs = selectors.filter_breaches(selectors.breaches_for(request.membership, request.organization),
                                       request.query_params)
        return paginate(request, qs, BreachSerializer)

    @extend_schema(responses=BreachSerializer)
    def retrieve(self, request, pk=None):
        return Response(BreachSerializer(self._breach(request, pk)).data)

    @extend_schema(request=None, responses=BreachSerializer)
    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        breach = self._breach(request, pk, "sla.acknowledge")
        services.acknowledge_breach(breach, actor=request.user, request=request)
        return Response(BreachSerializer(self._breach(request, pk)).data)


class SLAMetricsView(TenantAPIMixin, APIView):
    """Aggregated SLA data for the future M14 dashboards (real counts over the caller's visible trackings)."""

    permission_map = {"get": "sla.view"}

    @extend_schema(parameters=[query_param("since", "ISO date-time", OpenApiTypes.DATETIME),
                               query_param("until", "ISO date-time", OpenApiTypes.DATETIME)],
                   responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        def parse(name):
            raw = (request.query_params.get(name) or "").strip()
            if not raw:
                return None
            try:
                value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValidationFailed(f"{name} must be an ISO date-time.", code="invalid_date") from exc
            from django.utils import timezone

            return timezone.make_aware(value) if timezone.is_naive(value) else value

        return Response(selectors.metrics(request.membership, request.organization, since=parse("since"),
                                          until=parse("until")))
