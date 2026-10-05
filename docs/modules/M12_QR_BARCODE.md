# M12 QR / Barcode Identification

Part of the ONE integrated FieldOps Nexus ERP. Read `docs/PROJECT_SOURCE_OF_TRUTH.md` and `docs/FIELDOPS_NEXUS_MASTER_BUSINESS_WORKFLOW.md` first.

- **Answers:** How do we identify an asset physically and open it by scanning?
- **HPE CONFIRMED scope (section 7.2):** Generate/scan asset labels to open the asset profile or create a service event.
- **Team owner / phase:** Purva / Phase 8
- **Authoritatively owns:** Opaque identifiers, labels, scan resolution, authorization, approved service-event entry point.
- **Consumes:** M02 assets, M05.
- **Provides:** Asset context from a scan.
- **Boundary rules:** Identifiers opaque and non-secret, no sensitive DB ids. Does not own the incident/WO lifecycle.
- **Status:** IMPLEMENTED (Phase 8, decision D-050); automated tests `tests/test_m12_identification.py` (21); browser verification pending (final audit).
- **Where:** app `src/apps/identification/`; API `/api/v1/asset-identifiers/`, `/api/v1/scan/{resolve,report}/`; UI asset page "Labels" tab, `/app/identification/scan/`, `/app/s/<token>/`, printable label, SVG/PNG downloads; permissions `qr.view|qr.generate` (+ `asset.view` / `incident.create` for scanning).


Related: `docs/MODULE_STATUS.md`, `docs/TRACEABILITY.md`, `docs/DOMAIN_MODEL.md`, `docs/DECISIONS.md`, `docs/blueprint/04_MODULE_RESPONSIBILITIES.md`.
Done means: requirement, feature, database, service, permission, tenant isolation, API/view, UI, browser workflow, PostgreSQL verification, audit verification, integration verification, automated + negative + regression tests (see PROJECT_SOURCE_OF_TRUTH section 5).
