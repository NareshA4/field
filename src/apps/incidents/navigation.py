from apps.ui.navigation import NavItem, register

register(NavItem("Incidents & Requests", "incidents:list", "incident.view", "bell", "Operations", 10, "/app/incidents"))
