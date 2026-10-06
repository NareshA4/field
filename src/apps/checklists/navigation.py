from apps.ui.navigation import NavItem, register

register(NavItem("Checklists", "checklists:list", "checklist.view", "list-check", "Operations", 30, "/app/checklists"))
register(NavItem("Inspections", "checklists:inspections", "inspection.view", "clipboard", "Operations", 31,
                 "/app/inspections"))
