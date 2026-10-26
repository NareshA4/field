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


# The 10 Primary System Role Templates as defined by PRD §3.2 (with legacy keys preserved for DB stability)
TEMPLATES: tuple[RoleTemplate, ...] = (
    RoleTemplate("owner", "Organization Owner", "Full control of the organization and all modules.", ("*",), is_owner=True),
    RoleTemplate(
        "admin", "Organization Admin", "Administers users, roles, sites and configuration.",
        ("organization.*", "user.*", "role.*", "site.*", "zone.*", "calendar.*", "audit.view", "report.view", "qr.*",
         "*.view", "incident.triage", "incident.approve", "work_order.create"),
        excludes=("portal.request.*",),
    ),
    RoleTemplate(
        "asset_manager", "Asset Manager", "Owns the asset registry, documentation, meters, and hierarchy.",
        ("asset.*", "site.view", "site.create", "site.update", "zone.view", "zone.create", "zone.update",
         "incident.view", "calendar.view", "contract.*", "qr.*", "work_order.view", "inventory.view",
         "inventory.part.view", "report.view"),
    ),
    RoleTemplate(
        "maintenance_planner", "Maintenance Planner", "Plans, schedules and assigns preventive and corrective maintenance work.",
        ("asset.view", "asset.history.view", "site.view", "zone.view", "calendar.view", "maintenance.*", "incident.view",
         "work_order.view", "work_order.create", "work_order.plan", "work_order.assign",
         "work_order.dispatch", "work_order.update", "work_order.cancel", "work_order.attach", "checklist.view",
         "checklist.manage", "inspection.view", "inventory.view", "inventory.part.view", "inventory.request",
         "sla.view", "contract.view", "contract.check", "report.view", "qr.scan"),
    ),
    RoleTemplate(
        "service_dispatcher", "Service Dispatcher", "Assignment, dispatch, rescheduling, route coordination and escalation.",
        ("asset.view", "site.view", "zone.view", "calendar.view", "incident.view", "incident.triage", "incident.approve",
         "work_order.view", "work_order.assign", "work_order.dispatch", "work_order.plan", "work_order.update",
         "work_order.attach", "checklist.view", "inspection.view", "inventory.view", "inventory.part.view",
         "sla.view", "sla.acknowledge", "report.view", "qr.scan"),
    ),
    RoleTemplate(
        "technician", "Technician", "Executes assigned jobs, checklists, evidence, labor/time, and parts usage.",
        ("asset.view", "asset.meter.record", "site.view", "zone.view", "incident.create", "incident.view_assigned",
         "work_order.view_assigned", "work_order.start", "work_order.hold", "work_order.complete", "work_order.record",
         "work_order.attach", "incident.attach", "checklist.execute", "inspection.execute", "inventory.request",
         "inventory.consume", "inventory.part.view", "qr.scan"),
    ),
    RoleTemplate(
        "supervisor", "Maintenance Supervisor", "Reviews completed work, closure approval, exception handling and oversight.",
        ("asset.view", "asset.history.view", "site.view", "zone.view", "incident.view", "work_order.view",
         "work_order.assign", "work_order.dispatch", "work_order.review", "maintenance.view", "work_order.close",
         "work_order.hold", "work_order.record", "work_order.attach", "checklist.*", "inspection.*", "inventory.view",
         "inventory.part.view", "inventory.request", "sla.view", "report.view", "qr.scan"),
    ),
    RoleTemplate(
        "stores_manager", "Stores Manager", "Part master, warehouse stock, reservations, issue/return, transfers and reconciliation.",
        ("inventory.*", "asset.view", "work_order.view", "site.view", "report.view", "qr.scan"),
    ),
    RoleTemplate(
        "service_manager", "Service Manager", "SLA policy, breach review, contract/AMC operations and service requests.",
        ("incident.*", "sla.*", "contract.*", "portal.manage", "work_order.view", "asset.view", "site.view", "report.view"),
    ),
    RoleTemplate(
        "client_requester", "Client / Requester", "External requester raising service requests and tracking status in the client portal.",
        ("portal.request.create", "portal.request.view", "portal.request.confirm", "incident.report_owned",
         "incident.view_assigned", "incident.view_owned", "incident.attach", "incident.confirm"),
    ),
    RoleTemplate(
        "auditor", "Auditor / Report Consumer", "Read-only access to asset/service history, audit trail and compliance evidence.",
        ("*.view", "audit.view", "audit.export", "report.*"),
        excludes=("portal.request.*",),
    ),
    # Legacy Operations Manager template kept for backward-compatibility with existing seedings
    RoleTemplate(
        "operations_manager", "Operations Manager (Legacy)", "Oversees maintenance and field operations.",
        ("*.view", "incident.*", "work_order.view", "work_order.assign",
         "work_order.dispatch", "maintenance.*", "checklist.*", "inspection.*", "sla.view",
         "sla.acknowledge", "inventory.request", "inventory.reserve", "inventory.reconcile", "contract.check",
         "report.*"),
        excludes=("portal.request.*",),
    ),
)

# Registry dictionary mapping both primary PRD keys and modern aliases
BY_KEY = {t.key: t for t in TEMPLATES}

# Aliases for modern PRD naming conventions
BY_KEY["field_technician"] = BY_KEY["technician"]
BY_KEY["stores_inventory"] = BY_KEY["stores_manager"]
BY_KEY["compliance"] = BY_KEY["auditor"]
