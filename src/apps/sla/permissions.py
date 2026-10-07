"""M11 permissions. Viewing and acknowledging are site-scoped (through the tracked request / work order's site);
profile / rule configuration and running the monitor are organization-wide."""
from apps.rbac.catalog import register

register("sla", "sla.view", "View SLA trackings, breaches and metrics", site_scoped=True)
register("sla", "sla.acknowledge", "Acknowledge SLA breaches", site_scoped=True)
register("sla", "sla.manage", "Create / edit SLA profiles, targets and escalation rules")
register("sla", "sla.process", "Run the SLA monitor now")
