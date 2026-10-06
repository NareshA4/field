from django.apps import AppConfig


class AssetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.assets"
    label = "assets"
    verbose_name = "Asset Registry & Hierarchy"

    def ready(self):
        try:
            from apps.files import access
            from apps.rbac import services as rbac

            def can_read_document(membership, document) -> bool:
                return getattr(document, "is_active", True) and rbac.has_permission(membership, "asset.view", getattr(document.asset, "site_id", None))

            access.register("assets.assetdocument", can_read_document)
        except Exception:
            pass
