from django.urls import reverse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from apps.core.apiutils import ID_PARAM, paginate, query_param, require_permission
from apps.core.exceptions import DomainError, NotFound, ValidationFailed
from apps.sites import selectors as site_selectors
from apps.sites.models import Zone
from apps.tenancy.api import TenantAPIMixin
from apps.tenancy.models import Membership

from . import hierarchy, selectors, services
from .models import Asset, AssetCategory, AssetComponent, AssetDocument, AssetMeter, AssetMeterReading
from .workflow import ASSET_STATUS

# --- serializers -----------------------------------------------------------------------------------------


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetCategory
        fields = ["id", "name", "description", "is_active", "attribute_definitions"]
        read_only_fields = fields


class CategoryWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    description = serializers.CharField(max_length=300, required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)
    attribute_definitions = serializers.ListField(
        child=serializers.DictField(), required=False,
        help_text="[{label, type: text|number|date|choice, required, choices}] - see assets.attributes")


class AssetSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    site_code = serializers.CharField(source="site.code", read_only=True)
    zone_name = serializers.CharField(source="zone.name", read_only=True, default=None)
    owner_name = serializers.SerializerMethodField()
    parent = serializers.SerializerMethodField()
    available_actions = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = ["id", "asset_tag", "name", "description", "category", "category_name", "manufacturer", "model",
                  "serial_number", "purchase_date", "commission_date", "site", "site_code", "zone", "zone_name",
                  "owner", "owner_name", "warranty_ref", "attributes", "status", "parent", "available_actions", "created_at",
                  "updated_at"]
        read_only_fields = fields

    def get_owner_name(self, obj) -> str | None:
        return obj.owner.user.display_name if obj.owner_id else None

    def get_parent(self, obj) -> dict | None:
        link = getattr(obj, "parent_link", None)
        if link is None:
            return None
        return {"id": str(link.parent_id), "asset_tag": link.parent.asset_tag,
                "relationship_type": link.relationship_type, "link_id": str(link.pk)}

    def get_available_actions(self, obj) -> list[str]:
        return [t.action for t in ASSET_STATUS.available(obj.status)]


class AssetWriteSerializer(serializers.Serializer):
    asset_tag = serializers.CharField(max_length=40)
    name = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    category = serializers.UUIDField()
    manufacturer = serializers.CharField(max_length=100, required=False, allow_blank=True)
    model = serializers.CharField(max_length=100, required=False, allow_blank=True)
    serial_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    purchase_date = serializers.DateField(required=False, allow_null=True)
    commission_date = serializers.DateField(required=False, allow_null=True)
    site = serializers.UUIDField()
    zone = serializers.UUIDField(required=False, allow_null=True)
    owner = serializers.UUIDField(required=False, allow_null=True, help_text="Membership id of the owner")
    warranty_ref = serializers.CharField(max_length=200, required=False, allow_blank=True)
    attributes = serializers.DictField(child=serializers.CharField(allow_blank=True), required=False,
                                       help_text="Values of the category's custom attributes (key -> text)")
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True,
                                   help_text="Reason for a location move (PATCH)")

    def validate(self, attrs):
        if "status" in getattr(self, "initial_data", {}):
            raise serializers.ValidationError({"status": "Status cannot be edited; use POST /assets/{id}/transition/."})
        return attrs


class TransitionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=[t.action for t in ASSET_STATUS.transitions])
    reason = serializers.CharField(max_length=500)


class StatusHistorySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    from_status = serializers.CharField()
    to_status = serializers.CharField()
    action = serializers.CharField()
    reason = serializers.CharField()
    source = serializers.CharField()
    changed_by = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    def get_changed_by(self, obj) -> str | None:
        return obj.changed_by.email if obj.changed_by_id else None


class LocationHistorySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    from_site = serializers.CharField(source="from_site.code", default=None)
    from_zone = serializers.CharField(source="from_zone.name", default=None)
    to_site = serializers.CharField(source="to_site.code")
    to_zone = serializers.CharField(source="to_zone.name", default=None)
    reason = serializers.CharField()
    moved_by = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    def get_moved_by(self, obj) -> str | None:
        return obj.moved_by.email if obj.moved_by_id else None


class DocumentSerializer(serializers.ModelSerializer):
    original_name = serializers.CharField(source="attachment.original_name", read_only=True, default="")
    size = serializers.IntegerField(source="attachment.size", read_only=True, default=0)
    mime_type = serializers.CharField(source="attachment.mime_type", read_only=True, default="")
    uploaded_by = serializers.EmailField(source="uploaded_by.email", read_only=True)
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = AssetDocument
        fields = ["id", "asset", "title", "doc_type", "original_name", "size", "mime_type", "uploaded_by",
                  "download_url", "is_active", "created_at"]
        read_only_fields = fields

    def get_download_url(self, obj) -> str | None:
        return reverse("files:download", args=[obj.attachment_id]) if obj.attachment_id else None


class DocumentRemoveSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300)


class DocumentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    title = serializers.CharField(max_length=150, required=False, allow_blank=True)
    doc_type = serializers.ChoiceField(choices=AssetDocument.DocType.choices, required=False)


class MeterSerializer(serializers.ModelSerializer):
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    last_value = serializers.DecimalField(max_digits=16, decimal_places=3, read_only=True, required=False,
                                          allow_null=True)
    last_read_at = serializers.DateTimeField(read_only=True, required=False, allow_null=True)

    class Meta:
        model = AssetMeter
        fields = ["id", "asset", "asset_tag", "name", "unit", "is_active", "last_value", "last_read_at"]
        read_only_fields = fields


class MeterWriteSerializer(serializers.Serializer):
    asset = serializers.UUIDField()
    name = serializers.CharField(max_length=80)
    unit = serializers.CharField(max_length=20)


class ReadingSerializer(serializers.ModelSerializer):
    recorded_by = serializers.EmailField(source="recorded_by.email", read_only=True, default=None)

    class Meta:
        model = AssetMeterReading
        fields = ["id", "meter", "value", "read_at", "recorded_by", "source", "notes"]
        read_only_fields = fields


class ReadingWriteSerializer(serializers.Serializer):
    value = serializers.DecimalField(max_digits=16, decimal_places=3, min_value=0)
    read_at = serializers.DateTimeField(required=False)
    notes = serializers.CharField(max_length=300, required=False, allow_blank=True)


class ComponentSerializer(serializers.ModelSerializer):
    parent_tag = serializers.CharField(source="parent.asset_tag", read_only=True)
    child_tag = serializers.CharField(source="child.asset_tag", read_only=True)
    child_name = serializers.CharField(source="child.name", read_only=True)

    class Meta:
        model = AssetComponent
        fields = ["id", "parent", "parent_tag", "child", "child_tag", "child_name", "relationship_type",
                  "quantity", "part_number", "notes"]
        read_only_fields = fields


class ComponentCreateSerializer(serializers.Serializer):
    child = serializers.UUIDField()
    relationship_type = serializers.ChoiceField(choices=AssetComponent.Relationship.choices, required=False)
    quantity = serializers.IntegerField(min_value=1, required=False)
    part_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    notes = serializers.CharField(max_length=300, required=False, allow_blank=True)


class ComponentUpdateSerializer(serializers.Serializer):
    relationship_type = serializers.ChoiceField(choices=AssetComponent.Relationship.choices, required=False)
    quantity = serializers.IntegerField(min_value=1, required=False)
    part_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    notes = serializers.CharField(max_length=300, required=False, allow_blank=True)


class MoveSerializer(serializers.Serializer):
    parent = serializers.UUIDField()


class ValidateLinkSerializer(serializers.Serializer):
    child = serializers.UUIDField()


class TreeNodeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    asset_tag = serializers.CharField()
    name = serializers.CharField()
    status = serializers.CharField()
    category = serializers.CharField()
    relationship_type = serializers.CharField(allow_null=True)
    quantity = serializers.IntegerField(allow_null=True)
    link_id = serializers.UUIDField(allow_null=True)
    depth = serializers.IntegerField()
    children = serializers.ListField(child=serializers.DictField())


def tree_node(n: dict) -> dict:
    a, link = n["asset"], n["link"]
    return {"id": str(a.pk), "asset_tag": a.asset_tag, "name": a.name, "status": a.status,
            "category": a.category.name, "relationship_type": link.relationship_type if link else None,
            "quantity": link.quantity if link else None, "link_id": str(link.pk) if link else None,
            "depth": n["depth"], "children": [tree_node(c) for c in n["children"]]}


def _validated(serializer_cls, request, partial=False):
    ser = serializer_cls(data=request.data, partial=partial)
    ser.is_valid(raise_exception=True)
    return ser.validated_data


def _org_obj(model, org, pk, what):
    if pk is None:
        return None
    try:
        return model.objects.for_organization(org).get(pk=pk)
    except model.DoesNotExist as exc:
        raise NotFound(f"{what} not found.") from exc


def resolve_refs(request, d: dict, *, code: str) -> dict:
    """Turns ids in a write payload into organization-checked objects. The target site must be one the caller
    holds ``code`` for (404 outside tenant/scope, 403 when visible but not permitted)."""
    org = request.organization
    out = {}
    if "site" in d:
        out["site"] = site_selectors.get_site_for(request.membership, org, d["site"], code)
    if "zone" in d:
        out["zone"] = _org_obj(Zone, org, d["zone"], "Location")
    if "category" in d:
        out["category"] = _org_obj(AssetCategory, org, d["category"], "Category")
    if "owner" in d:
        out["owner"] = _org_obj(Membership, org, d["owner"], "Owner")
    return out


# --- viewsets ----------------------------------------------------------------------------------------------


@extend_schema(parameters=[ID_PARAM])
class AssetCategoryViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "asset.view", "retrieve": "asset.view", "create": "asset.category.manage",
                      "partial_update": "asset.category.manage"}

    def _cat(self, request, pk):
        return site_selectors.scoped_get(selectors.categories_for(request.organization), pk, "Category")

    @extend_schema(responses=CategorySerializer(many=True))
    def list(self, request):
        return paginate(request, selectors.categories_for(request.organization), CategorySerializer)

    @extend_schema(responses=CategorySerializer)
    def retrieve(self, request, pk=None):
        return Response(CategorySerializer(self._cat(request, pk)).data)

    @extend_schema(request=CategoryWriteSerializer, responses={201: CategorySerializer})
    def create(self, request):
        d = _validated(CategoryWriteSerializer, request)
        cat = services.create_category(request.organization, name=d["name"], description=d.get("description", ""),
                                       attribute_definitions=d.get("attribute_definitions"), actor=request.user,
                                       request=request)
        return Response(CategorySerializer(cat).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=CategoryWriteSerializer, responses=CategorySerializer)
    def partial_update(self, request, pk=None):
        cat = self._cat(request, pk)
        d = _validated(CategoryWriteSerializer, request, partial=True)
        cat = services.update_category(cat, actor=request.user, request=request, name=d.get("name"),
                                       description=d.get("description"), is_active=d.get("is_active"),
                                       attribute_definitions=d.get("attribute_definitions", services.UNSET))
        return Response(CategorySerializer(cat).data)


@extend_schema(parameters=[ID_PARAM])
class AssetViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {
        "list": "asset.view", "retrieve": "asset.view", "create": "asset.create", "partial_update": "asset.update",
        "transition": "asset.change_status", "history": "asset.history.view",
        "location_history": "asset.history.view", "changes": "asset.history.view", "documents:get": "asset.view",
        "documents:post": "asset.document.manage", "remove_document": "asset.document.manage", "tree": "asset.view", "validate": "asset.view",
        "components:get": "asset.view", "components:post": "asset.hierarchy.manage",
        "validate_link": "asset.hierarchy.manage",
    }

    def _qs(self, request):
        return selectors.assets_for(request.membership, request.organization).select_related("parent_link__parent")

    def _asset(self, request, pk, code=None) -> Asset:
        asset = site_selectors.scoped_get(self._qs(request), pk, "Asset")  # 404 outside tenant/site scope
        if code:
            require_permission(request.membership, code, asset.site_id)
        return asset

    @extend_schema(
        parameters=[query_param("site", "site id", OpenApiTypes.UUID), query_param("zone", "location id",
                    OpenApiTypes.UUID), query_param("category", "category id", OpenApiTypes.UUID),
                    query_param("status", "asset status"), query_param("owner", "owner membership id",
                    OpenApiTypes.UUID), query_param("q", "tag / name / serial / model / manufacturer"),
                    query_param("ordering", "asset_tag, name, status, created_at (prefix - to reverse)")],
        responses=AssetSerializer(many=True))
    def list(self, request):
        qs = selectors.filter_assets(self._qs(request), request.query_params)
        return paginate(request, qs, AssetSerializer)

    @extend_schema(responses=AssetSerializer)
    def retrieve(self, request, pk=None):
        return Response(AssetSerializer(self._asset(request, pk)).data)

    @extend_schema(request=AssetWriteSerializer, responses={201: AssetSerializer})
    def create(self, request):
        d = _validated(AssetWriteSerializer, request)
        d.pop("reason", None)
        refs = resolve_refs(request, d, code="asset.create")
        for k in ("site", "zone", "category", "owner"):
            d.pop(k, None)
        asset = services.create_asset(request.organization, actor=request.user, request=request, **refs, **d)
        return Response(AssetSerializer(self._asset(request, asset.pk)).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=AssetWriteSerializer, responses=AssetSerializer)
    def partial_update(self, request, pk=None):
        asset = self._asset(request, pk, "asset.update")
        d = _validated(AssetWriteSerializer, request, partial=True)
        reason = d.pop("reason", "")
        refs = resolve_refs(request, d, code="asset.update")
        for k in ("site", "zone", "category", "owner"):
            d.pop(k, None)
        services.update_asset(asset, actor=request.user, request=request, reason=reason, **refs, **d)
        return Response(AssetSerializer(self._asset(request, pk)).data)

    @extend_schema(request=TransitionSerializer, responses=AssetSerializer)
    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        """The only way to change asset status: validated transition + history row + audit."""
        asset = self._asset(request, pk, "asset.change_status")
        d = _validated(TransitionSerializer, request)
        services.change_status(asset, action=d["action"], reason=d["reason"], actor=request.user, request=request)
        return Response(AssetSerializer(self._asset(request, pk)).data)

    @extend_schema(responses=StatusHistorySerializer(many=True))
    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        asset = self._asset(request, pk, "asset.history.view")
        return paginate(request, selectors.status_history_for(request.membership, request.organization, asset),
                        StatusHistorySerializer)

    @extend_schema(responses=LocationHistorySerializer(many=True))
    @action(detail=True, methods=["get"], url_path="location-history")
    def location_history(self, request, pk=None):
        asset = self._asset(request, pk, "asset.history.view")
        return paginate(request, selectors.location_history_for(request.organization, asset),
                        LocationHistorySerializer)

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["get"])
    def changes(self, request, pk=None):
        """Field-level change log (who, when, before -> after) from the audit trail."""
        asset = self._asset(request, pk, "asset.history.view")
        data = selectors.change_log(request.organization, asset)
        for row in data:
            row["occurred_at"] = row["occurred_at"].isoformat()
        return Response({"count": len(data), "results": data})

    @extend_schema(methods=["GET"], responses=DocumentSerializer(many=True))
    @extend_schema(methods=["POST"], request={"multipart/form-data": DocumentUploadSerializer},
                   responses={201: DocumentSerializer})
    @action(detail=True, methods=["get", "post"], parser_classes=[MultiPartParser, FormParser])
    def documents(self, request, pk=None):
        asset = self._asset(request, pk)
        if request.method == "GET":
            return paginate(request, selectors.documents_for(request.organization, asset), DocumentSerializer)
        d = _validated(DocumentUploadSerializer, request)
        doc = services.add_document(asset, d["file"], title=d.get("title", ""), doc_type=d.get("doc_type", "OTHER"),
                                    actor=request.user, request=request)
        return Response(DocumentSerializer(doc).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=DocumentRemoveSerializer, responses=DocumentSerializer,
                   parameters=[OpenApiParameter("doc_id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
    @action(detail=True, methods=["post"], url_path=r"documents/(?P<doc_id>[^/.]+)/remove")
    def remove_document(self, request, pk=None, doc_id=None):
        asset = self._asset(request, pk)
        doc = selectors.documents_for(request.organization, asset, include_removed=True).filter(
            pk=selectors._uuid_or_none(doc_id)).first()
        if doc is None:
            raise NotFound("Document not found.")
        d = _validated(DocumentRemoveSerializer, request)
        services.remove_document(doc, reason=d["reason"], actor=request.user, request=request)
        doc.refresh_from_db()
        return Response(DocumentSerializer(doc).data)

    @extend_schema(responses=TreeNodeSerializer)
    @action(detail=True, methods=["get"])
    def tree(self, request, pk=None):
        """This asset with all of its descendants, plus the chain of ancestors (root first)."""
        asset = self._asset(request, pk)
        path = [{"id": str(a.pk), "asset_tag": a.asset_tag} for a in reversed(hierarchy.ancestors(asset))]
        return Response({"path": path, "tree": tree_node(hierarchy.build_tree(asset))})

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["get"])
    def validate(self, request, pk=None):
        """Integrity report (organization, site, cycle, depth) for the subtree below this asset."""
        return Response(hierarchy.validate_tree(self._asset(request, pk)))

    @extend_schema(request=ValidateLinkSerializer, responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="validate-link")
    def validate_link(self, request, pk=None):
        """Dry run: could <child> be added below this asset? Never changes data."""
        parent = self._asset(request, pk, "asset.hierarchy.manage")
        d = _validated(ValidateLinkSerializer, request)
        child = site_selectors.scoped_get(self._qs(request), d["child"], "Asset")
        try:
            hierarchy.check_link(parent, child)
        except DomainError as exc:
            return Response({"valid": False, "code": exc.code, "message": exc.message})
        return Response({"valid": True})

    @extend_schema(methods=["GET"], responses=ComponentSerializer(many=True))
    @extend_schema(methods=["POST"], request=ComponentCreateSerializer, responses={201: ComponentSerializer})
    @action(detail=True, methods=["get", "post"])
    def components(self, request, pk=None):
        """GET: direct children. POST: add an existing asset as a child (assembly/component/replaceable part)."""
        asset = self._asset(request, pk)
        if request.method == "GET":
            qs = selectors.components_for(request.membership, request.organization).filter(parent=asset)
            return paginate(request, qs, ComponentSerializer)
        d = _validated(ComponentCreateSerializer, request)
        child = site_selectors.scoped_get(self._qs(request), d.pop("child"), "Asset")
        require_permission(request.membership, "asset.hierarchy.manage", child.site_id)
        link = hierarchy.add_component(asset, child, actor=request.user, request=request, **d)
        return Response(ComponentSerializer(link).data, status=status.HTTP_201_CREATED)


@extend_schema(parameters=[ID_PARAM])
class ComponentViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "asset.view", "retrieve": "asset.view", "partial_update": "asset.hierarchy.manage",
                      "move": "asset.hierarchy.manage", "destroy": "asset.hierarchy.manage"}

    def _link(self, request, pk, code=None):
        link = selectors.get_component(request.membership, request.organization, pk)
        if code:
            require_permission(request.membership, code, link.parent.site_id)
        return link

    @extend_schema(parameters=[query_param("parent", "parent asset id", OpenApiTypes.UUID),
                               query_param("child", "child asset id", OpenApiTypes.UUID)],
                   responses=ComponentSerializer(many=True))
    def list(self, request):
        import uuid

        qs = selectors.components_for(request.membership, request.organization)
        for name in ("parent", "child"):
            value = request.query_params.get(name)
            if value:
                try:
                    qs = qs.filter(**{f"{name}_id": uuid.UUID(value)})
                except ValueError:
                    qs = qs.none()
        return paginate(request, qs, ComponentSerializer)

    @extend_schema(responses=ComponentSerializer)
    def retrieve(self, request, pk=None):
        return Response(ComponentSerializer(self._link(request, pk)).data)

    @extend_schema(request=ComponentUpdateSerializer, responses=ComponentSerializer)
    def partial_update(self, request, pk=None):
        link = self._link(request, pk, "asset.hierarchy.manage")
        d = _validated(ComponentUpdateSerializer, request, partial=True)
        return Response(ComponentSerializer(hierarchy.update_component(
            link, actor=request.user, request=request, **d)).data)

    @extend_schema(request=MoveSerializer, responses=ComponentSerializer)
    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        """Re-parent the component under another asset (cycle, depth, site and tenant rules re-validated)."""
        link = self._link(request, pk, "asset.hierarchy.manage")
        d = _validated(MoveSerializer, request)
        new_parent = site_selectors.scoped_get(
            selectors.assets_for(request.membership, request.organization), d["parent"], "Asset")
        require_permission(request.membership, "asset.hierarchy.manage", new_parent.site_id)
        return Response(ComponentSerializer(hierarchy.move_component(
            link, new_parent, actor=request.user, request=request)).data)

    @extend_schema(responses={204: None})
    def destroy(self, request, pk=None):
        hierarchy.remove_component(self._link(request, pk, "asset.hierarchy.manage"), actor=request.user,
                                   request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(parameters=[ID_PARAM])
class MeterViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "asset.view", "retrieve": "asset.view", "create": "asset.update",
                      "activate": "asset.update", "deactivate": "asset.update",
                      "readings:get": "asset.view", "readings:post": "asset.meter.record"}

    def _meter(self, request, pk, code=None):
        meter = selectors.get_meter(request.membership, request.organization, pk)
        if code:
            require_permission(request.membership, code, meter.asset.site_id)
        return meter

    @extend_schema(parameters=[query_param("asset", "asset id", OpenApiTypes.UUID)],
                   responses=MeterSerializer(many=True))
    def list(self, request):
        import uuid

        qs = selectors.meters_for(request.membership, request.organization)
        if request.query_params.get("asset"):
            try:
                qs = qs.filter(asset_id=uuid.UUID(request.query_params["asset"]))
            except ValueError:
                qs = qs.none()
        return paginate(request, qs, MeterSerializer)

    @extend_schema(responses=MeterSerializer)
    def retrieve(self, request, pk=None):
        return Response(MeterSerializer(self._meter(request, pk)).data)

    @extend_schema(request=MeterWriteSerializer, responses={201: MeterSerializer})
    def create(self, request):
        d = _validated(MeterWriteSerializer, request)
        asset = site_selectors.scoped_get(selectors.assets_for(request.membership, request.organization),
                                          d["asset"], "Asset")
        require_permission(request.membership, "asset.update", asset.site_id)
        meter = services.create_meter(asset, name=d["name"], unit=d["unit"], actor=request.user, request=request)
        return Response(MeterSerializer(self._meter(request, meter.pk)).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=MeterSerializer)
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        meter = self._meter(request, pk, "asset.update")
        services.set_meter_active(meter, active=True, actor=request.user, request=request)
        return Response(MeterSerializer(self._meter(request, pk)).data)

    @extend_schema(request=None, responses=MeterSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        meter = self._meter(request, pk, "asset.update")
        services.set_meter_active(meter, active=False, actor=request.user, request=request)
        return Response(MeterSerializer(self._meter(request, pk)).data)

    @extend_schema(methods=["GET"], responses=ReadingSerializer(many=True))
    @extend_schema(methods=["POST"], request=ReadingWriteSerializer, responses={201: ReadingSerializer})
    @action(detail=True, methods=["get", "post"])
    def readings(self, request, pk=None):
        meter = self._meter(request, pk)
        if request.method == "GET":
            return paginate(request, selectors.readings_for(request.organization, meter), ReadingSerializer)
        d = _validated(ReadingWriteSerializer, request)
        if "value" not in d:
            raise ValidationFailed("Reading value is required.")
        reading = services.record_reading(meter, value=d["value"], read_at=d.get("read_at"),
                                          notes=d.get("notes", ""), actor=request.user, request=request)
        return Response(ReadingSerializer(reading).data, status=status.HTTP_201_CREATED)

