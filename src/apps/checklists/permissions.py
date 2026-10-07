"""M08 permissions. Templates are organization-wide configuration; inspections belong to an asset / site."""
from apps.rbac.catalog import register

register("checklist", "checklist.view", "View checklist templates")
register("checklist", "checklist.manage", "Create, edit, activate and version checklist templates")
register("checklist", "checklist.execute", "Choose a checklist template to inspect with")
register("inspection", "inspection.view", "View all inspections and findings in scope", site_scoped=True)
register("inspection", "inspection.execute", "Start inspections, record answers and complete them (own / assigned work)",
         site_scoped=True)
register("inspection", "inspection.review", "Resolve findings", site_scoped=True)
