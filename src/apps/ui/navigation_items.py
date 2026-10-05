from .navigation import NavItem, register

register(NavItem("Dashboard", "ui:home", None, "grid", "Overview", 1, "/app/"))
register(NavItem("Organization", "tenancy:organization", "organization.view", "building", "Administration", 10, "/app/organization"))
register(NavItem("Users", "tenancy:members", "user.view", "people", "Administration", 20, "/app/users"))
register(NavItem("Roles & Permissions", "rbac:roles", "role.view", "shield", "Administration", 30, "/app/roles"))
register(NavItem("Audit Trail", "audit:list", "audit.view", "clock", "Compliance", 90, "/app/audit"))
