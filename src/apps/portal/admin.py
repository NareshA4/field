from django.contrib import admin
from .models import PortalAccount, PortalAssetGrant, PortalClosureConfirmation


@admin.register(PortalAccount)
class PortalAccountAdmin(admin.ModelAdmin):
    list_display = ["company_name", "membership", "organization", "is_enabled", "created_at"]
    list_filter = ["is_enabled", "organization"]
    search_fields = ["company_name", "membership__user__email", "membership__user__full_name"]


@admin.register(PortalAssetGrant)
class PortalAssetGrantAdmin(admin.ModelAdmin):
    list_display = ["portal_account", "asset", "organization", "created_at"]
    list_filter = ["organization"]
    search_fields = ["portal_account__company_name", "asset__asset_tag", "asset__name"]


@admin.register(PortalClosureConfirmation)
class PortalClosureConfirmationAdmin(admin.ModelAdmin):
    list_display = ["request", "confirmed_by", "confirmed_at", "rating", "organization"]
    list_filter = ["rating", "organization"]
    search_fields = ["request__number", "confirmed_by__email", "feedback"]

