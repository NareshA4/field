from django.apps import AppConfig


class AssetsConfig(AppConfig):
    name = "apps.assets"
    label = "assets"

    def ready(self):
        from apps.files import access
        from apps.rbac import services as rbac

        def can_read_document(membership, document) -> bool:
            # downloads follow the asset's site scope, not just an organization-wide permission
            return document.is_active and rbac.has_permission(membership, "asset.view", document.asset.site_id)

        access.register("assets.assetdocument", can_read_document)
