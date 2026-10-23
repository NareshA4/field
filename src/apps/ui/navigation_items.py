from .navigation import NavItem, register

register(NavItem("Operations Overview", "ui:home", None, "grid", "Overview", 1, "/app/"))
register(NavItem("Organization", "tenancy:organization", "organization.update", "building", "Administration", 10, "/app/organization"))
register(NavItem("Users", "tenancy:members", "user.invite", "people", "Administration", 20, "/app/users"))
register(NavItem("Roles & Permissions", "rbac:roles", "role.manage", "shield", "Administration", 30, "/app/roles"))
register(NavItem("Audit Trail", "audit:list", "audit.view", "clock", "Compliance", 90, "/app/audit"))
