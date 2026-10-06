from django.apps import AppConfig


class WorkOrdersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.workorders"
    label = "workorders"
    verbose_name = "Work Order Management"
