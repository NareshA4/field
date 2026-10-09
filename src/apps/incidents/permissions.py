"""M05 permissions. Every request is bound to an asset, hence to a site, so all are site-scoped."""
from apps.rbac.catalog import register

register("incident", "incident.view", "View incidents and service requests", site_scoped=True)
register("incident", "incident.create", "Report incidents and service requests", site_scoped=True)
register("incident", "incident.update", "Edit incident details before approval", site_scoped=True)
register("incident", "incident.attach", "Attach evidence (photos / files) to an incident", site_scoped=True)
register("incident", "incident.triage", "Triage incidents", site_scoped=True)
register("incident", "incident.approve", "Approve or reject triaged incidents", site_scoped=True)
register("incident", "incident.confirm", "Confirm a resolution (or reopen it)", site_scoped=True)
register("incident", "incident.close", "Close confirmed incidents", site_scoped=True)
register("incident", "incident.downtime.manage", "Record and correct incident downtime", site_scoped=True)
