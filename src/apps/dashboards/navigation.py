from apps.ui.navigation import NavItem, register

register(
    NavItem(
        label="Analytics Hub",
        url_name="dashboards:hub",
        permission="report.view",
        icon="grid",
        section="Analytics",
        order=10,
        match_prefix="/app/dashboards",
    )
)

register(
    NavItem(
        label="My Work Cockpit",
        url_name="dashboards:my_work",
        permission="work_order.view_assigned",
        icon="activity",
        section="Operations",
        order=25,
        match_prefix="/app/dashboards/my-work",
    )
)
