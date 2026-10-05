# FieldOps Nexus

Multi-tenant Enterprise Asset, Maintenance & Field Service ERP (Django). Folder structure only; each team member adds the code for their assigned module.

```
src/apps/<app>/     one Django app per module or shared area (core, accounts, tenancy, rbac, audit, notifications, files,
                    platform_admin, ui, sites, assets, incidents, workorders, inventory, maintenance, checklists,
                    workspace, contracts, identification, portal, dashboards, sla)
src/config/         project settings and URL routing
src/templates/      HTML templates (one folder per app)
src/static/         css, js, lib
tests/              automated tests
docs/               documentation (docs/modules = one spec per module)
deploy/ scripts/    deployment config and helper scripts
```

Delete the `.gitkeep` file in a folder once it has real files.
