from django.apps import AppConfig
from django.utils.module_loading import autodiscover_modules


class UiConfig(AppConfig):
    name = "apps.ui"
    label = "ui"

    def ready(self):
        from . import navigation_items  # noqa: F401  (foundation entries)

        autodiscover_modules("navigation")  # module apps register their own entries
