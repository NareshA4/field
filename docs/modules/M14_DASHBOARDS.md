# M14 Operational Dashboards

Part of the ONE integrated FieldOps Nexus ERP. Read `docs/PROJECT_SOURCE_OF_TRUTH.md` and `docs/FIELDOPS_NEXUS_MASTER_BUSINESS_WORKFLOW.md` first.

- **Answers:** How is operations performing?
- **HPE CONFIRMED scope (section 7.2):** MTTR, MTBF, downtime, open work orders, technician utilization, SLA breaches, PM compliance, parts consumption.
- **Team owner / phase:** Team Lead / Phase 9
- **Authoritatively owns:** Read/analytics views over real transactional data with tenant, date and site filters.
- **Consumes:** M05, M06, M09, M11, M04, M02.
- **Provides:** Metrics for managers.
- **Boundary rules:** No hardcoded numbers; every metric traceable to source records.
- **Status:** IMPLEMENTED (Phase 9, decision D-052); tests `tests/test_m14_dashboards.py`; browser verification pending (final audit).
- **Where:** app `src/apps/dashboards/` (no tables): `metrics.py` (all aggregates + `KPI_DEFINITIONS`), `views.py` (`/app/dashboards/?section=&site=&from=&to=`, `/app/dashboards/my-work/`), `api_views.py` (`/api/v1/dashboards/`, `/api/v1/dashboards/<section>/`); permission `report.view` (+ the section's data permission).
- **KPI definitions (formula / source / filters):** see `KPI_DEFINITIONS` in `metrics.py`, also returned by `GET /api/v1/dashboards/`. Filters: organization (always), site (within `report.view` AND data-permission scope), date window (UTC days, default 30, max 400). Snapshot KPIs ignore the window.


Related: `docs/MODULE_STATUS.md`, `docs/TRACEABILITY.md`, `docs/DOMAIN_MODEL.md`, `docs/DECISIONS.md`, `docs/blueprint/04_MODULE_RESPONSIBILITIES.md`.
Done means: requirement, feature, database, service, permission, tenant isolation, API/view, UI, browser workflow, PostgreSQL verification, audit verification, integration verification, automated + negative + regression tests (see PROJECT_SOURCE_OF_TRUTH section 5).
