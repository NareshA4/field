# Module 1: Unit test proof

**Module:** M01 Site & Location Master (organizations, sites, buildings/zones, service areas, operating calendars, contact hierarchy)

**Environment:** Python 3.13.14, Django 5.2.17, pytest 9.1.1, PostgreSQL 16.15 (Docker test container; dedicated test_* DB created by pytest). Branch dev @ 3a5899c plus the M05 commits. Run date 2026-10-06.

**Scope:** Service layer rules, DB constraints, validation, state transition, zone hierarchy, calendars, contacts (no HTTP).

**Command**
```bash
pytest tests/test_sites.py -v
```

**Result: 19 passed, 0 failed, 19 total.**

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

Rules proven: site code normalisation and case-insensitive uniqueness per organization, invalid code/timezone refused, deactivation needs a reason (state transition), inactive site rejects new locations, no hard delete of referenced sites, zone tree/cycle/depth/same-site-and-org parent rules, calendar and holiday rules, contact escalation order.
