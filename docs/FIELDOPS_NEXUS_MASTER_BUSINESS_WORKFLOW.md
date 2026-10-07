# FieldOps Nexus: master business workflow (permanent knowledge)

Project: FieldOps Nexus, Enterprise Asset, Maintenance & Field Service ERP. HPE project code HPE-PRD-2026-FOPS02.
This document is persistent project memory (Team Lead directive 2026-10-03). It survives /clear, /compact, new sessions, new agents and new developers. Chat history is not the project's memory; the repository is. Read `PROJECT_SOURCE_OF_TRUTH.md` first for priority rules, ownership and verification rules.

Labels used throughout: **HPE CONFIRMED** (explicit in the HPE PRD), **OUR IMPLEMENTATION DECISION**, **CLARIFICATION REQUIRED**, **EXAMPLE**. Never present an implementation decision as an HPE requirement.

## 1. One integrated ERP
FieldOps Nexus is ONE multi-organization ERP, not 15 applications: no per-module authentication, no duplicate Asset/Site/Work Order models, no disconnected dashboards, no frontend-only mock workflows. The 15 modules are functional boundaries. A module may trigger, consume, display or link another module's functionality but must not take over its authoritative responsibility. Every important workflow is:
UI -> backend endpoint/view -> authentication -> RBAC -> tenant/site scope -> domain/service rule -> database transaction -> PostgreSQL -> audit -> notification/background processing where applicable -> UI refresh. Never "button -> change frontend state -> show Success".

## 2. Tenancy and onboarding
Platform Super Admin -> Organization -> Organization users -> Roles -> Permissions -> Sites -> Locations -> Assets -> Operations. Each organization owns and sees only its own users, roles, sites, zones, service areas, assets, hierarchy, incidents, service requests, work orders, technicians, inventory, contracts, SLA data, reports, audit records and files. Every sensitive request validates: authenticated identity, organization membership, permission, tenant ownership, site scope, object ownership, workflow state, domain rules. IDOR/cross-tenant access is actively tested.
Onboarding (OUR IMPLEMENTATION DECISION unless HPE says otherwise): Super Admin creates the organization and invites the initial Organization Administrator by a secure invitation/password-setup link (never a plaintext password); the admin configures users/roles, then sites/locations, assets and operational setup.

## 3. One account, role-aware
One person = one account: User -> Organization membership -> Role(s) -> Permissions -> allowed modules/actions/data scope (optionally site-scoped). Role-aware navigation is fine; backend authorization is mandatory. Role names (Org Owner/Admin, Operations, Asset Manager, Maintenance Planner, Supervisor, Service Manager, Technician, Stores, Client/Requester) are EXAMPLES / implementation decisions (`rbac/role_templates.py`, D-005).

## 4. Modules (HPE numbering, never renumber)
| ID | Module | Answers | Owns (authoritative) |
|---|---|---|---|
| M01 | Site & Location Master | Where do we operate and where can work happen? | organization/site/location configuration, calendars, contacts |
| M02 | Asset Registry | What physical thing do we manage? | the Asset master record, status, documents |
| M03 | Asset Hierarchy | How are assets/components related? | relationships, components, (meters/readings: see conflict C-2) |
| M04 | Preventive Maintenance | How do we maintain before failure? | PM plans/schedules; creates maintenance demand |
| M05 | Incident / Breakdown | What failed and what is the impact? | incident/service-request workflow |
| M06 | Work Order Management | How is work planned and executed? | the Work Order lifecycle |
| M07 | Technician Workspace | How does the technician execute? | technician-facing experience only |
| M08 | Inspection & Checklist Engine | What must be checked/recorded? | templates, inspections, responses, findings |
| M09 | Spare Parts & Inventory | What stock exists and moved? | stock truth and movements |
| M10 | Warranty / AMC / Contract | What coverage applies? | coverage |
| M11 | SLA & Escalation | What timing applies and was it breached? | SLA truth, timers, breaches, escalations |
| M12 | QR / Barcode | How do we identify assets physically? | opaque identity, labels, scanning |
| M13 | Client / Requester Portal | How do clients request and follow up? | client-facing experience |
| M14 | Operational Dashboards | How is operations performing? | read/analytics views over real data |
| M15 | Audit & Compliance | Who did what, when, what changed? | audit/compliance evidence |
Per-module detail: `docs/modules/Mxx_*.md`.

## 5. Business story (end to end)
Organization created -> site -> buildings/zones/service areas -> asset registered -> hierarchy/components -> PM may be configured -> incident/service request raised -> triaged -> approved/rejected -> approved request creates/links a Work Order -> M06 plans it -> technician assigned -> dispatched -> started -> checklist executed -> parts requested/reserved/issued/consumed/returned -> labor/time captured -> evidence/notes -> completed -> supervisor review -> closed -> request resolved/confirmed/closed -> audit evidence exists -> dashboard metrics update.
Parallel: Asset -> Warranty/AMC/Contract; Asset -> QR/Barcode; Request/WO -> SLA.

## 6. Mandatory state machines (HPE CONFIRMED, section 8.1)
- Service Request: NEW -> TRIAGED -> APPROVED / REJECTED -> WORK ORDER CREATED -> IN SERVICE -> RESOLVED -> CONFIRMED -> CLOSED
- Work Order: DRAFT -> PLANNED -> ASSIGNED -> DISPATCHED -> IN PROGRESS -> ON HOLD (back to IN PROGRESS) -> COMPLETED -> SUPERVISOR REVIEW -> CLOSED. Never DRAFT -> CLOSED unless an approved workflow says so.
- Preventive Maintenance: SCHEDULED -> DUE -> GENERATED -> ASSIGNED -> COMPLETED -> VERIFIED -> NEXT CYCLE
- Part Request: REQUESTED -> RESERVED -> ISSUED -> CONSUMED / RETURNED -> RECONCILED
- Asset: ACTIVE -> UNDER MAINTENANCE -> OUT OF SERVICE -> ACTIVE / RETIRED / DISPOSED (our exact transition table: D-027 and `assets/workflow.py`).
Every machine has backend transition enforcement; every allowed and forbidden transition is tested.

## 7. Cross-module triggers (approved implementation relationships; never duplicate authoritative state)
WO starts -> Service Request IN SERVICE where applicable; Asset may become UNDER MAINTENANCE per the approved rule. WO completed -> Service Request may become RESOLVED; completion evidence recorded. WO closed -> approved closure actions; Asset may return to ACTIVE; downtime may end; SLA may stop. PM schedule -> generates Work Order. M12 scan -> resolves Asset -> opens approved context. M13 client request -> enters the M05 workflow.

## 8. Module boundary rules (critical)
M05 may create/link a Work Order after approval but M06 owns its lifecycle (M05 must not become M06). M07 consumes M06's Work Order and must not become a second WO system. M04 creates demand; M06 owns the generated WO; the scheduler is idempotent and concurrency-safe. M08 completion may be a prerequisite for M06 closure. M09 owns stock truth: every issue/return creates stock-movement records, transactional, concurrency/rollback tested, never just `quantity -= N`. M10 provides coverage context; M11 owns SLA behaviour (response and resolution are separate; exact pause states and business-hour semantics are NOT HPE-defined: do not present them as HPE requirements); escalation monitoring is retry-safe with no duplicate events. M12 identifiers are opaque and never expose sensitive DB ids; M12 does not own the incident/WO lifecycle. M13 clients see only authorised records and do not control assignment, inventory, internal SLA config. M14 uses real transactional data (MTTR, MTBF, downtime, open WOs, technician utilization, SLA breaches, PM compliance, parts consumption), traceable to source records, never hardcoded. M15 audit is generated by real operations, protected from ordinary modification, covering create/update/delete-where-allowed/transitions/assignments/approvals/closures/inventory movement/security events/exports.

## 9. Dependency graph and order
Foundation -> M01 -> M02 -> M03; M02 -> M04, M05, M06, M10, M12; M05 -> M06; M08 -> M06; M04 -> M06; M06 -> M07, M09, M11, M15; M13 -> M05; M04/M05/M06/M09/M11 -> M14; all critical operations -> M15. Vertical slice: M01 -> M02 -> M03 -> M05 -> M06 -> M07 -> M08 -> M09 -> M15.
Development order: Foundation; Phase 1 M01->M02->M03; Phase 2 M05->M06; Phase 3 M08->M07; Phase 4 M09; Phase 5 M04; Phase 6 M11; Phase 7 M10; Phase 8 M12->M13; Phase 9 M14->M15; then full HPE E2E acceptance. Do not start a downstream module by duplicating upstream functionality; do not start the next phase while the current phase has unresolved Critical/High defects.

## 10. HPE Day-90 acceptance journeys (HPE CONFIRMED)
1 Register site and asset hierarchy, show status/change history. 2 PM plan -> scheduler generates a WO. 3 Assign technician -> checklist -> spare parts -> labor -> complete WO. 4 High-priority request -> SLA response/resolution timers -> escalation. 5 Inventory issue/return -> stock movement -> WO linkage. 6 QR/barcode -> open asset -> create service event. 7 Client request -> technician completion -> closure confirmation -> audit evidence -> dashboard metrics.

## 11. Team ownership (coordination information)
Team Lead: M05, M06, M11, M14, M15, architecture, integration contracts, critical transactions, RBAC/integration oversight, final debugging and acceptance. Prasad / Sub-TL: M04, M07, M08, M09, code review. Vishnu: M01. Raju: M02. Veeresh: M03. Naresh: M10. Purva: M12, M13.
