"""M13 Client / Requester portal permissions."""
from apps.rbac.catalog import register

register("portal", "portal.request.create", "Submit client service requests through the portal")
register("portal", "portal.request.view", "View own client portal requests and dashboard")
register("portal", "portal.request.confirm", "Confirm resolution or request rework on own portal requests")
register("portal", "portal.manage", "Manage client portal accounts and access")
