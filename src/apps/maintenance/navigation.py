from apps.ui.navigation import NavItem, register

register(NavItem("Maintenance plans", "maintenance:plans", "maintenance.view", "wrench", "Maintenance", 10,
                 "/app/maintenance/plans"))
register(NavItem("Due & upcoming", "maintenance:due", "maintenance.view", "clock", "Maintenance", 20,
                 "/app/maintenance/due"))
register(NavItem("PM history", "maintenance:history", "maintenance.view", "list-check", "Maintenance", 30,
                 "/app/maintenance/history"))
