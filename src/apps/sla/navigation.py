from apps.ui.navigation import NavItem, register

register(NavItem("SLA tracking", "sla:trackings", "sla.view", "clock", "Service levels", 10, "/app/sla/trackings"))
register(NavItem("SLA breaches", "sla:breaches", "sla.view", "bell", "Service levels", 20, "/app/sla/breaches"))
register(NavItem("SLA profiles", "sla:profiles", "sla.manage", "shield", "Service levels", 30, "/app/sla/profiles"))
