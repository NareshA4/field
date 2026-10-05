# M13 Client / Requester Portal

Part of the ONE integrated FieldOps Nexus ERP. Read `docs/PROJECT_SOURCE_OF_TRUTH.md` and `docs/FIELDOPS_NEXUS_MASTER_BUSINESS_WORKFLOW.md` first.

- **Answers:** How do clients request service and follow up?
- **HPE CONFIRMED scope (section 7.2):** Raise service request, view status, attachments, scheduled visit, closure details.
- **Team owner / phase:** Purva / Phase 8
- **Authoritatively owns:** Client login, request creation, status, attachments, confirmation.
- **Consumes:** M05 workflow.
- **Provides:** Requests into M05.
- **Boundary rules:** Clients see only authorised records; no control of assignment, inventory, internal SLA configuration; closure follows the approved workflow.
- **Status:** IMPLEMENTED (Phase 8, decision D-051); automated tests `tests/test_m13_portal.py` (19); browser / mobile verification pending (final audit).
- **Where:** app `src/apps/portal/`; client UI `/app/portal/` (dashboard, requests, new, detail with visit + timeline + files + confirm/reopen); staff UI `/app/portal/accounts/`; API `/api/v1/portal/requests|assets/` (client-safe), `/api/v1/portal-accounts/` (staff); permissions `portal.request.create|view|confirm`, `portal.manage`.


Related: `docs/MODULE_STATUS.md`, `docs/TRACEABILITY.md`, `docs/DOMAIN_MODEL.md`, `docs/DECISIONS.md`, `docs/blueprint/04_MODULE_RESPONSIBILITIES.md`.
Done means: requirement, feature, database, service, permission, tenant isolation, API/view, UI, browser workflow, PostgreSQL verification, audit verification, integration verification, automated + negative + regression tests (see PROJECT_SOURCE_OF_TRUTH section 5).
