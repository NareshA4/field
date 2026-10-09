"""M10 permissions (role templates already grant ``contract.*`` to asset and service managers, ``*.view`` to
operations and auditors). All are site-scoped through the agreement's site."""
from apps.rbac.catalog import register

register("contracts", "contract.view", "View warranties, AMCs, contracts, providers and asset coverage",
         site_scoped=True)
register("contracts", "contract.create", "Create providers and coverage agreements", site_scoped=True)
register("contracts", "contract.update", "Edit agreements, covered assets and exclusions; renew; deactivate",
         site_scoped=True)
register("contracts", "contract.check", "Record a coverage / eligibility check against a work order",
         site_scoped=True)
