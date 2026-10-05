from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.utils.module_loading import autodiscover_modules


def _sync(sender, **kwargs):
    from . import services

    services.sync_permissions()
    services.sync_all_organizations()


class RbacConfig(AppConfig):
    name = "apps.rbac"
    label = "rbac"

    def ready(self):
        autodiscover_modules("permissions")  # every app's <app>/permissions.py registers its catalog
        post_migrate.connect(_sync, sender=self, dispatch_uid="rbac_sync_permissions_and_roles")
