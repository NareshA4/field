from apps.ui.navigation import NavItem, register

register(
    NavItem(
        "My Jobs",
        "workspace:my_jobs",
        "work_order.view_assigned",
        "wrench",
        "Operations",
        25,
        "/app/workspace",
    )
)
