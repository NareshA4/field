from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import parsers, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.assets.models import Asset
from apps.core.apiutils import paginate
from apps.core.exceptions import Conflict, DomainError, NotFound, ValidationFailed
from apps.incidents.models import ServiceRequest
from apps.tenancy.api import TenantAPIMixin
from apps.tenancy.models import Membership

from . import selectors, services
from .models import PortalAccount, PortalAssetGrant, PortalClosureConfirmation


class PortalAccountSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="membership.user.email", read_only=True)
    user_name = serializers.CharField(source="membership.user.full_name", read_only=True)
    membership_status = serializers.CharField(source="membership.status", read_only=True)

    class Meta:
        model = PortalAccount
        fields = [
            "id",
            "membership",
            "user_email",
            "user_name",
            "membership_status",
            "company_name",
            "is_enabled",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "user_email", "user_name", "membership_status"]


class PortalAccountCreateSerializer(serializers.Serializer):
    membership = serializers.UUIDField()
    company_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    is_enabled = serializers.BooleanField(required=False, default=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class PortalAccountViewSet(TenantAPIMixin, viewsets.ModelViewSet):
    """Staff ViewSet to manage client portal accounts within the organization.

    Requires portal.manage permission.
    """

    permission_map = {
        "list": "portal.manage",
        "retrieve": "portal.manage",
        "create": "portal.manage",
        "update": "portal.manage",
        "partial_update": "portal.manage",
        "toggle": "portal.manage",
    }
    serializer_class = PortalAccountSerializer

    def get_queryset(self):
        org = getattr(self.request, "organization", None)
        if org is None:
            return PortalAccount.objects.none()
        return selectors.accounts_for_organization(org)

    def create(self, request, *args, **kwargs):
        write_serializer = PortalAccountCreateSerializer(data=request.data)
        write_serializer.is_valid(raise_exception=True)
        data = write_serializer.validated_data

        mem_id = data["membership"]
        membership = Membership.objects.for_organization(request.organization).filter(pk=mem_id).first()
        if not membership:
            return Response({"membership": ["Membership not found in this organization."]}, status=status.HTTP_400_BAD_REQUEST)

        try:
            account = services.create_portal_account(
                organization=request.organization,
                membership=membership,
                company_name=data.get("company_name", ""),
                is_enabled=data.get("is_enabled", True),
                notes=data.get("notes", ""),
                actor=request.user,
            )
        except DomainError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_409_CONFLICT)

        read_serializer = PortalAccountSerializer(account)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        data = serializer.validated_data
        account = serializer.instance
        services.update_portal_account(
            account,
            company_name=data.get("company_name", account.company_name),
            notes=data.get("notes", account.notes),
            actor=self.request.user,
        )

    @action(detail=True, methods=["post"])
    def toggle(self, request, pk=None):
        account = self.get_object()
        updated = services.toggle_portal_account(account, actor=request.user)
        return Response(PortalAccountSerializer(updated).data)


class PortalMeView(TenantAPIMixin, APIView):
    """Returns portal authentication and account details for the calling client user."""

    permission_map = {
        "get": "portal.request.view",
    }

    @extend_schema(
        responses={200: PortalAccountSerializer},
        summary="Get client portal profile",
        description="Returns portal account details for the authenticated client in the active tenant organization.",
    )
    def get(self, request):
        try:
            account = services.verify_portal_access(request.membership)
        except Exception as exc:
            raise DRFPermissionDenied(str(exc)) from exc

        return Response({
            "id": str(account.pk),
            "company_name": account.company_name or request.organization.name,
            "is_enabled": account.is_enabled,
            "user": {
                "id": str(request.user.pk),
                "email": request.user.email,
                "full_name": request.user.full_name,
            },
            "organization": {
                "id": str(request.organization.pk),
                "name": request.organization.name,
                "slug": request.organization.slug,
            },
        })


class PortalAssetSerializer(serializers.ModelSerializer):
    site_id = serializers.UUIDField(source="site.id", read_only=True)
    site_name = serializers.CharField(source="site.name", read_only=True)
    site_code = serializers.CharField(source="site.code", read_only=True)
    model_number = serializers.CharField(source="model", read_only=True)

    class Meta:
        model = Asset
        fields = [
            "id",
            "asset_tag",
            "name",
            "category",
            "status",
            "site_id",
            "site_name",
            "site_code",
            "model_number",
            "serial_number",
            "created_at",
        ]
        read_only_fields = fields


class PortalAssetListView(TenantAPIMixin, APIView):
    """Client / Requester endpoint to list equipment assets explicitly granted to the client account."""

    permission_map = {
        "get": "portal.request.view",
    }

    @extend_schema(
        responses={200: PortalAssetSerializer(many=True)},
        summary="List granted client assets",
        description="Returns a paginated list of non-terminal equipment assets explicitly granted to the authenticated client.",
    )
    def get(self, request):
        try:
            services.verify_portal_access(request.membership)
        except DomainError as exc:
            raise DRFPermissionDenied(str(exc)) from exc

        qs = selectors.granted_assets_for(request.membership)
        return paginate(request, qs, PortalAssetSerializer)


class PortalServiceRequestCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, min_length=3)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    asset = serializers.UUIDField(required=True)
    site = serializers.UUIDField(required=False, allow_null=True)
    kind = serializers.ChoiceField(
        choices=ServiceRequest.Kind.choices,
        required=False,
        default=ServiceRequest.Kind.SERVICE_REQUEST,
    )
    severity = serializers.ChoiceField(
        choices=[
            (ServiceRequest.Severity.LOW, "Low"),
            (ServiceRequest.Severity.MEDIUM, "Medium"),
            (ServiceRequest.Severity.HIGH, "High"),
        ],
        required=False,
        default=ServiceRequest.Severity.MEDIUM,
    )
    service_impact = serializers.ChoiceField(
        choices=ServiceRequest.Impact.choices,
        required=False,
        default=ServiceRequest.Impact.NONE,
    )
    impact_notes = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    occurred_at = serializers.DateTimeField(required=False, allow_null=True)

    def to_internal_value(self, data):
        if hasattr(data, "dict"):
            data = data.dict()
        elif hasattr(data, "copy"):
            data = data.copy()
        else:
            data = dict(data)
        if "title" not in data and "subject" in data:
            data["title"] = data["subject"]
        if "description" not in data and "problem_description" in data:
            data["description"] = data["problem_description"]
        if "severity" not in data and "priority" in data:
            data["severity"] = data["priority"]
        return super().to_internal_value(data)


class PortalServiceRequestSerializer(serializers.ModelSerializer):
    reference_number = serializers.CharField(source="number", read_only=True)
    status_label = serializers.SerializerMethodField()
    progress_stage = serializers.SerializerMethodField()
    priority = serializers.CharField(source="severity", read_only=True)
    asset_id = serializers.UUIDField(source="asset.id", read_only=True)
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    asset_name = serializers.CharField(source="asset.name", read_only=True)
    site_id = serializers.UUIDField(source="site.id", read_only=True)
    site_code = serializers.CharField(source="site.code", read_only=True)
    site_name = serializers.CharField(source="site.name", read_only=True)
    reported_by_id = serializers.UUIDField(source="reported_by.id", read_only=True)
    reported_by_name = serializers.CharField(source="reported_by.user.full_name", read_only=True)
    reported_by_email = serializers.CharField(source="reported_by.user.email", read_only=True)
    attachments_count = serializers.SerializerMethodField()

    class Meta:
        model = ServiceRequest
        fields = [
            "id",
            "number",
            "reference_number",
            "kind",
            "title",
            "description",
            "status",
            "status_label",
            "progress_stage",
            "severity",
            "priority",
            "service_impact",
            "impact_notes",
            "asset",
            "asset_id",
            "asset_tag",
            "asset_name",
            "site",
            "site_id",
            "site_code",
            "site_name",
            "reported_by",
            "reported_by_id",
            "reported_by_name",
            "reported_by_email",
            "occurred_at",
            "attachments_count",
            "created_at",
            "updated_at",
            "resolved_at",
            "closed_at",
        ]
        read_only_fields = fields

    def get_status_label(self, obj) -> str:
        return selectors.get_status_label(obj.status)

    def get_progress_stage(self, obj) -> str:
        return selectors.get_status_stage(obj.status)

    def get_attachments_count(self, obj) -> int:
        from apps.incidents.selectors import evidence_for
        return evidence_for(obj.organization, obj).count()


class PortalServiceRequestDetailSerializer(PortalServiceRequestSerializer):
    """Detailed serializer for client request tracking including safe history and evidence."""

    history = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()
    closure_confirmation = serializers.SerializerMethodField()
    is_eligible_for_confirmation = serializers.SerializerMethodField()
    work_order = serializers.SerializerMethodField()

    class Meta(PortalServiceRequestSerializer.Meta):
        fields = PortalServiceRequestSerializer.Meta.fields + [
            "history",
            "attachments",
            "closure_confirmation",
            "is_eligible_for_confirmation",
            "work_order",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        if ret.get("work_order") is None:
            ret.pop("work_order", None)
        return ret

    def get_history(self, obj):
        history_qs = selectors.client_request_history_for(obj)
        items = []
        for h in history_qs:
            # Client-safe projection: only reveal rejection or reopen reasons
            # Never leak internal staff triage notes or operations comments
            is_safe_reason = h.action in ("reject", "reopen")
            items.append({
                "id": str(h.id),
                "from_status": h.from_status,
                "to_status": h.to_status,
                "status_label": selectors.get_status_label(h.to_status),
                "stage": selectors.get_status_stage(h.to_status),
                "action": selectors.ACTION_LABELS.get(h.action, h.action),
                "reason": (h.reason or "") if is_safe_reason else "",
                "created_at": h.created_at.isoformat() if h.created_at else None,
            })
        return items

    def get_work_order(self, obj):
        wo = selectors.get_linked_work_order(obj)
        if wo is None:
            return None

        # Per Decision D-051: Technician display name is only revealed from DISPATCHED on
        tech_name = None
        if wo.status in ("DISPATCHED", "IN_PROGRESS", "COMPLETED", "CLOSED") and wo.assigned_to:
            tech_name = wo.assigned_to.user.full_name

        return {
            "order_number": wo.order_number,
            "status": wo.status,
            "status_label": wo.get_status_display() if hasattr(wo, "get_status_display") else wo.status,
            "planned_start": wo.planned_start.isoformat() if wo.planned_start else None,
            "planned_end": wo.planned_end.isoformat() if wo.planned_end else None,
            "dispatched_at": wo.dispatched_at.isoformat() if wo.dispatched_at else None,
            "technician_name": tech_name,
        }

    def get_attachments(self, obj):
        from apps.incidents.selectors import evidence_for
        ev_qs = evidence_for(obj.organization, obj)
        return [
            {
                "id": str(e.id),
                "file_name": e.original_name,
                "size_bytes": e.size,
                "mime_type": e.mime_type,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in ev_qs
        ]

    def get_closure_confirmation(self, obj):
        conf = getattr(obj, "closure_confirmation", None)
        if conf is None:
            from .models import PortalClosureConfirmation
            conf = PortalClosureConfirmation.objects.filter(request=obj).first()
        if conf:
            return {
                "id": str(conf.id),
                "confirmed_at": conf.confirmed_at.isoformat() if conf.confirmed_at else None,
                "confirmed_by": conf.confirmed_by.email,
                "rating": conf.rating,
                "feedback": conf.feedback,
            }
        return None

    def get_is_eligible_for_confirmation(self, obj) -> bool:
        request = self.context.get("request")
        membership = getattr(request, "membership", None)
        if not membership:
            return False
        eligible, _ = services.check_closure_confirmation_eligibility(membership, obj)
        return eligible


class PortalRequestListCreateView(TenantAPIMixin, APIView):
    """Client / Requester endpoint to list and submit service requests.

    GET: Returns a paginated list of service requests accessible to the authenticated client.
    POST: Submits a new client service request.
    """

    parser_classes = [parsers.JSONParser, parsers.FormParser, parsers.MultiPartParser]
    permission_map = {
        "get": "portal.request.view",
        "post": "portal.request.create",
    }

    @extend_schema(
        responses={200: PortalServiceRequestSerializer(many=True)},
        summary="List client service requests",
        description="Returns a paginated list of service requests reported by the authenticated client.",
    )
    def get(self, request):
        try:
            services.verify_portal_access(request.membership)
        except DomainError as exc:
            raise DRFPermissionDenied(str(exc)) from exc

        qs = selectors.client_requests_for(request.membership)

        status_param = request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param.strip().upper())

        ordering = request.query_params.get("ordering", "-created_at")
        if ordering in ("-created_at", "created_at", "-number", "number", "-updated_at", "updated_at", "status", "-status"):
            qs = qs.order_by(ordering)

        return paginate(request, qs, PortalServiceRequestSerializer)

    @extend_schema(
        request=PortalServiceRequestCreateSerializer,
        responses={201: PortalServiceRequestSerializer},
        summary="Create client service request",
        description="Submit a new client service request against an authorized asset.",
    )
    def post(self, request):
        try:
            services.verify_portal_request_create_access(request.membership)
        except DomainError as exc:
            raise DRFPermissionDenied(str(exc)) from exc

        serializer = PortalServiceRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        attachments = []
        if "file" in request.FILES:
            attachments.append(request.FILES["file"])
        for key in request.FILES:
            if key not in ("file",):
                attachments.append(request.FILES[key])

        try:
            sr = services.create_portal_service_request(
                request.membership,
                asset=data["asset"],
                title=data["title"],
                description=data.get("description", ""),
                kind=data.get("kind", ServiceRequest.Kind.SERVICE_REQUEST),
                severity=data.get("severity", ServiceRequest.Severity.MEDIUM),
                service_impact=data.get("service_impact", ServiceRequest.Impact.NONE),
                impact_notes=data.get("impact_notes", ""),
                site=data.get("site"),
                occurred_at=data.get("occurred_at"),
                attachments=attachments,
                actor=request.user,
                request=request,
            )
        except Conflict as exc:
            return Response(
                {"detail": exc.message, "code": getattr(exc, "code", "conflict")},
                status=status.HTTP_409_CONFLICT,
            )
        except ValidationFailed as exc:
            code = getattr(exc, "code", "") or ""
            field = "asset" if "asset" in code.lower() else ("site" if "site" in code.lower() else "detail")
            return Response(
                {field: [exc.message], "detail": exc.message, "code": code or "validation_failed"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except DomainError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(PortalServiceRequestSerializer(sr).data, status=status.HTTP_201_CREATED)


class PortalRequestDetailView(TenantAPIMixin, APIView):
    """Client / Requester endpoint to retrieve details and status tracking of a single service request."""

    permission_map = {
        "get": "portal.request.view",
    }

    @extend_schema(
        responses={200: PortalServiceRequestDetailSerializer},
        summary="Retrieve client service request details",
        description="Returns details, current status, and progression history for an authorized request.",
    )
    def get(self, request, pk):
        try:
            services.verify_portal_access(request.membership)
        except DomainError as exc:
            raise DRFPermissionDenied(str(exc)) from exc

        sr = selectors.get_client_request(request.membership, pk)
        if sr is None:
            return Response(
                {"detail": "Service request not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PortalServiceRequestDetailSerializer(sr)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PortalRequestConfirmSerializer(serializers.Serializer):
    rating = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        max_value=5,
        default=None,
    )
    feedback = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=1000,
        default="",
    )


class PortalClosureConfirmationSerializer(serializers.ModelSerializer):
    request_id = serializers.UUIDField(source="request.id", read_only=True)
    reference_number = serializers.CharField(source="request.number", read_only=True)
    status = serializers.CharField(source="request.status", read_only=True)
    confirmed_by = serializers.CharField(source="confirmed_by.email", read_only=True)
    work_order_number = serializers.SerializerMethodField()

    class Meta:
        model = PortalClosureConfirmation
        fields = [
            "id",
            "request_id",
            "reference_number",
            "status",
            "confirmed_at",
            "confirmed_by",
            "rating",
            "feedback",
            "work_order_number",
        ]
        read_only_fields = fields

    def get_work_order_number(self, obj) -> str | None:
        return obj.work_order.order_number if obj.work_order else None


class PortalRequestConfirmView(TenantAPIMixin, APIView):
    """Client / Requester endpoint to confirm satisfactory completion of a service request."""

    permission_map = {
        "post": "portal.request.confirm",
    }

    @extend_schema(
        request=PortalRequestConfirmSerializer,
        responses={200: PortalClosureConfirmationSerializer},
        summary="Confirm service request closure",
        description="Allows the authenticated requester to confirm that a resolved service request was completed satisfactorily.",
    )
    def post(self, request, pk):
        try:
            services.verify_portal_request_confirm_access(request.membership)
        except DomainError as exc:
            raise DRFPermissionDenied(str(exc)) from exc

        serializer = PortalRequestConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            confirmation = services.confirm_portal_request_closure(
                request.membership,
                pk,
                rating=data.get("rating"),
                feedback=data.get("feedback", ""),
                actor=request.user,
                request=request,
            )
        except NotFound as exc:
            return Response({"detail": exc.message}, status=status.HTTP_404_NOT_FOUND)
        except Conflict as exc:
            return Response(
                {"detail": exc.message, "code": getattr(exc, "code", "conflict")},
                status=status.HTTP_409_CONFLICT,
            )
        except DomainError as exc:
            return Response(
                {"detail": exc.message, "code": getattr(exc, "code", "domain_error")},
                status=exc.status_code,
            )

        return Response(PortalClosureConfirmationSerializer(confirmation).data, status=status.HTTP_200_OK)


class PortalRequestReopenSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=True,
        allow_blank=False,
        min_length=3,
        max_length=500,
        trim_whitespace=True,
    )


class PortalRequestReopenView(TenantAPIMixin, APIView):
    """Client / Requester endpoint to reopen a resolved service request for rework."""

    permission_map = {
        "post": "portal.request.confirm",
    }

    @extend_schema(
        request=PortalRequestReopenSerializer,
        responses={200: PortalServiceRequestDetailSerializer},
        summary="Reopen service request",
        description="Allows the authenticated requester to reopen a resolved service request with a mandatory reason if the issue remains unresolved.",
    )
    def post(self, request, pk):
        try:
            services.verify_portal_access(request.membership)
        except DomainError as exc:
            raise DRFPermissionDenied(str(exc)) from exc

        serializer = PortalRequestReopenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["reason"]

        try:
            sr = services.reopen_portal_service_request(
                request.membership,
                pk,
                reason=reason,
                actor=request.user,
                request=request,
            )
        except NotFound as exc:
            return Response({"detail": exc.message}, status=status.HTTP_404_NOT_FOUND)
        except Conflict as exc:
            return Response(
                {"detail": exc.message, "code": getattr(exc, "code", "conflict")},
                status=status.HTTP_409_CONFLICT,
            )
        except DomainError as exc:
            return Response(
                {"detail": exc.message, "code": getattr(exc, "code", "domain_error")},
                status=exc.status_code,
            )

        return Response(PortalServiceRequestDetailSerializer(sr).data, status=status.HTTP_200_OK)


# Backward compatibility alias
PortalRequestCreateView = PortalRequestListCreateView
