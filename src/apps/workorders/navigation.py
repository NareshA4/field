from apps.ui.navigation import NavItem, register

register(NavItem("Work Orders", "workorders:list", "work_order.view_assigned", "grid", "Operations", 20,
                 "/app/work-orders"))
