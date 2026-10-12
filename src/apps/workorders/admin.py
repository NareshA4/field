from django.contrib import admin

from .models import (
    ClosureApproval,
    WorkOrder,
    WorkOrderEvent,
    WorkOrderEvidence,
    WorkOrderLabor,
    WorkOrderMaterial,
)


class EventInline(admin.TabularInline):
    model = WorkOrderEvent
    extra = 0
    can_delete = False
    readonly_fields = ["organization", "actor", "action", "from_status", "to_status", "notes", "metadata"]


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ["order_number", "title", "site", "priority", "status", "assigned_to", "created_at"]
    list_filter = ["status", "priority", "work_type"]
    search_fields = ["order_number", "title", "asset_reference"]
    readonly_fields = ["organization", "order_number", "created_by", "created_at", "updated_at"]
    inlines = [EventInline]


admin.site.register(WorkOrderLabor)
admin.site.register(WorkOrderMaterial)
admin.site.register(WorkOrderEvidence)


@admin.register(ClosureApproval)
class ClosureApprovalAdmin(admin.ModelAdmin):
    list_display = ["work_order", "decision", "approver", "created_at"]
    readonly_fields = ["organization", "work_order", "approver", "decision", "comment", "created_at", "updated_at"]
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
