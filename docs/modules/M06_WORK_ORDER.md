# M06 Work Order Management

Part of the ONE integrated FieldOps Nexus ERP. Read `docs/PROJECT_SOURCE_OF_TRUTH.md` and `docs/FIELDOPS_NEXUS_MASTER_BUSINESS_WORKFLOW.md` first.

- **Answers:** How is work planned and executed?
- **HPE CONFIRMED scope (section 7.2):** Create, plan, prioritize, assign, dispatch, pause, complete, close with labor/material/time capture.
- **Team owner / phase:** Team Lead / Phase 2
- **Authoritatively owns:** The Work Order lifecycle DRAFT -> PLANNED -> ASSIGNED -> DISPATCHED -> IN PROGRESS -> ON HOLD -> COMPLETED -> SUPERVISOR REVIEW -> CLOSED; assignment rules; closure rules.
- **Consumes:** M05, M04, manual creation; M08 checklists; M09 parts.
- **Provides:** Authoritative execution contract for M07, M09, M11, M15.
- **Boundary rules:** Closure requires checklist completion, resolution notes, evidence per work type. Never DRAFT -> CLOSED. Invalid technician allocation prevented (inactive/conflicting).
- **Status:** IMPLEMENTED on local PostgreSQL (Phase 2, D-034..D-038); browser acceptance and Supabase apply pending; awaiting Team Lead approval. See `docs/integrations/incidents-workorders.md`.


Related: `docs/MODULE_STATUS.md`, `docs/TRACEABILITY.md`, `docs/DOMAIN_MODEL.md`, `docs/DECISIONS.md`, `docs/blueprint/04_MODULE_RESPONSIBILITIES.md`.
Done means: requirement, feature, database, service, permission, tenant isolation, API/view, UI, browser workflow, PostgreSQL verification, audit verification, integration verification, automated + negative + regression tests (see PROJECT_SOURCE_OF_TRUTH section 5).
