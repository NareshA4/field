from django.contrib import admin
from apps.assets.models import (
    AssetCategory,
    Asset,
    AssetComponent,
    AssetMeter,
    AssetMeterReading,
    AssetStatusHistory,
    AssetDocument,
)


class AssetComponentInline(admin.TabularInline):
    model = AssetComponent
    fk_name = "parent"
    extra = 1
    fields = ["child", "relationship_type", "quantity", "part_number", "notes"]


class AssetMeterInline(admin.TabularInline):
    model = AssetMeter
    extra = 1
    fields = ["name", "unit", "is_active"]


class AssetDocumentInline(admin.TabularInline):
    model = AssetDocument
    extra = 1
    fields = ["title", "doc_type", "uploaded_by", "is_active"]


class AssetStatusHistoryInline(admin.TabularInline):
    model = AssetStatusHistory
    extra = 0
    can_delete = False
    fields = ["from_status", "to_status", "action", "reason", "changed_by", "created_at"]
    readonly_fields = ["from_status", "to_status", "action", "reason", "changed_by", "created_at"]


@admin.register(AssetCategory)
class AssetCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "organization", "is_active", "created_at"]
    list_filter = ["organization", "is_active"]
    search_fields = ["name", "description"]


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = [
        "asset_tag",
        "name",
        "category",
        "site",
        "zone",
        "status",
        "manufacturer",
        "model",
        "serial_number",
        "organization",
    ]
    list_filter = ["organization", "status", "site", "category"]
    search_fields = ["asset_tag", "name", "model", "serial_number", "manufacturer"]
    readonly_fields = ["id", "opaque_identifier", "created_at", "updated_at"]
    inlines = [AssetComponentInline, AssetMeterInline, AssetDocumentInline, AssetStatusHistoryInline]


@admin.register(AssetComponent)
class AssetComponentAdmin(admin.ModelAdmin):
    list_display = ["parent", "child", "relationship_type", "quantity", "part_number"]
    list_filter = ["relationship_type"]
    search_fields = ["parent__asset_tag", "child__asset_tag", "part_number"]


@admin.register(AssetMeter)
class AssetMeterAdmin(admin.ModelAdmin):
    list_display = ["name", "asset", "unit", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "asset__asset_tag", "asset__name"]


@admin.register(AssetMeterReading)
class AssetMeterReadingAdmin(admin.ModelAdmin):
    list_display = ["meter", "value", "read_at", "recorded_by"]
    list_filter = ["read_at"]
    readonly_fields = ["created_at"]


@admin.register(AssetStatusHistory)
class AssetStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ["asset", "from_status", "to_status", "action", "changed_by", "created_at"]
    list_filter = ["from_status", "to_status"]
    readonly_fields = ["id", "asset", "from_status", "to_status", "action", "reason", "changed_by", "created_at"]


@admin.register(AssetDocument)
class AssetDocumentAdmin(admin.ModelAdmin):
    list_display = ["title", "asset", "doc_type", "uploaded_by", "is_active", "created_at"]
    list_filter = ["doc_type", "is_active"]
    search_fields = ["title", "asset__asset_tag"]
