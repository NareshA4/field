from django.apps import AppConfig


class MaintenanceConfig(AppConfig):
    name = "apps.maintenance"
    label = "maintenance"

    def ready(self):
        try:
            import apps.maintenance.permissions  # noqa: F401
        except ImportError:
            pass
