from apps.rbac.catalog import register

register(
    "dashboards",
    "report.view",
    "View operational dashboards and KPI reports",
    site_scoped=True,
)
