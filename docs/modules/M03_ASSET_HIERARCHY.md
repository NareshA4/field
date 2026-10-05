# M03 Asset Hierarchy

Part of the ONE integrated FieldOps Nexus ERP. Read `docs/PROJECT_SOURCE_OF_TRUTH.md` and `docs/FIELDOPS_NEXUS_MASTER_BUSINESS_WORKFLOW.md` first.

- **Answers:** How are assets/components physically or logically related?
- **HPE CONFIRMED scope (section 7.2):** Parent-child assemblies, components, replaceable parts, relationship tree.
- **Team owner / phase:** Veeresh / Phase 1
- **Authoritatively owns:** AssetComponent relationships (ASSEMBLY/COMPONENT/REPLACEABLE_PART), tree, cycle/depth/site/tenant integrity, relationship audit.
- **Consumes:** M02 (authoritative assets), M01 (site).
- **Provides:** Tree/ancestor context to maintenance, inspection, work-order contexts; `part_number` bridge to M09.
- **Boundary rules:** Never creates another Asset master. Same tenant and same site, no self-parent, no cycles (A->B->C->A rejected), relationship changes audited.
- **Status:** IMPLEMENTED (Phase 1; `assets/hierarchy.py`); browser acceptance partial, approval pending
- **Evidence so far:** Tree, child registration, tree via HTMX verified on a throwaway stack. Not yet browser-verified: re-parent, cycle/self-parent rejection, cross-tenant link attempts on Supabase.

Related: `docs/MODULE_STATUS.md`, `docs/TRACEABILITY.md`, `docs/DOMAIN_MODEL.md`, `docs/DECISIONS.md`, `docs/blueprint/04_MODULE_RESPONSIBILITIES.md`, `docs/integrations/sites-assets-contracts.md`.
Done means: requirement, feature, database, service, permission, tenant isolation, API/view, UI, browser workflow, PostgreSQL verification, audit verification, integration verification, automated + negative + regression tests (see PROJECT_SOURCE_OF_TRUTH section 5).
