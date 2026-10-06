from apps.ui.navigation import NavItem, register

register(NavItem("Warranties & contracts", "contracts:agreements", "contract.view", "shield", "Coverage", 10,
                 "/app/contracts/agreements"))
register(NavItem("Expiring coverage", "contracts:expiry", "contract.view", "clock", "Coverage", 20,
                 "/app/contracts/expiry"))
register(NavItem("Providers", "contracts:providers", "contract.view", "building", "Coverage", 30,
                 "/app/contracts/providers"))
