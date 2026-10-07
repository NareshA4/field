from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from apps.assets import selectors as asset_selectors
from apps.core.apiutils import ID_PARAM, paginate, query_param, require_permission
from apps.sites.selectors import scoped_get
from apps.tenancy.api import MEMBER_ONLY, TenantAPIMixin
from apps.workorders import selectors as wo_selectors

from . import selectors, services
from .models import ChecklistItem, ChecklistTemplate, Finding, Inspection, InspectionResponse


class ItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChecklistItem
        fields = ["id", "position", "prompt", "guidance", "item_type", "required", "options", "exception_options",
                  "min_value", "max_value", "unit", "evidence_required"]
        read_only_fields = fields


class TemplateSerializer(serializers.ModelSerializer):
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = ChecklistTemplate
        fields = ["id", "key", "version", "name", "description", "status", "work_type", "is_required", "activated_at",
                  "item_count", "created_at", "updated_at"]
        read_only_fields = fields

    def get_item_count(self, obj) -> int:
        return obj.items.count()


class TemplateDetailSerializer(TemplateSerializer):
    items = ItemSerializer(many=True, read_only=True)

    class Meta(TemplateSerializer.Meta):
        fields = TemplateSerializer.Meta.fields + ["items"]
        read_only_fields = fields


class TemplateWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    description = serializers.CharField(required=False, allow_blank=True)
    work_type = serializers.CharField(max_length=20, required=False, allow_blank=True)
    is_required = serializers.BooleanField(required=False)


class TemplatePatchSerializer(TemplateWriteSerializer):
    name = serializers.CharField(max_length=150, required=False)

    def validate(self, attrs):
        extra = set(getattr(self, "initial_data", {})) - set(self.fields)
        if extra:
            raise serializers.ValidationError({k: "This field cannot be edited." for k in sorted(extra)})
        return attrs


class ItemWriteSerializer(serializers.Serializer):
    prompt = serializers.CharField(max_length=300)
    item_type = serializers.ChoiceField(choices=ChecklistItem.ItemType.choices)
    guidance = serializers.CharField(required=False, allow_blank=True)
    required = serializers.BooleanField(required=False)
    options = serializers.ListField(child=serializers.CharField(max_length=100), required=False)
    exception_options = serializers.ListField(child=serializers.CharField(max_length=100), required=False)
    min_value = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    max_value = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    unit = serializers.CharField(max_length=20, required=False, allow_blank=True)
    evidence_required = serializers.BooleanField(required=False)


class ItemPatchSerializer(ItemWriteSerializer):
    prompt = serializers.CharField(max_length=300, required=False)
    item_type = serializers.ChoiceField(choices=ChecklistItem.ItemType.choices, required=False)


class ReorderSerializer(serializers.Serializer):
    order = serializers.ListField(child=serializers.UUIDField())


class ResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = InspectionResponse
        fields = ["id", "item", "value_text", "value_number", "value_bool", "is_exception", "answered_at"]
        read_only_fields = fields


class FindingSerializer(serializers.ModelSerializer):
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    site_code = serializers.CharField(source="site.code", read_only=True)
    work_order_number = serializers.CharField(source="work_order.number", read_only=True, default=None)

    class Meta:
        model = Finding
        fields = ["id", "inspection", "item", "work_order", "work_order_number", "asset", "asset_tag", "site",
                  "site_code", "description", "severity", "status", "resolved_at", "resolution_notes", "created_at"]
        read_only_fields = fields


class InspectionSerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(source="template.name", read_only=True)
    template_version = serializers.IntegerField(source="template.version", read_only=True)
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    site_code = serializers.CharField(source="site.code", read_only=True)
    work_order_number = serializers.CharField(source="work_order.number", read_only=True, default=None)

    class Meta:
        model = Inspection
        fields = ["id", "template", "template_name", "template_version", "work_order", "work_order_number", "asset",
                  "asset_tag", "site", "site_code", "status", "started_at", "completed_at", "summary"]
        read_only_fields = fields


class InspectionDetailSerializer(InspectionSerializer):
    items = serializers.SerializerMethodField()
    responses = serializers.SerializerMethodField()
    findings = serializers.SerializerMethodField()
    issues = serializers.SerializerMethodField()

    class Meta(InspectionSerializer.Meta):
        fields = InspectionSerializer.Meta.fields + ["items", "responses", "findings", "issues"]
        read_only_fields = fields

    @extend_schema_field(ItemSerializer(many=True))
    def get_items(self, obj):
        return ItemSerializer(selectors.items_for(obj.organization, obj.template), many=True).data

    @extend_schema_field(ResponseSerializer(many=True))
    def get_responses(self, obj):
        return ResponseSerializer(selectors.responses_for(obj.organization, obj).values(), many=True).data

    @extend_schema_field(FindingSerializer(many=True))
    def get_findings(self, obj):
        return FindingSerializer(obj.findings.select_related("asset", "site", "work_order"), many=True).data

    def get_issues(self, obj) -> list[str]:
        return services.completion_issues(obj) if obj.status == "IN_PROGRESS" else []


class StartSerializer(serializers.Serializer):
    template = serializers.UUIDField()
    work_order = serializers.UUIDField(required=False, allow_null=True)
    asset = serializers.UUIDField(required=False, allow_null=True)


class AnswersSerializer(serializers.Serializer):
    answers = serializers.DictField(child=serializers.JSONField(), help_text="{item id: value}")


class CompleteSerializer(serializers.Serializer):
    summary = serializers.CharField(required=False, allow_blank=True)


class FindingWriteSerializer(serializers.Serializer):
    description = serializers.CharField()
    severity = serializers.ChoiceField(choices=Finding.Severity.choices, required=False)
    item = serializers.UUIDField(required=False, allow_null=True)


class ResolveSerializer(serializers.Serializer):
    notes = serializers.CharField()


class EvidenceUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    description = serializers.CharField(max_length=200, required=False, allow_blank=True)
    item = serializers.UUIDField(required=False, help_text="Checklist item the file supports")
    finding = serializers.UUIDField(required=False, help_text="Finding the file supports")


class EvidenceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    original_name = serializers.CharField()
    mime_type = serializers.CharField()
    size = serializers.IntegerField()
    description = serializers.CharField()
    target = serializers.SerializerMethodField()
    target_id = serializers.CharField(source="object_id")
    created_at = serializers.DateTimeField()

    def get_target(self, obj) -> str:
        return obj.content_type.model


def _validated(serializer_cls, request, partial=False):
    ser = serializer_cls(data=request.data, partial=partial)
    ser.is_valid(raise_exception=True)
    return ser.validated_data


@extend_schema(parameters=[ID_PARAM])
class ChecklistTemplateViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {
        "list": "checklist.view", "retrieve": "checklist.view", "create": "checklist.manage",
        "partial_update": "checklist.manage", "items": "checklist.manage", "item_detail": "checklist.manage",
        "reorder": "checklist.manage", "activate": "checklist.manage", "deactivate": "checklist.manage",
        "new_version": "checklist.manage",
    }

    def _obj(self, request, pk) -> ChecklistTemplate:
        return selectors.get_template(request.organization, pk)

    @extend_schema(parameters=[query_param("status", "DRAFT / ACTIVE / INACTIVE"), query_param("q", "name")],
                   responses=TemplateSerializer(many=True))
    def list(self, request):
        qs = selectors.filter_templates(selectors.templates_for(request.organization), request.query_params)
        return paginate(request, qs, TemplateSerializer)

    @extend_schema(responses=TemplateDetailSerializer)
    def retrieve(self, request, pk=None):
        return Response(TemplateDetailSerializer(self._obj(request, pk)).data)

    @extend_schema(request=TemplateWriteSerializer, responses={201: TemplateSerializer})
    def create(self, request):
        d = _validated(TemplateWriteSerializer, request)
        t = services.create_template(request.organization, actor=request.user, request=request, **d)
        return Response(TemplateSerializer(t).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=TemplatePatchSerializer, responses=TemplateSerializer)
    def partial_update(self, request, pk=None):
        t = self._obj(request, pk)
        d = _validated(TemplatePatchSerializer, request, partial=True)
        t = services.update_template(t, actor=request.user, request=request, **d)
        return Response(TemplateSerializer(t).data)

    @extend_schema(request=ItemWriteSerializer, responses={201: ItemSerializer})
    @action(detail=True, methods=["post"])
    def items(self, request, pk=None):
        t = self._obj(request, pk)
        d = _validated(ItemWriteSerializer, request)
        item = services.add_item(t, actor=request.user, request=request, **d)
        return Response(ItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @extend_schema(parameters=[OpenApiParameter("item_id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
    @extend_schema(methods=["PATCH"], request=ItemPatchSerializer, responses=ItemSerializer)
    @extend_schema(methods=["DELETE"], responses={204: None})
    @action(detail=True, methods=["patch", "delete"], url_path=r"items/(?P<item_id>[^/.]+)")
    def item_detail(self, request, pk=None, item_id=None):
        t = self._obj(request, pk)
        item = scoped_get(selectors.items_for(request.organization, t), item_id, "Checklist item")
        if request.method == "DELETE":
            services.remove_item(item, actor=request.user, request=request)
            return Response(status=status.HTTP_204_NO_CONTENT)
        d = _validated(ItemPatchSerializer, request, partial=True)
        item = services.update_item(item, actor=request.user, request=request, **d)
        return Response(ItemSerializer(item).data)

    @extend_schema(request=ReorderSerializer, responses=ItemSerializer(many=True))
    @action(detail=True, methods=["post"])
    def reorder(self, request, pk=None):
        t = self._obj(request, pk)
        d = _validated(ReorderSerializer, request)
        items = services.reorder_items(t, d["order"], actor=request.user, request=request)
        return Response(ItemSerializer(items, many=True).data)

    @extend_schema(request=None, responses=TemplateSerializer)
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        t = services.activate_template(self._obj(request, pk), actor=request.user, request=request)
        return Response(TemplateSerializer(t).data)

    @extend_schema(request=None, responses=TemplateSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        t = services.deactivate_template(self._obj(request, pk), actor=request.user, request=request)
        return Response(TemplateSerializer(t).data)

    @extend_schema(request=None, responses={201: TemplateSerializer})
    @action(detail=True, methods=["post"], url_path="new-version")
    def new_version(self, request, pk=None):
        t = services.new_version(self._obj(request, pk), actor=request.user, request=request)
        return Response(TemplateSerializer(t).data, status=status.HTTP_201_CREATED)


@extend_schema(parameters=[ID_PARAM])
class InspectionViewSet(TenantAPIMixin, viewsets.ViewSet):
    # reads are self-scoping: the selector returns only inspections the caller may see (404 otherwise)
    permission_map = {
        "list": MEMBER_ONLY, "retrieve": MEMBER_ONLY, "create": "inspection.execute",
        "responses": "inspection.execute", "complete": "inspection.execute", "findings:get": MEMBER_ONLY,
        "findings:post": "inspection.execute", "evidence:get": MEMBER_ONLY, "evidence:post": "inspection.execute",
        "requirements": MEMBER_ONLY,
    }

    def _qs(self, request):
        return selectors.inspections_for(request.membership, request.organization)

    def _obj(self, request, pk, code=None) -> Inspection:
        insp = scoped_get(self._qs(request), pk, "Inspection")
        if code:
            require_permission(request.membership, code, insp.site_id)
        return insp

    def _detail(self, request, pk):
        return Response(InspectionDetailSerializer(self._obj(request, pk)).data)

    @extend_schema(parameters=[query_param("site", "site id", OpenApiTypes.UUID),
                               query_param("asset", "asset id", OpenApiTypes.UUID),
                               query_param("work_order", "work order id", OpenApiTypes.UUID),
                               query_param("template", "template id", OpenApiTypes.UUID),
                               query_param("status", "IN_PROGRESS / COMPLETED")],
                   responses=InspectionSerializer(many=True))
    def list(self, request):
        return paginate(request, selectors.filter_inspections(self._qs(request), request.query_params),
                        InspectionSerializer)

    @extend_schema(responses=InspectionDetailSerializer)
    def retrieve(self, request, pk=None):
        return self._detail(request, pk)

    @extend_schema(request=StartSerializer, responses={201: InspectionDetailSerializer})
    def create(self, request):
        d = _validated(StartSerializer, request)
        template = selectors.get_template(request.organization, d["template"])
        wo = asset = None
        if d.get("work_order"):
            wo = wo_selectors.get_work_order(request.membership, request.organization, d["work_order"])
            site_id = wo.site_id
        elif d.get("asset"):
            asset = asset_selectors.get_asset(request.membership, request.organization, d["asset"])
            site_id = asset.site_id
        else:
            raise serializers.ValidationError({"work_order": "Provide a work order or an asset."})
        require_permission(request.membership, "inspection.execute", site_id)
        insp = services.start_inspection(request.organization, template=template, membership=request.membership,
                                         actor=request.user, work_order=wo, asset=asset, request=request)
        return Response(InspectionDetailSerializer(self._obj(request, insp.pk)).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=AnswersSerializer, responses=InspectionDetailSerializer)
    @action(detail=True, methods=["post"])
    def responses(self, request, pk=None):
        insp = self._obj(request, pk, "inspection.execute")
        d = _validated(AnswersSerializer, request)
        services.save_responses(insp, d["answers"], membership=request.membership, actor=request.user,
                                request=request)
        return self._detail(request, pk)

    @extend_schema(request=CompleteSerializer, responses=InspectionDetailSerializer)
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        insp = self._obj(request, pk, "inspection.execute")
        d = _validated(CompleteSerializer, request)
        services.complete_inspection(insp, membership=request.membership, actor=request.user,
                                     summary=d.get("summary", ""), request=request)
        return self._detail(request, pk)

    @extend_schema(methods=["GET"], responses=FindingSerializer(many=True))
    @extend_schema(methods=["POST"], request=FindingWriteSerializer, responses={201: FindingSerializer})
    @action(detail=True, methods=["get", "post"])
    def findings(self, request, pk=None):
        insp = self._obj(request, pk)
        if request.method == "GET":
            return paginate(request, insp.findings.select_related("asset", "site", "work_order"),
                            FindingSerializer)
        require_permission(request.membership, "inspection.execute", insp.site_id)
        d = _validated(FindingWriteSerializer, request)
        item = scoped_get(selectors.items_for(request.organization, insp.template), d["item"],
                          "Checklist item") if d.get("item") else None
        f = services.add_finding(insp, description=d["description"], severity=d.get("severity", "MEDIUM"),
                                 item=item, membership=request.membership, actor=request.user, request=request)
        return Response(FindingSerializer(f).data, status=status.HTTP_201_CREATED)

    @extend_schema(methods=["GET"], responses=EvidenceSerializer(many=True))
    @extend_schema(methods=["POST"], request={"multipart/form-data": EvidenceUploadSerializer},
                   responses={201: EvidenceSerializer})
    @action(detail=True, methods=["get", "post"], parser_classes=[MultiPartParser, FormParser])
    def evidence(self, request, pk=None):
        insp = self._obj(request, pk)
        if request.method == "GET":
            qs = services.evidence_for_inspection(insp)
            return paginate(request, qs, EvidenceSerializer)
        require_permission(request.membership, "inspection.execute", insp.site_id)
        d = _validated(EvidenceUploadSerializer, request)
        if bool(d.get("item")) == bool(d.get("finding")):
            raise serializers.ValidationError({"item": "Give exactly one of item or finding."})
        if d.get("item"):
            item = scoped_get(selectors.items_for(request.organization, insp.template), d["item"], "Checklist item")
            att = services.add_response_evidence(insp, item, d["file"], description=d.get("description", ""),
                                                 membership=request.membership, actor=request.user,
                                                 request=request)
        else:
            finding = scoped_get(insp.findings.all(), d["finding"], "Finding")
            att = services.add_finding_evidence(finding, d["file"], description=d.get("description", ""),
                                                membership=request.membership, actor=request.user, request=request)
        return Response(EvidenceSerializer(att).data, status=status.HTTP_201_CREATED)

    @extend_schema(parameters=[query_param("work_order", "work order id", OpenApiTypes.UUID)],
                   responses={200: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["get"])
    def requirements(self, request):
        """Checklists a work order needs and their state (the same data M06 uses to block completion)."""
        wo = wo_selectors.get_work_order(request.membership, request.organization,
                                         request.query_params.get("work_order") or "")
        rows = selectors.requirements_for(request.organization, wo, services)
        return Response({"work_order": str(wo.pk), "blockers": services.checklist_blockers(wo), "checklists": [
            {"template": str(r["template"].pk), "name": r["template"].name, "version": r["template"].version,
             "required": r["required"], "state": r["state"],
             "inspection": str(r["inspection"].pk) if r["inspection"] else None} for r in rows]})


@extend_schema(parameters=[ID_PARAM])
class FindingViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": MEMBER_ONLY, "retrieve": MEMBER_ONLY, "resolve": "inspection.review"}

    def _obj(self, request, pk) -> Finding:
        return selectors.get_finding(request.membership, request.organization, pk)

    @extend_schema(parameters=[query_param("site", "site id", OpenApiTypes.UUID),
                               query_param("asset", "asset id", OpenApiTypes.UUID),
                               query_param("work_order", "work order id", OpenApiTypes.UUID),
                               query_param("inspection", "inspection id", OpenApiTypes.UUID),
                               query_param("status", "OPEN / RESOLVED"), query_param("severity", "severity")],
                   responses=FindingSerializer(many=True))
    def list(self, request):
        qs = selectors.filter_findings(selectors.findings_for(request.membership, request.organization),
                                       request.query_params)
        return paginate(request, qs, FindingSerializer)

    @extend_schema(responses=FindingSerializer)
    def retrieve(self, request, pk=None):
        return Response(FindingSerializer(self._obj(request, pk)).data)

    @extend_schema(request=ResolveSerializer, responses=FindingSerializer)
    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        f = self._obj(request, pk)
        require_permission(request.membership, "inspection.review", f.site_id)
        d = _validated(ResolveSerializer, request)
        f = services.resolve_finding(f, notes=d["notes"], actor=request.user, request=request)
        return Response(FindingSerializer(f).data)

