# M07 Technician Workspace

Part of the ONE integrated FieldOps Nexus ERP. Read `docs/PROJECT_SOURCE_OF_TRUTH.md` and `docs/FIELDOPS_NEXUS_MASTER_BUSINESS_WORKFLOW.md` first.

- **Answers:** How does the technician execute assigned work?
- **HPE CONFIRMED scope (section 7.2):** Assigned jobs, route/site details, checklist, notes, attachments, parts usage, time entry, completion evidence.
- **Team owner / phase:** Prasad / Sub-TL / Phase 3
- **Authoritatively owns:** Technician-facing experience only: My Jobs, job detail, start/hold/resume/complete actions that call M06.
- **Consumes:** M06 (authoritative WO), M08, M09, M01, M02.
- **Provides:** Execution evidence into M06/M08/M09.
- **Boundary rules:** Must not become a second Work Order lifecycle.
- **Status:** IMPLEMENTED (Phase 3: app `workspace`, D-041); automated tests on local PostgreSQL; browser/responsive acceptance DEFERRED; awaiting Team Lead approval. Evidence: `docs/PHASE_3_ACCEPTANCE_REPORT.md`.


Related: `docs/MODULE_STATUS.md`, `docs/TRACEABILITY.md`, `docs/DOMAIN_MODEL.md`, `docs/DECISIONS.md`, `docs/blueprint/04_MODULE_RESPONSIBILITIES.md`.
Done means: requirement, feature, database, service, permission, tenant isolation, API/view, UI, browser workflow, PostgreSQL verification, audit verification, integration verification, automated + negative + regression tests (see PROJECT_SOURCE_OF_TRUTH section 5).
