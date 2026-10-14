from django.apps import AppConfig


class PortalConfig(AppConfig):
    name = "apps.portal"
    label = "portal"
    verbose_name = "Client / Requester Portal"

    def ready(self):
        import apps.portal.permissions  # noqa: F401
