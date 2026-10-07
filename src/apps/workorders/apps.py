from django.apps import AppConfig


class WorkOrdersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.workorders"
    label = "workorders"
    verbose_name = "Work Order Management"

    def ready(self):
        from apps.files import access
        from apps.rbac import services as rbac

        def can_read_work_order_file(membership, work_order):
            if rbac.has_permission(membership, "work_order.view", work_order.site_id):
                return True
            return (work_order.assigned_to_id == membership.pk and
                    rbac.has_permission(membership, "work_order.view_assigned", work_order.site_id))

        access.register("workorders.workorder", can_read_work_order_file)
