# M10 Warranty / AMC / Contract

Part of the ONE integrated FieldOps Nexus ERP. Read `docs/PROJECT_SOURCE_OF_TRUTH.md` and `docs/FIELDOPS_NEXUS_MASTER_BUSINESS_WORKFLOW.md` first.

- **Answers:** What coverage applies to this asset/service?
- **HPE CONFIRMED scope (section 7.2):** Coverage terms, provider, SLA, exclusions, expiry, renewal alerts, eligible claim validation.
- **Team owner / phase:** Naresh / Phase 7
- **Authoritatively owns:** Warranty, AMC, service contracts, provider, terms, exclusions, expiry, renewal, eligibility.
- **Consumes:** M02 assets.
- **Provides:** Coverage context to M05/M06/M11 and alerts.
- **Boundary rules:** Provides coverage; M11 owns SLA behaviour. `Asset.warranty_ref` is only a pointer today.
- **Status:** IMPLEMENTED (Phase 7, decision D-049); automated tests `tests/test_m10_contracts.py` (26); browser verification pending (final audit).
- **Where:** app `src/apps/contracts/`; API `/api/v1/coverage-agreements/`, `/contract-providers/`, `/coverage/` (live answer), `/coverage-checks/` (persisted work-order decision); UI `/app/contracts/{agreements,expiry,providers}/`; asset page "Coverage" tab; work-order page coverage panel (HTMX fragments `/app/contracts/assets|work-orders/<id>/panel/`); permissions `contract.view|create|update|check`; Celery `send_renewal_alerts` (6-hourly).


Related: `docs/MODULE_STATUS.md`, `docs/TRACEABILITY.md`, `docs/DOMAIN_MODEL.md`, `docs/DECISIONS.md`, `docs/blueprint/04_MODULE_RESPONSIBILITIES.md`.
Done means: requirement, feature, database, service, permission, tenant isolation, API/view, UI, browser workflow, PostgreSQL verification, audit verification, integration verification, automated + negative + regression tests (see PROJECT_SOURCE_OF_TRUTH section 5).
