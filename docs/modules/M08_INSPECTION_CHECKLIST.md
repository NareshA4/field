# M08 Inspection & Checklist Engine

Part of the ONE integrated FieldOps Nexus ERP. Read `docs/PROJECT_SOURCE_OF_TRUTH.md` and `docs/FIELDOPS_NEXUS_MASTER_BUSINESS_WORKFLOW.md` first.

- **Answers:** What must be checked and recorded?
- **HPE CONFIRMED scope (section 7.2):** Template-based safety, quality and maintenance checklists with mandatory fields and exception findings.
- **Team owner / phase:** Prasad / Sub-TL / Phase 3
- **Authoritatively owns:** Checklist templates/items, inspections, responses, findings, evidence, completion.
- **Consumes:** M02 assets, M06 work.
- **Provides:** Completion status for M06 closure and M04 references.
- **Boundary rules:** Required checklist completion can be a prerequisite for M06 closure.
- **Status:** IMPLEMENTED (Phase 3: app `checklists`, D-039 / D-040); automated tests on local PostgreSQL; browser acceptance DEFERRED; awaiting Team Lead approval. Evidence: `docs/PHASE_3_ACCEPTANCE_REPORT.md`; contract `docs/integrations/checklists-workorders.md`.


Related: `docs/MODULE_STATUS.md`, `docs/TRACEABILITY.md`, `docs/DOMAIN_MODEL.md`, `docs/DECISIONS.md`, `docs/blueprint/04_MODULE_RESPONSIBILITIES.md`.
Done means: requirement, feature, database, service, permission, tenant isolation, API/view, UI, browser workflow, PostgreSQL verification, audit verification, integration verification, automated + negative + regression tests (see PROJECT_SOURCE_OF_TRUTH section 5).
