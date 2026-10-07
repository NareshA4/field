# Project source of truth (read this first, every session)

The repository is the project's memory, not any chat. Before modifying any module read, in order: `docs/DEVELOPMENT_RULES.md`, this file, `docs/FIELDOPS_NEXUS_MASTER_BUSINESS_WORKFLOW.md`, `docs/DECISIONS.md`, `docs/MODULE_STATUS.md`, `docs/TRACEABILITY.md`, `docs/DOMAIN_MODEL.md`, `docs/DEVELOPMENT_RULES.md`, then only the relevant `docs/modules/Mxx_*.md` and blueprint files. Use `docs/PROJECT_MAP.md`, symbol search and targeted reads; do not load the whole repository.

## 1. Source priority (Team Lead directive 2026-10-03, D-032)
1. Explicit HPE PRD requirement
2. Final approved master business/workflow documentation (`FIELDOPS_NEXUS_MASTER_BUSINESS_WORKFLOW.md`, `docs/modules/`)
3. Explicitly recorded Team Lead decision
4. Documented implementation decision/assumption (`docs/DECISIONS.md`; there is no separate `assumptions.md`, DECISIONS.md plays that role)
5. Existing implementation, only if it does not contradict 1-4
6. General engineering assumptions
`docs/blueprint/` is working source material beneath level 2: it informs decisions but never overrides the master documents.
Never silently invent a requirement. Where HPE is silent label it: HPE CONFIRMED / OUR IMPLEMENTATION DECISION / CLARIFICATION REQUIRED / EXAMPLE. If two documents conflict: stop, identify the exact conflict, compare the statements, prefer the higher-priority source, record the resolution in DECISIONS.md, never choose silently. Conflicts that need the Team Lead are listed in section 8 below and not resolved by an agent.

## 2. Golden rules
One integrated multi-organization ERP; one account per person; one authoritative Asset, Site and WorkOrder model; a module may trigger/consume/display/link another module but never take over its authoritative responsibility. Request path: UI -> endpoint -> authentication -> RBAC -> tenant/site scope -> service rule -> transaction -> PostgreSQL -> audit -> notification/background -> UI refresh.

## 3. Critical rules (never forget)
1 no duplicate authoritative models; 2 never bypass tenant isolation; 3 never rely only on frontend permissions; 4 no uncontrolled state changes; 5 never fake success; 6 never hardcode dashboard metrics; 7 never fake persistence; 8 never silently invent HPE requirements; 9 never turn an implementation decision into an HPE requirement; 10 M05 must not become M06; 11 M07 must not become a second WO system; 12 M09 is not `quantity -= N`; 13 M12 never exposes sensitive ids; 14 M14 never uses fake data; 15 M15 audit must come from real operations; 16 no completion claim without evidence; 17 never skip browser verification; 18 never skip database verification; 19 never skip negative/security testing; 20 never start the next phase with unresolved Critical/High defects.

## 4. Module ownership and dependencies
See `FIELDOPS_NEXUS_MASTER_BUSINESS_WORKFLOW.md` sections 4, 8, 9, 11 and the per-module files in `docs/modules/`.

## 5. Definition of done (a module is enterprise-done only when all pass)
Requirement -> feature -> database -> backend service -> permission -> tenant isolation -> API/view -> UI -> browser workflow -> PostgreSQL verification -> audit verification -> integration verification -> automated tests -> negative tests -> regression tests. "UI completed", "API returns 200", "tests pass" or "the developer says completed" are never sufficient evidence.

## 6. Verification rules
- Browser: for every completed module actually open pages, log in, click, fill, submit, navigate, search, filter, edit, deactivate/delete where allowed, refresh, navigate away, return, verify persistence. Do not infer browser coverage from backend tests, nor M01/M03 coverage from M02 testing. Phase 1 must be tested as a real M01 -> M02 -> M03 dependency flow; check Browser -> API -> PostgreSQL for representative operations; also RBAC, IDOR, tenant isolation, site scope, invalid data/transitions, cross-tenant references, responsive UI, console/network errors, 403/404/500 behaviour.
- Database: verify organization_id, foreign keys, site/asset/relationship ids, state values, timestamps, audit rows; look for orphans, cross-tenant relations, missing history, duplicate relationships, UI-only state.
- QA coverage per phase: unit, API, integration, RBAC, database, UI/responsive, security, performance, regression.
- Test database: see conflict C-1 below before choosing where tests/acceptance data live.

## 7. Team Lead mindset checklist (ask for every module)
Business (problem, users, who creates/edits/approves/executes/closes, owned/consumed/provided records); UI (list/create/edit/detail/every button/action/error state); backend (API/view, validation, RBAC, tenant, site scope, domain rules, transitions); database (PostgreSQL, FKs, uniques, indexes, migrations, transactions, rollback); integration (who starts it, who consumes it, relationship persisted, one authoritative record, contract clear); audit (create/update/approval/transition/closure/evidence/export); testing (unit/API/negative/RBAC/tenant/integration/browser E2E/database/regression).
If something is ambiguous do not guess: is it HPE? in the master doc? a Team Lead decision? a documented assumption? does an owner already exist? would it duplicate responsibility? If it materially affects architecture, security, data model, workflow, tenant isolation or irreversible behaviour: STOP AND ASK the Team Lead. Small reversible details: choose sensibly and record in DECISIONS.md.

## 5b. Keeping documentation synchronized
When a major decision changes update DECISIONS.md, MODULE_STATUS.md, TRACEABILITY.md, DOMAIN_MODEL.md, the master workflow and the module file. Never leave the repository describing an old architecture.

## 8. Open documentation conflicts (need Team Lead decision; not resolved by an agent)
- **C-1 Test database. TEAM LEAD DECISION 2026-10-03 (chat): browser acceptance for Phase 1 may and should run against Supabase (schema `fieldops`, persistent QA data); pytest and fresh-DB migration tests stay on local Docker Postgres. Directive Part 32 is superseded for this purpose; docs/DEVELOPMENT_RULES.md rule 8 still forbids pytest against Supabase.** Original conflict: The master directive (Part 32) says: never use a shared remote production database as the test database; use a dedicated local/QA PostgreSQL. A later explicit Team Lead instruction (D-031, 2026-10-03) says persistent test organizations/data and each phase's migrations live in the Supabase `fieldops` schema, with local PostgreSQL only for pytest/fresh-DB tests. the rule that tests never use Supabase also says tests never point at Supabase. Current practice follows D-031 for browser/acceptance data and keeps pytest on local Docker Postgres. CLARIFICATION REQUIRED: confirm whether browser acceptance may run against Supabase, or restore local/QA only.
- **C-2 Meters ownership (M02 vs M03).** The master directive (Parts 8, 11) assigns meters and meter readings to M03. HPE lists `AssetMeter` as a core entity without assigning it to a module; the Phase 1 blueprint (`04_MODULE_RESPONSIBILITIES.md`) and the implementation put meters/readings in M02 (app `assets`, `/api/v1/meters/`). Same Django app, same API group (HPE `Assets: /assets/, /assets/{id}/history/, /meters/`). CLARIFICATION REQUIRED: keep as is (module-ownership label only) or relabel.
- **C-3 Source-priority list.** `docs/DEVELOPMENT_RULES.md` previously ranked HPE > blueprint > repo > agent ideas; the master directive ranks HPE > master workflow > Team Lead decisions > documented decisions > implementation > general assumptions. Resolved by the newer explicit directive and recorded as D-032 (docs/DEVELOPMENT_RULES.md updated); noted here for transparency.
- **C-4 `assumptions.md`** is referenced by the directive but does not exist; `docs/DECISIONS.md` serves that role (recorded, not a conflict of substance).

- **Update 2026-10-04:** the D-035 asset-status rule is now decided and implemented (D-058: work order start -> UNDER_MAINTENANCE, close -> ACTIVE, never overwriting OUT_OF_SERVICE / terminal / manually set states). Items reviewed and intentionally not built are listed in D-061.
