# M11 SLA & Escalation

Part of the ONE integrated FieldOps Nexus ERP. Read `docs/PROJECT_SOURCE_OF_TRUTH.md` and `docs/FIELDOPS_NEXUS_MASTER_BUSINESS_WORKFLOW.md` first.

- **Answers:** What timing applies, and was it breached?
- **HPE CONFIRMED scope (section 7.2):** Response/resolution targets by priority, automated escalation, breach tracking, supervisor alerts.
- **Team owner / phase:** Team Lead / Phase 6
- **Authoritatively owns:** SLA profiles, priority mapping, timers, pause rules, breaches, escalation rules and events.
- **Consumes:** Incident / Breakdown, Work Order, and contract/AMC coverage data.
- **Provides:** Breach/escalation data to M14, M15 and notifications.
- **Boundary rules:** Response and resolution are separate. Targets use 24/7 elapsed minutes; business calendars and holidays do not affect SLA due times (D-047). Pause states are configurable per policy. Celery Beat dispatches the monitor once per minute; each active organization is processed independently, with per-record error logging and deduplicated notifications/escalations.
- **Policy selection:** Client, contract/AMC, asset, asset category, site, service type, then organization default. Contract-linked policies are considered only for active agreements that cover the asset and are in force; exclusions apply. If a higher-priority policy has no target for the subject priority, selection continues to the next applicable policy. The chosen policy and selection reason are persisted on the tracking record.
- **Lifecycle:** Running, At Risk, Paused, Breached, Under Review, Closed, and Cancelled are persisted tracking states. Resolution enters Supervisor Review; a later confirmation/closure closes the SLA record. A linked Work Order uses the originating request's tracking and does not create a second clock.
- **Status:** Implemented in app `sla`; evidence: `docs/PHASE_6_ACCEPTANCE_REPORT.md`, `docs/integrations/sla-requests-workorders.md`, and `docs/manual-tests/PHASE_6_MANUAL_TEST.md`. The approved 24/7 calculation decision is recorded in D-047.


Related: `docs/MODULE_STATUS.md`, `docs/TRACEABILITY.md`, `docs/DOMAIN_MODEL.md`, `docs/DECISIONS.md`, `docs/blueprint/04_MODULE_RESPONSIBILITIES.md`.
Done means: requirement, feature, database, service, permission, tenant isolation, API/view, UI, browser workflow, PostgreSQL verification, audit verification, integration verification, automated + negative + regression tests (see PROJECT_SOURCE_OF_TRUTH section 5).
