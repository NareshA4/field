from django.apps import AppConfig


class WorkOrdersConfig(AppConfig):
    name = "apps.workorders"
    label = "workorders"

    def ready(self):
        from apps.files import access
        from apps.rbac import services as rbac

        def can_read_work_order_file(membership, wo) -> bool:
            if rbac.has_permission(membership, "work_order.view", wo.site_id):
                return True
            # a technician sees the files of the work orders assigned to them
            return wo.assigned_to_id == membership.pk and rbac.has_permission(
                membership, "work_order.view_assigned", wo.site_id)

        access.register("workorders.workorder", can_read_work_order_file)
