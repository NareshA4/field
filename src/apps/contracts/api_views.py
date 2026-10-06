"""M10 REST API (``/api/v1/contract-providers|coverage-agreements|coverage|coverage-checks``).

``TenantAPIMixin`` + ``permission_map`` (unmapped = denied) -> object lookup restricted to organization AND site
scope (404) -> permission for the agreement's site (403) -> service. Providers are organization-wide reference
data: any holder of the permission (at any site) may use them."""
import datetime

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.assets import selectors as asset_selectors
from apps.core.apiutils import ID_PARAM, paginate, query_param, require_permission
from apps.core.exceptions import ValidationFailed
from apps.sites import selectors as site_selectors
from apps.tenancy.api import TenantAPIMixin
from apps.workorders import selectors as wo_selectors

from . import selectors, services
from .models import CoverageAgreement, CoverageCheck, ServiceProvider

WORK_TYPE_CHOICES = [(w, w) for w in services.WORK_TYPES]

# --- serializers -----------------------------------------------------------------------------------------------------


class ProviderSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceProvider
        fields = ["id", "name", "contact_name", "email", "phone", "notes", "is_active", "created_at", "updated_at"]
        read_only_fields = fields


class ProviderWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    contact_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    email = serializers.CharField(max_length=254, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=40, required=False, allow_blank=True)
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True)


class ProviderPatchSerializer(ProviderWriteSerializer):
    name = serializers.CharField(max_length=150, required=False)


class AgreementSerializer(serializers.ModelSerializer):
    provider_name = serializers.CharField(source="provider.name", read_only=True)
    site_code = serializers.CharField(source="site.code", read_only=True)
    state = serializers.SerializerMethodField(help_text="ACTIVE | UPCOMING | EXPIRED | INACTIVE")
    assets = serializers.SerializerMethodField()
    excluded_work_types = serializers.SerializerMethodField()
    renewed_from_reference = serializers.CharField(source="renewed_from.reference", read_only=True, default=None)

    class Meta:
        model = CoverageAgreement
        fields = ["id", "reference", "title", "kind", "provider", "provider_name", "site", "site_code",
                  "start_date", "end_date", "terms", "exclusion_notes", "sla_terms", "sla_profile",
                  "renewal_alert_days", "is_active", "deactivation_reason", "state", "assets", "excluded_work_types", "renewed_from",
                  "renewed_from_reference", "created_at", "updated_at"]
        read_only_fields = fields

    def get_state(self, obj) -> str:
        return obj.state()

    def get_assets(self, obj) -> list[dict]:
        return [{"id": str(c.asset_id), "asset_tag": c.asset.asset_tag, "name": c.asset.name}
                for c in selectors.covered_assets_for(obj)]

    def get_excluded_work_types(self, obj) -> list[str]:
        return sorted(e.work_type for e in obj.exclusions.all())


class AgreementWriteSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=CoverageAgreement.Kind.choices)
    reference = serializers.CharField(max_length=80)
    title = serializers.CharField(max_length=200)
    provider = serializers.UUIDField()
    site = serializers.UUIDField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    assets = serializers.ListField(child=serializers.UUIDField(), min_length=1)
    terms = serializers.CharField(required=False, allow_blank=True)
    exclusion_notes = serializers.CharField(required=False, allow_blank=True)
    sla_terms = serializers.CharField(max_length=300, required=False, allow_blank=True)
    sla_profile = serializers.UUIDField(required=False, allow_null=True)
    renewal_alert_days = serializers.IntegerField(required=False, min_value=0, max_value=365)
    excluded_work_types = serializers.ListField(child=serializers.ChoiceField(choices=WORK_TYPE_CHOICES),
                                                required=False)


class AgreementPatchSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=80, required=False)
    title = serializers.CharField(max_length=200, required=False)
    provider = serializers.UUIDField(required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    terms = serializers.CharField(required=False, allow_blank=True)
    exclusion_notes = serializers.CharField(required=False, allow_blank=True)
    sla_terms = serializers.CharField(max_length=300, required=False, allow_blank=True)
    sla_profile = serializers.UUIDField(required=False, allow_null=True)
    renewal_alert_days = serializers.IntegerField(required=False, min_value=0, max_value=365)
    excluded_work_types = serializers.ListField(child=serializers.ChoiceField(choices=WORK_TYPE_CHOICES),
                                                required=False)


class AgreementReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300)


class RenewSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=80)
    new_end_date = serializers.DateField()


class AssetRefSerializer(serializers.Serializer):
    asset = serializers.UUIDField()


class CheckSerializer(serializers.ModelSerializer):
    work_order_number = serializers.CharField(source="work_order.number", read_only=True)
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    agreement_reference = serializers.CharField(source="agreement.reference", read_only=True, default=None)
    provider_name = serializers.CharField(source="agreement.provider.name", read_only=True, default=None)

    class Meta:
        model = CoverageCheck
        fields = ["id", "work_order", "work_order_number", "asset", "asset_tag", "agreement",
                  "agreement_reference", "provider_name", "work_type", "reference_date", "eligible", "reason",
                  "created_at"]
        read_only_fields = fields


class CheckCreateSerializer(serializers.Serializer):
    work_order = serializers.UUIDField()


class EntrySerializer(serializers.Serializer):
    agreement = serializers.UUIDField(source="agreement.pk")
    reference = serializers.CharField(source="agreement.reference")
    kind = serializers.CharField(source="agreement.kind")
    provider = serializers.CharField(source="agreement.provider.name")
    start_date = serializers.DateField(source="agreement.start_date")
    end_date = serializers.DateField(source="agreement.end_date")
    status = serializers.CharField()
    eligible = serializers.BooleanField()
    reason = serializers.CharField()


class CoverageResultSerializer(serializers.Serializer):
    asset = serializers.UUIDField(source="asset.pk")
    asset_tag = serializers.CharField(source="asset.asset_tag")
    date = serializers.DateField(source="on")
    work_type = serializers.CharField()
    covered = serializers.BooleanField()
    eligible = serializers.BooleanField()
    reason = serializers.CharField()
    entries = EntrySerializer(many=True)


def _sla_profile(org, pk):
    """Resolves an SLA profile id inside the caller's organization (another tenant's id answers 404)."""
    if pk is None:
        return None
    from apps.core.exceptions import NotFound
    from apps.sla.models import SLAProfile

    profile = SLAProfile.objects.for_organization(org).filter(pk=pk).first()
    if profile is None:
        raise NotFound("SLA profile not found.")
    return profile


def _validated(serializer_cls, request, partial=False):
    ser = serializer_cls(data=request.data, partial=partial)
    ser.is_valid(raise_exception=True)
    return dict(ser.validated_data)


# --- viewsets --------------------------------------------------------------------------------------------------------


@extend_schema(parameters=[ID_PARAM])
class ProviderViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "contract.view", "retrieve": "contract.view", "create": "contract.create",
                      "partial_update": "contract.update", "enable": "contract.update",
                      "disable": "contract.update"}

    @extend_schema(parameters=[query_param("q", "name"), query_param("active", "1 / 0")],
                   responses=ProviderSerializer(many=True))
    def list(self, request):
        qs = selectors.providers_for(request.organization)
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(name__icontains=q)
        active = (request.query_params.get("active") or "").strip()
        if active in ("1", "0"):
            qs = qs.filter(is_active=active == "1")
        return paginate(request, qs.order_by("name"), ProviderSerializer)

    @extend_schema(responses=ProviderSerializer)
    def retrieve(self, request, pk=None):
        return Response(ProviderSerializer(selectors.get_provider(request.organization, pk)).data)

    @extend_schema(request=ProviderWriteSerializer, responses={201: ProviderSerializer})
    def create(self, request):
        d = _validated(ProviderWriteSerializer, request)
        p = services.create_provider(request.organization, actor=request.user, request=request, **d)
        return Response(ProviderSerializer(p).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ProviderPatchSerializer, responses=ProviderSerializer)
    def partial_update(self, request, pk=None):
        p = selectors.get_provider(request.organization, pk)
        d = _validated(ProviderPatchSerializer, request, partial=True)
        return Response(ProviderSerializer(services.update_provider(p, actor=request.user, request=request,
                                                                    **d)).data)

    @extend_schema(request=None, responses=ProviderSerializer)
    @action(detail=True, methods=["post"])
    def enable(self, request, pk=None):
        p = selectors.get_provider(request.organization, pk)
        return Response(ProviderSerializer(services.set_provider_active(p, True, actor=request.user,
                                                                        request=request)).data)

    @extend_schema(request=None, responses=ProviderSerializer)
    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        p = selectors.get_provider(request.organization, pk)
        return Response(ProviderSerializer(services.set_provider_active(p, False, actor=request.user,
                                                                        request=request)).data)


@extend_schema(parameters=[ID_PARAM])
class AgreementViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {
        "list": "contract.view", "retrieve": "contract.view", "create": "contract.create",
        "partial_update": "contract.update", "deactivate": "contract.update", "reactivate": "contract.update",
        "renew": "contract.update", "add_asset": "contract.update", "remove_asset": "contract.update",
        "expiring": "contract.view",
    }

    def _ag(self, request, pk, code=None) -> CoverageAgreement:
        ag = selectors.get_agreement(request.membership, request.organization, pk)
        if code:
            require_permission(request.membership, code, ag.site_id)
        return ag

    def _asset(self, request, pk, code):
        asset = asset_selectors.get_asset(request.membership, request.organization, pk)
        require_permission(request.membership, code, asset.site_id)
        return asset

    @extend_schema(parameters=[query_param("q", "reference / title / provider / asset tag"),
                               query_param("kind", "WARRANTY | AMC | SERVICE_CONTRACT"),
                               query_param("state", "ACTIVE | UPCOMING | EXPIRED | INACTIVE"),
                               query_param("site", "site id", OpenApiTypes.UUID),
                               query_param("provider", "provider id", OpenApiTypes.UUID),
                               query_param("asset", "asset id", OpenApiTypes.UUID)],
                   responses=AgreementSerializer(many=True))
    def list(self, request):
        qs = selectors.filter_agreements(selectors.agreements_for(request.membership, request.organization),
                                         request.query_params)
        return paginate(request, qs, AgreementSerializer)

    @extend_schema(responses=AgreementSerializer)
    def retrieve(self, request, pk=None):
        return Response(AgreementSerializer(self._ag(request, pk)).data)

    @extend_schema(request=AgreementWriteSerializer, responses={201: AgreementSerializer})
    def create(self, request):
        d = _validated(AgreementWriteSerializer, request)
        org = request.organization
        site = site_selectors.get_site_for(request.membership, org, d.pop("site"), "contract.create")
        provider = selectors.get_provider(org, d.pop("provider"))
        d["sla_profile"] = _sla_profile(org, d.pop("sla_profile", None))
        assets = [self._asset(request, a, "contract.create") for a in d.pop("assets")]
        ag = services.create_agreement(org, site=site, provider=provider, assets=assets, actor=request.user,
                                       request=request, **d)
        return Response(AgreementSerializer(ag).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=AgreementPatchSerializer, responses=AgreementSerializer)
    def partial_update(self, request, pk=None):
        ag = self._ag(request, pk, "contract.update")
        d = _validated(AgreementPatchSerializer, request, partial=True)
        if "provider" in d:
            d["provider"] = selectors.get_provider(request.organization, d["provider"])
        if "sla_profile" in d:
            d["sla_profile"] = _sla_profile(request.organization, d["sla_profile"])
        services.update_agreement(ag, actor=request.user, request=request, **d)
        return Response(AgreementSerializer(self._ag(request, pk)).data)

    @extend_schema(request=AgreementReasonSerializer, responses=AgreementSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        ag = self._ag(request, pk, "contract.update")
        d = _validated(AgreementReasonSerializer, request)
        return Response(AgreementSerializer(services.set_agreement_active(
            ag, False, reason=d["reason"], actor=request.user, request=request)).data)

    @extend_schema(request=None, responses=AgreementSerializer)
    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        ag = self._ag(request, pk, "contract.update")
        return Response(AgreementSerializer(services.set_agreement_active(
            ag, True, actor=request.user, request=request)).data)

    @extend_schema(request=RenewSerializer, responses={201: AgreementSerializer})
    @action(detail=True, methods=["post"])
    def renew(self, request, pk=None):
        ag = self._ag(request, pk, "contract.update")
        d = _validated(RenewSerializer, request)
        new = services.renew_agreement(ag, new_end_date=d["new_end_date"], reference=d["reference"],
                                       actor=request.user, request=request)
        return Response(AgreementSerializer(new).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=AssetRefSerializer, responses=AgreementSerializer)
    @action(detail=True, methods=["post"], url_path="add-asset")
    def add_asset(self, request, pk=None):
        ag = self._ag(request, pk, "contract.update")
        asset = self._asset(request, _validated(AssetRefSerializer, request)["asset"], "contract.update")
        services.add_asset(ag, asset, actor=request.user, request=request)
        return Response(AgreementSerializer(self._ag(request, pk)).data)

    @extend_schema(request=AssetRefSerializer, responses=AgreementSerializer)
    @action(detail=True, methods=["post"], url_path="remove-asset")
    def remove_asset(self, request, pk=None):
        ag = self._ag(request, pk, "contract.update")
        asset = self._asset(request, _validated(AssetRefSerializer, request)["asset"], "contract.update")
        services.remove_asset(ag, asset, actor=request.user, request=request)
        return Response(AgreementSerializer(self._ag(request, pk)).data)

    @extend_schema(parameters=[query_param("days", "look-ahead window in days (default 60, max 365)",
                                           OpenApiTypes.INT)], responses=AgreementSerializer(many=True))
    @action(detail=False, methods=["get"])
    def expiring(self, request):
        try:
            days = max(1, min(int(request.query_params.get("days", 60)), 365))
        except ValueError as exc:
            raise ValidationFailed("days must be a whole number.", code="invalid_number") from exc
        return paginate(request, selectors.expiring(request.membership, request.organization, days),
                        AgreementSerializer)


@extend_schema(parameters=[query_param("asset", "asset id (required)", OpenApiTypes.UUID),
                           query_param("work_type", "CORRECTIVE | PREVENTIVE | INSPECTION | INSTALLATION | OTHER"),
                           query_param("date", "reference date YYYY-MM-DD (default today)", OpenApiTypes.DATE)],
               responses=CoverageResultSerializer)
class CoverageViewSet(TenantAPIMixin, viewsets.ViewSet):
    """Live coverage / eligibility answer for an asset (computed by the backend; nothing is stored)."""

    permission_map = {"list": "contract.view"}

    def list(self, request):
        asset_id = request.query_params.get("asset")
        if not asset_id:
            raise ValidationFailed("The asset parameter is required.", code="asset_required")
        asset = asset_selectors.get_asset(request.membership, request.organization, asset_id)
        require_permission(request.membership, "contract.view", asset.site_id)
        work_type = (request.query_params.get("work_type") or "").strip().upper()
        if work_type and work_type not in services.WORK_TYPES:
            raise ValidationFailed("Unknown work type.", code="invalid_work_type")
        raw = (request.query_params.get("date") or "").strip()
        on = services._date(raw, "Date") if raw else datetime.date.today()
        return Response(CoverageResultSerializer(services.evaluate(request.organization, asset, on, work_type)).data)


@extend_schema(parameters=[ID_PARAM])
class CoverageCheckViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "contract.view", "retrieve": "contract.view", "create": "contract.check"}

    @extend_schema(parameters=[query_param("work_order", "work order id", OpenApiTypes.UUID)],
                   responses=CheckSerializer(many=True))
    def list(self, request):
        qs = selectors.checks_for(request.membership, request.organization)
        wo = (request.query_params.get("work_order") or "").strip()
        if wo:
            parsed = selectors._uuid_or_none(wo)
            qs = qs.filter(work_order_id=parsed) if parsed else qs.none()
        return paginate(request, qs, CheckSerializer)

    @extend_schema(responses=CheckSerializer)
    def retrieve(self, request, pk=None):
        from apps.sites.selectors import scoped_get

        return Response(CheckSerializer(scoped_get(selectors.checks_for(request.membership, request.organization),
                                                   pk, "Coverage check")).data)

    @extend_schema(request=CheckCreateSerializer, responses={201: CheckSerializer})
    def create(self, request):
        d = _validated(CheckCreateSerializer, request)
        wo = wo_selectors.get_work_order(request.membership, request.organization, d["work_order"])
        require_permission(request.membership, "contract.check", wo.site_id)
        check = services.record_check(wo, actor=request.user, request=request)
        return Response(CheckSerializer(check).data, status=status.HTTP_201_CREATED)
