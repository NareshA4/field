from apps.ui.navigation import NavItem, register

register(NavItem("Incidents & Requests", "incidents:list", "incident.view", "bell", "Operations", 10, "/app/incidents"))
register(NavItem("Incident dashboard", "incidents:dashboard", None, "grid", "Operations", 9,
                 "/app/incidents/dashboard/"))
