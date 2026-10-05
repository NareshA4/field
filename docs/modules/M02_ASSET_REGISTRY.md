# M02 Asset Registry

Part of the ONE integrated FieldOps Nexus ERP. Read `docs/PROJECT_SOURCE_OF_TRUTH.md` and `docs/FIELDOPS_NEXUS_MASTER_BUSINESS_WORKFLOW.md` first.

- **Answers:** What physical thing do we manage?
- **HPE CONFIRMED scope (section 7.2):** Asset ID, category, model, serial number, purchase/commission dates, location, owner, warranty, status, documents.
- **Team owner / phase:** Raju / Phase 1
- **Authoritatively owns:** Asset master record, category, controlled status workflow + append-only status/location history, documents, (meters/readings: conflict C-2).
- **Consumes:** M01 (site, zone), Foundation (users as owners, files).
- **Provides:** Asset identity/status/history to M03, M04, M05, M06, M10, M12, M14, M15. Status changes by other modules only through `assets.services.change_status`.
- **Boundary rules:** One authoritative Asset model. No direct status edits. M12 owns QR; Asset Detail may only expose a convenient QR action. Warranty is a reference only (coverage = M10).
- **Status:** IMPLEMENTED (Phase 1; `apps/assets`); browser acceptance partial, approval pending
- **Evidence so far:** Asset create/edit/status/history/hierarchy verified on a throwaway stack. Not yet browser-verified on Supabase: documents, meters, filters, read-only/technician roles.

Related: `docs/MODULE_STATUS.md`, `docs/TRACEABILITY.md`, `docs/DOMAIN_MODEL.md`, `docs/DECISIONS.md`, `docs/blueprint/04_MODULE_RESPONSIBILITIES.md`, `docs/integrations/sites-assets-contracts.md`.
Done means: requirement, feature, database, service, permission, tenant isolation, API/view, UI, browser workflow, PostgreSQL verification, audit verification, integration verification, automated + negative + regression tests (see PROJECT_SOURCE_OF_TRUTH section 5).
