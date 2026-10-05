from apps.ui.navigation import NavItem, register

register(NavItem("Compliance reports", "audit:reports", "audit.view", "list-check", "Compliance", 91,
                 "/app/audit/reports"))
