"""Foundation-level permissions (organization, users, roles, audit). Module apps add their own."""
from .catalog import register

register("organization", "organization.view", "View organization profile")
register("organization", "organization.update", "Edit organization profile")
register("user", "user.view", "View users and memberships")
register("user", "user.invite", "Invite users to the organization")
register("user", "user.update", "Edit users and their roles")
register("user", "user.deactivate", "Deactivate / reactivate users")
register("role", "role.view", "View roles and permissions")
register("role", "role.manage", "Create, edit and delete roles")
register("audit", "audit.view", "View the organization audit trail (site-scoped holders see only their sites)",
         site_scoped=True)
