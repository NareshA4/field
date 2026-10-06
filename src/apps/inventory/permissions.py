"""M09 permissions. Stock operations are site-scoped through the warehouse's site; the part catalogue is
organization-wide (parts are not tied to a site)."""
from apps.rbac.catalog import register

register("inventory", "inventory.part.view", "View the spare-part catalogue")
register("inventory", "inventory.part.manage", "Create / edit / deactivate spare parts")
register("inventory", "inventory.view", "View warehouses, stock levels, reservations and movements", site_scoped=True)
register("inventory", "inventory.warehouse.manage", "Create / edit / deactivate warehouses", site_scoped=True)
register("inventory", "inventory.receive", "Receive stock into a warehouse", site_scoped=True)
register("inventory", "inventory.adjust", "Adjust stock (count corrections) and set min / max levels", site_scoped=True)
register("inventory", "inventory.transfer", "Transfer stock between warehouses", site_scoped=True)
register("inventory", "inventory.request", "Request parts for a work order", site_scoped=True)
register("inventory", "inventory.reserve", "Reserve / release stock for a work order", site_scoped=True)
register("inventory", "inventory.issue", "Issue reserved / available stock to a work order", site_scoped=True)
register("inventory", "inventory.return", "Return issued stock to a warehouse", site_scoped=True)
register("inventory", "inventory.consume", "Record consumption of issued parts on a work order", site_scoped=True)
register("inventory", "inventory.reconcile", "Reconcile or cancel a work-order part line", site_scoped=True)
