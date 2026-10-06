# Module 1: Regression test proof

**Module:** M01 Site & Location Master (organizations, sites, buildings/zones, service areas, operating calendars, contact hierarchy)

**Environment:** Python 3.13.14, Django 5.2.17, pytest 9.1.1, PostgreSQL 16.15 (Docker test container; dedicated test_* DB created by pytest). Branch dev @ 3a5899c plus the M05 commits. Run date 2026-10-06.

**Scope:** Full suite on current dev plus this module, run after the latest merge (PR #6 asset-hierarchy) to prove nothing regressed.

**Command**
```bash
pytest -v --ignore=tests/test_m10_contracts.py --ignore=tests/test_m10_m11_coverage_sla.py
```

**Full suite: 203 passed, 1 failed, 204 total (2m54s).**

**Module 1 tests inside the full run: 45/45 passed.**

## Per-file results

| File | Passed | Failed |
|---|---|---|
| test_audit.py | 5 | 0 |
| test_core.py | 14 | 0 |
| test_incidents.py | 29 | 0 |
| test_login_integration.py | 18 | 0 |
| test_onboarding_auth.py | 12 | 0 |
| test_rbac.py | 17 | 0 |
| test_sites.py | 32 | 0 |
| test_sites_ui.py | 13 | 0 |
| test_tenant_isolation.py | 17 | 1 |
| test_ui.py | 29 | 0 |
| test_ui_actions.py | 17 | 0 |

## Findings not caused by Module 1

1. `test_tenant_isolation.py::test_unscoped_is_only_used_in_trusted_modules` fails because `apps/accounts/management/commands/seed_user_roles.py` (added by another team member) calls `.unscoped()` but is not on the allow-list in `tests/test_tenant_isolation.py`. Fix: add it to the allow-list or remove the call.
2. `tests/test_m10_contracts.py` and `tests/test_m10_m11_coverage_sla.py` fail at collection on dev (`apps.contracts` is not in `INSTALLED_APPS`); excluded from this run. Owner: M10.

## Module 1 tests in the full run

| # | Test | File | Covers | Result |
|---|---|---|---|---|
| 1 | `test_create_site_normalises_code_and_audits` | test_sites.py | Audit record written | PASS |
| 2 | `test_duplicate_site_code_is_case_insensitive_per_org_but_allowed_across_orgs` | test_sites.py | Site code rules | PASS |
| 3 | `test_site_code_unique_constraint_in_database` | test_sites.py | Site code rules | PASS |
| 4 | `test_invalid_site_code_rejected[]` | test_sites.py | Site code rules | PASS |
| 5 | `test_invalid_site_code_rejected[has space]` | test_sites.py | Site code rules | PASS |
| 6 | `test_invalid_site_code_rejected[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx]` | test_sites.py | Site code rules | PASS |
| 7 | `test_invalid_site_code_rejected[-lead]` | test_sites.py | Site code rules | PASS |
| 8 | `test_invalid_timezone_rejected` | test_sites.py | Zones / buildings / service areas | PASS |
| 9 | `test_deactivation_needs_reason_and_is_a_state_transition` | test_sites.py | Workflow state machine | PASS |
| 10 | `test_inactive_site_rejects_new_locations_and_calendars` | test_sites.py | Operating calendars / holidays | PASS |
| 11 | `test_sites_are_never_hard_deleted_when_referenced` | test_sites.py | Site master rules | PASS |
| 12 | `test_zone_hierarchy_and_tree` | test_sites.py | Zones / buildings / service areas | PASS |
| 13 | `test_zone_cycle_is_rejected` | test_sites.py | Zone cycle prevention | PASS |
| 14 | `test_zone_parent_must_be_same_site_and_org` | test_sites.py | Zones / buildings / service areas | PASS |
| 15 | `test_building_must_be_top_level_and_depth_is_limited` | test_sites.py | HTML pages / buttons | PASS |
| 16 | `test_zone_duplicate_names_and_codes` | test_sites.py | Zones / buildings / service areas | PASS |
| 17 | `test_calendar_rules` | test_sites.py | Operating calendars / holidays | PASS |
| 18 | `test_calendar_holidays` | test_sites.py | Operating calendars / holidays | PASS |
| 19 | `test_contact_escalation_hierarchy` | test_sites.py | Contact escalation hierarchy | PASS |
| 20 | `test_api_site_crud_persists_and_audits` | test_sites.py | Audit record written | PASS |
| 21 | `test_api_site_validation_and_duplicates` | test_sites.py | REST API | PASS |
| 22 | `test_api_site_list_filter_search_pagination` | test_sites.py | REST API | PASS |
| 23 | `test_api_rbac_matrix_for_sites` | test_sites.py | Role/permission matrix | PASS |
| 24 | `test_api_header_for_non_member_org_is_rejected` | test_sites.py | Organization header selection | PASS |
| 25 | `test_api_zone_flow_and_tree` | test_sites.py | Zones / buildings / service areas | PASS |
| 26 | `test_api_calendar_holiday_contact_flow` | test_sites.py | Operating calendars / holidays | PASS |
| 27 | `test_cross_tenant_idor_for_every_m01_route` | test_sites.py | Cross-tenant access (IDOR) denied | PASS |
| 28 | `test_zone_with_foreign_parent_via_api_is_not_found` | test_sites.py | Zones / buildings / service areas | PASS |
| 29 | `test_malformed_ids_do_not_crash` | test_sites.py | Malformed id handling (no 500) | PASS |
| 30 | `test_escalation_order_boundaries_in_the_service` | test_sites.py | Contact escalation hierarchy | PASS |
| 31 | `test_escalation_order_api_returns_4xx_never_500` | test_sites.py | Contact escalation hierarchy | PASS |
| 32 | `test_escalation_order_ui_shows_a_validation_message` | test_sites.py | Contact escalation hierarchy | PASS |
| 33 | `test_owner_site_detail_tabs_render[overview]` | test_sites_ui.py | Site master rules | PASS |
| 34 | `test_owner_site_detail_tabs_render[locations]` | test_sites_ui.py | Site master rules | PASS |
| 35 | `test_owner_site_detail_tabs_render[calendars]` | test_sites_ui.py | Operating calendars / holidays | PASS |
| 36 | `test_owner_site_detail_tabs_render[contacts]` | test_sites_ui.py | Contact hierarchy | PASS |
| 37 | `test_owner_site_detail_tabs_render[assets]` | test_sites_ui.py | Site master rules | PASS |
| 38 | `test_technician_site_detail_tabs_render[overview]` | test_sites_ui.py | Site master rules | PASS |
| 39 | `test_technician_site_detail_tabs_render[locations]` | test_sites_ui.py | Site master rules | PASS |
| 40 | `test_technician_site_detail_tabs_render[calendars]` | test_sites_ui.py | Operating calendars / holidays | PASS |
| 41 | `test_technician_site_detail_tabs_render[contacts]` | test_sites_ui.py | Contact hierarchy | PASS |
| 42 | `test_technician_site_detail_tabs_render[assets]` | test_sites_ui.py | Site master rules | PASS |
| 43 | `test_owner_site_pages_render[/app/sites/]` | test_sites_ui.py | HTML pages render | PASS |
| 44 | `test_owner_site_pages_render[/app/sites/new/]` | test_sites_ui.py | HTML pages render | PASS |
| 45 | `test_site_edit_and_zone_create_pages_render` | test_sites_ui.py | Zones / buildings / service areas | PASS |
