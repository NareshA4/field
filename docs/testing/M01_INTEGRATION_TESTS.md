# Module 1: Integration test proof

**Module:** M01 Site & Location Master (organizations, sites, buildings/zones, service areas, operating calendars, contact hierarchy)

**Environment:** Python 3.13.14, Django 5.2.17, pytest 9.1.1, PostgreSQL 16.15 (Docker test container; dedicated test_* DB created by pytest). Branch dev @ 3a5899c plus the M05 commits. Run date 2026-10-06.

**Scope:** REST API through the real stack (auth, tenancy middleware, RBAC, service, DB, audit) and HTML pages per role/tab.

**Command**
```bash
pytest tests/test_sites.py tests/test_sites_ui.py -v
```

**Result: 26 passed, 0 failed, 26 total.**

| # | Test | File | Covers | Result |
|---|---|---|---|---|
| 1 | `test_api_site_crud_persists_and_audits` | test_sites.py | Audit record written | PASS |
| 2 | `test_api_site_validation_and_duplicates` | test_sites.py | REST API | PASS |
| 3 | `test_api_site_list_filter_search_pagination` | test_sites.py | REST API | PASS |
| 4 | `test_api_rbac_matrix_for_sites` | test_sites.py | Role/permission matrix | PASS |
| 5 | `test_api_header_for_non_member_org_is_rejected` | test_sites.py | Organization header selection | PASS |
| 6 | `test_api_zone_flow_and_tree` | test_sites.py | Zones / buildings / service areas | PASS |
| 7 | `test_api_calendar_holiday_contact_flow` | test_sites.py | Operating calendars / holidays | PASS |
| 8 | `test_cross_tenant_idor_for_every_m01_route` | test_sites.py | Cross-tenant access (IDOR) denied | PASS |
| 9 | `test_zone_with_foreign_parent_via_api_is_not_found` | test_sites.py | Zones / buildings / service areas | PASS |
| 10 | `test_malformed_ids_do_not_crash` | test_sites.py | Malformed id handling (no 500) | PASS |
| 11 | `test_escalation_order_boundaries_in_the_service` | test_sites.py | Contact escalation hierarchy | PASS |
| 12 | `test_escalation_order_api_returns_4xx_never_500` | test_sites.py | Contact escalation hierarchy | PASS |
| 13 | `test_escalation_order_ui_shows_a_validation_message` | test_sites.py | Contact escalation hierarchy | PASS |
| 14 | `test_owner_site_detail_tabs_render[overview]` | test_sites_ui.py | Site master rules | PASS |
| 15 | `test_owner_site_detail_tabs_render[locations]` | test_sites_ui.py | Site master rules | PASS |
| 16 | `test_owner_site_detail_tabs_render[calendars]` | test_sites_ui.py | Operating calendars / holidays | PASS |
| 17 | `test_owner_site_detail_tabs_render[contacts]` | test_sites_ui.py | Contact hierarchy | PASS |
| 18 | `test_owner_site_detail_tabs_render[assets]` | test_sites_ui.py | Site master rules | PASS |
| 19 | `test_technician_site_detail_tabs_render[overview]` | test_sites_ui.py | Site master rules | PASS |
| 20 | `test_technician_site_detail_tabs_render[locations]` | test_sites_ui.py | Site master rules | PASS |
| 21 | `test_technician_site_detail_tabs_render[calendars]` | test_sites_ui.py | Operating calendars / holidays | PASS |
| 22 | `test_technician_site_detail_tabs_render[contacts]` | test_sites_ui.py | Contact hierarchy | PASS |
| 23 | `test_technician_site_detail_tabs_render[assets]` | test_sites_ui.py | Site master rules | PASS |
| 24 | `test_owner_site_pages_render[/app/sites/]` | test_sites_ui.py | HTML pages render | PASS |
| 25 | `test_owner_site_pages_render[/app/sites/new/]` | test_sites_ui.py | HTML pages render | PASS |
| 26 | `test_site_edit_and_zone_create_pages_render` | test_sites_ui.py | Zones / buildings / service areas | PASS |

Rules proven: API CRUD persists and audits, validation/duplicate errors are 4xx (never 500), role matrix enforced by the backend, organization header for a non-member org rejected, cross-tenant IDOR returns 404 on every M01 route, foreign parents and malformed ids are safe, all site pages render for owner and technician.
