from django.apps import AppConfig


class IdentificationConfig(AppConfig):
    name = "apps.identification"
    label = "identification"

    def ready(self):
        import apps.identification.permissions  # noqa: F401
