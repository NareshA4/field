# M01 Site & Location Master

Part of the ONE integrated FieldOps Nexus ERP. Read `docs/PROJECT_SOURCE_OF_TRUTH.md` and `docs/FIELDOPS_NEXUS_MASTER_BUSINESS_WORKFLOW.md` first.

- **Answers:** Where does the organization operate, and where can work happen?
- **HPE CONFIRMED scope (section 7.2):** Organizations, sites, buildings/zones, service areas, operating calendars, contact hierarchy.
- **Team owner / phase:** Vishnu / Phase 1
- **Authoritatively owns:** Site, Zone (building/zone/service area tree), OperatingCalendar + holidays, SiteContact hierarchy, site activation.
- **Consumes:** Foundation (organizations, RBAC, audit).
- **Provides:** Location context for M02 (asset site/zone), M04/M05/M06/M07 (job site), M11 (calendars), M09 (site stock).
- **Boundary rules:** Does not own assets, incidents or work orders. Deactivation is refused while active assets exist; sites/zones are never hard-deleted.
- **Status:** IMPLEMENTED (Phase 1; `apps/sites`); browser acceptance partially done, approval pending
- **Evidence so far:** Site create/edit/deactivate (browser, UI=API=DB=audit), 4-level location tree, calendars/holidays/contacts (earlier QA run). Not yet on the Supabase data: calendars, contacts, cross-tenant/site-scope browser checks, responsive sweep.

Related: `docs/MODULE_STATUS.md`, `docs/TRACEABILITY.md`, `docs/DOMAIN_MODEL.md`, `docs/DECISIONS.md`, `docs/blueprint/04_MODULE_RESPONSIBILITIES.md`, `docs/integrations/sites-assets-contracts.md`.
Done means: requirement, feature, database, service, permission, tenant isolation, API/view, UI, browser workflow, PostgreSQL verification, audit verification, integration verification, automated + negative + regression tests (see PROJECT_SOURCE_OF_TRUTH section 5).
