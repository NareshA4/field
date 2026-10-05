# M11 SLA & Escalation

Part of the ONE integrated FieldOps Nexus ERP. Read `docs/PROJECT_SOURCE_OF_TRUTH.md` and `docs/FIELDOPS_NEXUS_MASTER_BUSINESS_WORKFLOW.md` first.

- **Answers:** What timing applies, and was it breached?
- **HPE CONFIRMED scope (section 7.2):** Response/resolution targets by priority, automated escalation, breach tracking, supervisor alerts.
- **Team owner / phase:** Team Lead / Phase 6
- **Authoritatively owns:** SLA profiles, priority mapping, timers, pause rules, breaches, escalation rules and events.
- **Consumes:** M05/M06, M01 calendars, M10.
- **Provides:** Breach/escalation data to M14, M15 and notifications.
- **Boundary rules:** Response and resolution are separate. Exact pause states/business-hour semantics are not HPE-defined: do not present as HPE. Celery monitoring retry-safe, no duplicate escalations.
- **Status:** IMPLEMENTED (Phase 6; app `sla`, D-044; awaiting Team Lead approval). Evidence: `docs/PHASE_6_ACCEPTANCE_REPORT.md`; contract `docs/integrations/sla-requests-workorders.md`; manual guide `docs/manual-tests/PHASE_6_MANUAL_TEST.md`. Calendar: 24/7 elapsed minutes confirmed by the Team Lead (D-047); pause states per profile remain our decision (D-044).


Related: `docs/MODULE_STATUS.md`, `docs/TRACEABILITY.md`, `docs/DOMAIN_MODEL.md`, `docs/DECISIONS.md`, `docs/blueprint/04_MODULE_RESPONSIBILITIES.md`.
Done means: requirement, feature, database, service, permission, tenant isolation, API/view, UI, browser workflow, PostgreSQL verification, audit verification, integration verification, automated + negative + regression tests (see PROJECT_SOURCE_OF_TRUTH section 5).
