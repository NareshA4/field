"""M02/M03 permissions. Everything bound to an asset is site-scoped through the asset's site."""
from apps.rbac.catalog import register

register("asset", "asset.view", "View assets, their documents, meters and hierarchy", site_scoped=True)
register("asset", "asset.create", "Register assets", site_scoped=True)
register("asset", "asset.update", "Edit assets and move them between locations", site_scoped=True)
register("asset", "asset.change_status", "Change the operational status of assets", site_scoped=True)
register("asset", "asset.history.view", "View asset status and location history", site_scoped=True)
register("asset", "asset.document.manage", "Upload asset documents", site_scoped=True)
register("asset", "asset.meter.record", "Record meter readings", site_scoped=True)
register("asset", "asset.hierarchy.manage", "Manage asset parent/child components", site_scoped=True)
register("asset", "asset.category.manage", "Manage asset categories")
