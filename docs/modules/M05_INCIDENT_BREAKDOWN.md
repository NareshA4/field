# M05 Incident / Breakdown

Part of the ONE integrated FieldOps Nexus ERP. Read `docs/PROJECT_SOURCE_OF_TRUTH.md` and `docs/FIELDOPS_NEXUS_MASTER_BUSINESS_WORKFLOW.md` first.

- **Answers:** Something failed. What happened and what is the service impact?
- **HPE CONFIRMED scope (section 7.2):** Failure reporting, severity, photos/files, asset linkage, downtime start/end, service impact, triage/approval.
- **Team owner / phase:** Team Lead / Phase 2
- **Authoritatively owns:** Incident/service-request workflow NEW -> TRIAGED -> APPROVED/REJECTED -> WORK ORDER CREATED -> IN SERVICE -> RESOLVED -> CONFIRMED -> CLOSED; downtime records.
- **Consumes:** M02/M03 assets, M01 sites, M13 requests.
- **Provides:** Approved requests that create/link a Work Order; downtime for M14.
- **Boundary rules:** May create/link a WO after approval; M06 owns the WO lifecycle. M05 must not become M06.
- **Status:** IMPLEMENTED on local PostgreSQL (Phase 2, D-034..D-038); browser acceptance and Supabase apply pending; awaiting Team Lead approval. See `docs/integrations/incidents-workorders.md`.


Related: `docs/MODULE_STATUS.md`, `docs/TRACEABILITY.md`, `docs/DOMAIN_MODEL.md`, `docs/DECISIONS.md`, `docs/blueprint/04_MODULE_RESPONSIBILITIES.md`.
Done means: requirement, feature, database, service, permission, tenant isolation, API/view, UI, browser workflow, PostgreSQL verification, audit verification, integration verification, automated + negative + regression tests (see PROJECT_SOURCE_OF_TRUTH section 5).
