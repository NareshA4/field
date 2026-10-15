"""M04 permissions (role templates already grant ``maintenance.*`` to planners and operations managers). All are
site-scoped through the plan's asset site."""
from apps.rbac.catalog import register

register("maintenance", "maintenance.view", "View maintenance plans, schedules and PM history", site_scoped=True)
register("maintenance", "maintenance.create", "Create maintenance plans and schedules", site_scoped=True)
register("maintenance", "maintenance.update", "Edit plans / schedules and enable or disable them", site_scoped=True)
register("maintenance", "maintenance.generate", "Generate the next PM work order now", site_scoped=True)
