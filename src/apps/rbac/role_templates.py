"""System role templates (FieldOps Nexus implementation decision D-005; HPE gives no role names).

Patterns are ``fnmatch`` expressions evaluated against the *registered* permission catalog, so permissions
added by later modules (asset.*, work_order.*, ...) are picked up automatically when roles are re-synced.
Syncing is ADDITIVE: it never removes permissions an organization admin granted or revoked-then-regranted;
it only adds template permissions that did not exist when the role was seeded.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RoleTemplate:
    key: str
    name: str
    description: str
    patterns: tuple[str, ...] = field(default_factory=tuple)
    is_owner: bool = False
    # codes removed from the wildcard matches (an exact, wildcard-free pattern is never removed): e.g. staff roles with
    # ``*.view`` must not receive the client-portal permissions
    excludes: tuple[str, ...] = field(default_factory=tuple)


TEMPLATES: tuple[RoleTemplate, ...] = (
    RoleTemplate("owner", "Organization Owner", "Full control of the organization.", ("*",), is_owner=True),
    RoleTemplate(
        "admin", "Organization Admin", "Administers users, roles, sites and configuration.",
        ("organization.*", "user.*", "role.*", "site.*", "zone.*", "calendar.*", "audit.view", "report.view",
         "*.view"),
        excludes=("portal.request.*",),
    ),
    RoleTemplate(
        "operations_manager", "Operations Manager", "Oversees maintenance and field operations.",
        ("*.view", "incident.*", "work_order.*", "maintenance.*", "checklist.*", "inspection.*", "sla.view",
         "inventory.request", "inventory.reserve", "inventory.reconcile", "sla.acknowledge", "contract.check",
         "report.*"),
        excludes=("portal.request.*",),
    ),
    RoleTemplate(
        "asset_manager", "Asset Manager", "Owns the asset registry and hierarchy.",
        ("asset.*", "site.view", "site.create", "site.update", "zone.view", "zone.create", "zone.update",
         "calendar.view", "contract.*", "qr.*", "work_order.view", "report.view"),
    ),
    RoleTemplate(
        "maintenance_planner", "Maintenance Planner", "Plans, schedules and assigns maintenance work.",
        ("asset.view", "asset.history.view", "site.view", "zone.view", "calendar.view", "maintenance.*", "incident.view",
         "work_order.view", "work_order.create", "work_order.plan", "work_order.assign",
         "work_order.dispatch", "work_order.update", "work_order.cancel", "work_order.attach", "checklist.view",
         "checklist.manage", "inspection.view",
         "inventory.view", "inventory.part.view", "inventory.request", "sla.view", "contract.view", "contract.check"),
    ),
    RoleTemplate(
        "supervisor", "Maintenance Supervisor", "Reviews and closes completed work.",
        ("asset.view", "asset.history.view", "site.view", "zone.view", "incident.view", "work_order.view",
         "work_order.review",
         "maintenance.view", "work_order.close", "work_order.hold", "work_order.record", "work_order.attach", "checklist.*", "inspection.*", "inventory.view",
         "inventory.part.view", "inventory.request", "sla.view", "report.view"),
    ),
    RoleTemplate(
        "technician", "Technician", "Executes assigned field jobs.",
        ("asset.view", "asset.meter.record", "site.view", "zone.view", "incident.create", "incident.view",
         "work_order.view_assigned",
         "work_order.start", "work_order.hold", "work_order.complete", "work_order.record", "work_order.attach",
         "incident.attach", "checklist.execute",
         "inspection.execute", "inventory.request", "inventory.consume", "inventory.part.view"),
    ),
    RoleTemplate(
        "stores_manager", "Stores Manager", "Manages warehouses, stock and part issues.",
        ("inventory.*", "asset.view", "work_order.view", "site.view"),
    ),
    RoleTemplate(
        "service_manager", "Service Manager", "Owns service levels, requests and contracts.",
        ("incident.*", "sla.*", "contract.*", "portal.manage", "work_order.view", "asset.view",
         "site.view", "report.view"),
    ),
    RoleTemplate(
        "client_requester", "Client / Requester", "External requester using the client portal.",
        ("portal.request.create", "portal.request.view", "portal.request.confirm"),
    ),
    RoleTemplate(
        "auditor", "Auditor / Report Consumer", "Read-only access to records, audit and reports.",
        ("*.view", "audit.view", "audit.export", "report.*"),
        excludes=("portal.request.*",),
    ),
)

BY_KEY = {t.key: t for t in TEMPLATES}
