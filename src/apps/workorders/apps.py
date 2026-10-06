<<<<<<< HEAD
﻿from django.apps import AppConfig


class WorkOrdersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.workorders"
    label = "workorders"
    verbose_name = "Work Order Management"
=======
from django.apps import AppConfig


class WorkOrdersConfig(AppConfig):
    name = "apps.workorders"
    label = "workorders"
>>>>>>> ba3f639 (feat(m08): implement Inspection & Checklist Engine with Work Order integration)

    def ready(self):
        from apps.files import access
        from apps.rbac import services as rbac

<<<<<<< HEAD
        def can_read_work_order_file(membership, work_order):
            if rbac.has_permission(membership, "work_order.view", work_order.site_id):
                return True
            return (work_order.assigned_to_id == membership.pk and
                    rbac.has_permission(membership, "work_order.view_assigned", work_order.site_id))
=======
        def can_read_work_order_file(membership, wo) -> bool:
            if rbac.has_permission(membership, "work_order.view", wo.site_id):
                return True
            # a technician sees the files of the work orders assigned to them
            return wo.assigned_to_id == membership.pk and rbac.has_permission(
                membership, "work_order.view_assigned", wo.site_id)
>>>>>>> ba3f639 (feat(m08): implement Inspection & Checklist Engine with Work Order integration)

        access.register("workorders.workorder", can_read_work_order_file)
