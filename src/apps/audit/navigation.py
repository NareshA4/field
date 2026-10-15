from apps.ui.navigation import NavItem, register

register(NavItem("Audit trail", "audit:list", "audit.view", "shield", "Compliance", 90,
                 "/app/audit/"))
register(NavItem("Compliance reports", "audit:reports", "audit.view", "list-check", "Compliance", 91,
                 "/app/audit/reports"))
