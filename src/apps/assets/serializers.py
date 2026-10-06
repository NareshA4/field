from rest_framework import serializers
from apps.assets.models import (
    AssetCategory,
    Asset,
    AssetComponent,
    AssetMeter,
    AssetMeterReading,
    AssetStatusHistory,
    AssetDocument,
    AssetStatus,
    AssetCriticality,
)
from apps.sites.models import Site, Zone
from apps.tenancy.models import Organization


class AssetCategorySerializer(serializers.ModelSerializer):
    children_count = serializers.IntegerField(source="children.count", read_only=True)

    class Meta:
        model = AssetCategory
        fields = [
            "id",
            "organization",
            "name",
            "code",
            "description",
            "parent",
            "is_active",
            "children_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class AssetComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetComponent
        fields = [
            "id",
            "parent_asset",
            "name",
            "component_code",
            "serial_number",
            "is_replaceable",
            "status",
            "installed_at",
            "specifications",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class AssetMeterReadingSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetMeterReading
        fields = [
            "id",
            "meter",
            "reading_value",
            "reading_date",
            "recorded_by",
            "notes",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class AssetMeterSerializer(serializers.ModelSerializer):
    recent_readings = AssetMeterReadingSerializer(source="readings", many=True, read_only=True)

    class Meta:
        model = AssetMeter
        fields = [
            "id",
            "asset",
            "meter_name",
            "unit_of_measurement",
            "current_reading",
            "last_reading_at",
            "recent_readings",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "current_reading", "last_reading_at", "created_at", "updated_at"]


class AssetStatusHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetStatusHistory
        fields = [
            "id",
            "asset",
            "previous_status",
            "new_status",
            "reason",
            "changed_by",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class AssetDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetDocument
        fields = [
            "id",
            "asset",
            "title",
            "document_type",
            "file_url",
            "version",
            "uploaded_by",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class AssetListSerializer(serializers.ModelSerializer):
    """Compact serializer for table grids and list views."""
    category_name = serializers.CharField(source="category.name", read_only=True)
    site_name = serializers.CharField(source="site.name", read_only=True)
    zone_name = serializers.CharField(source="zone.name", read_only=True, default="")
    parent_asset_code = serializers.CharField(source="parent_asset.asset_id", read_only=True, default="")
    child_count = serializers.IntegerField(source="child_assets.count", read_only=True)
    components_count = serializers.IntegerField(source="components.count", read_only=True)

    class Meta:
        model = Asset
        fields = [
            "id",
            "asset_id",
            "opaque_identifier",
            "name",
            "category",
            "category_name",
            "site",
            "site_name",
            "zone",
            "zone_name",
            "parent_asset",
            "parent_asset_code",
            "model_number",
            "serial_number",
            "manufacturer",
            "status",
            "criticality",
            "department",
            "child_count",
            "components_count",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "opaque_identifier", "created_at", "updated_at"]


class AssetHierarchyNodeSerializer(serializers.ModelSerializer):
    """
    Recursive serializer returning the full N-level assembly tree (M03).
    Includes sub-assets and replaceable sub-components.
    """
    category_name = serializers.CharField(source="category.name", read_only=True)
    site_name = serializers.CharField(source="site.name", read_only=True)
    zone_name = serializers.CharField(source="zone.name", read_only=True, default="")
    components = AssetComponentSerializer(many=True, read_only=True)
    children = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            "id",
            "asset_id",
            "opaque_identifier",
            "name",
            "category_name",
            "site_name",
            "zone_name",
            "status",
            "criticality",
            "model_number",
            "serial_number",
            "components",
            "children",
        ]

    def get_children(self, obj):
        children_qs = obj.child_assets.filter(is_active=True).prefetch_related("components", "child_assets")
        return AssetHierarchyNodeSerializer(children_qs, many=True).data


class AssetDetailSerializer(serializers.ModelSerializer):
    """Full detail view for single asset profile."""
    category_name = serializers.CharField(source="category.name", read_only=True)
    site_name = serializers.CharField(source="site.name", read_only=True)
    zone_name = serializers.CharField(source="zone.name", read_only=True, default="")
    parent_asset_code = serializers.CharField(source="parent_asset.asset_id", read_only=True, default="")
    components = AssetComponentSerializer(many=True, read_only=True)
    meters = AssetMeterSerializer(many=True, read_only=True)
    documents = AssetDocumentSerializer(many=True, read_only=True)
    status_history = AssetStatusHistorySerializer(many=True, read_only=True)
    child_assets_count = serializers.IntegerField(source="child_assets.count", read_only=True)

    class Meta:
        model = Asset
        fields = [
            "id",
            "organization",
            "asset_id",
            "opaque_identifier",
            "name",
            "category",
            "category_name",
            "site",
            "site_name",
            "zone",
            "zone_name",
            "parent_asset",
            "parent_asset_code",
            "child_assets_count",
            "model_number",
            "serial_number",
            "manufacturer",
            "status",
            "criticality",
            "purchase_date",
            "purchase_cost",
            "commission_date",
            "warranty_expiry_date",
            "department",
            "specifications",
            "is_active",
            "components",
            "meters",
            "documents",
            "status_history",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "opaque_identifier", "created_by", "updated_by", "created_at", "updated_at"]


class AssetStatusChangeSerializer(serializers.Serializer):
    """Controlled status transition payload (CR-05)."""
    status = serializers.ChoiceField(choices=AssetStatus.choices)
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    changed_by = serializers.CharField(required=False, default="Technician / Operator")


class AssetMeterReadingInputSerializer(serializers.Serializer):
    """Payload for submitting a new meter reading."""
    reading_value = serializers.DecimalField(max_digits=14, decimal_places=2)
    recorded_by = serializers.CharField(required=False, default="Technician")
    notes = serializers.CharField(required=False, allow_blank=True, default="")


AssetSerializer = AssetDetailSerializer
