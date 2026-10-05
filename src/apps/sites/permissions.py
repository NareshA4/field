"""M01 permissions. ``site_scoped`` ones can be granted for specific sites (rbac.MembershipRole.site)."""
from apps.rbac.catalog import register

register("site", "site.view", "View sites", site_scoped=True)
register("site", "site.create", "Create sites (organization-wide)")
register("site", "site.update", "Edit sites", site_scoped=True)
register("site", "site.deactivate", "Deactivate / reactivate sites", site_scoped=True)
register("site", "site.contact.manage", "Manage the site contact hierarchy", site_scoped=True)
register("zone", "zone.view", "View buildings, zones and service areas", site_scoped=True)
register("zone", "zone.create", "Create buildings, zones and service areas", site_scoped=True)
register("zone", "zone.update", "Edit buildings, zones and service areas", site_scoped=True)
register("zone", "zone.deactivate", "Deactivate / reactivate zones", site_scoped=True)
register("calendar", "calendar.view", "View operating calendars", site_scoped=True)
register("calendar", "calendar.create", "Create operating calendars", site_scoped=True)
register("calendar", "calendar.update", "Edit operating calendars and holidays", site_scoped=True)
register("calendar", "calendar.delete", "Delete operating calendars", site_scoped=True)
