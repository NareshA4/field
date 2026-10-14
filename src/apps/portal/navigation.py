from apps.ui.navigation import NavItem, register

register(NavItem("Client Portal", "portal:dashboard", "portal.request.view", "grid", "Portal", 5, "/app/portal"))
register(NavItem("My Requests", "portal:requests_list", "portal.request.view", "list-check", "Portal", 7, "/app/portal/requests"))
register(NavItem("New Request", "portal:request_create", "portal.request.create", "plus-circle", "Portal", 10, "/app/portal/requests/new"))
register(NavItem("Portal Accounts", "portal:accounts_list", "portal.manage", "people", "Administration", 35, "/app/portal/accounts"))

