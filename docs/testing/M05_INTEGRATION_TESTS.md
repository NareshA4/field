# Module 5: Integration test proof

**Module:** M05 Incident / Breakdown (failure reporting, severity, photos/files, asset linkage, downtime start/end, service impact)

**Environment:** Python 3.13.14, Django 5.2.17, pytest 9.1.1, PostgreSQL 16.15 (Docker test container; dedicated test_* DB created by pytest). Branch dev @ 3a5899c plus the M05 commits. Run date 2026-10-06.

**Scope:** REST API and HTML pages through auth, tenancy, RBAC, services, DB and audit; tenant isolation; site scope; demo seed.

**Command**
```bash
pytest tests/test_incidents.py -v   (API / UI / RBAC / tenant / seed tests)
```

**Result: 12 passed, 0 failed, 12 total.**

| # | Test | File | Covers | Result |
|---|---|---|---|---|
| 1 | `test_api_report_list_retrieve_and_downtime` | test_incidents.py | Downtime start/end rules | PASS |
| 2 | `test_api_evidence_upload_and_list` | test_incidents.py | Photos / file evidence | PASS |
| 3 | `test_api_transition_requires_the_action_permission` | test_incidents.py | Workflow state machine | PASS |
| 4 | `test_api_requires_authentication` | test_incidents.py | REST API | PASS |
| 5 | `test_other_organization_sees_and_touches_nothing` | test_incidents.py | Tenant isolation | PASS |
| 6 | `test_cannot_report_against_a_foreign_asset_via_api` | test_incidents.py | Cross-tenant asset refused | PASS |
| 7 | `test_site_scoped_member_only_sees_their_site` | test_incidents.py | Site scope | PASS |
| 8 | `test_auditor_reads_but_cannot_report` | test_incidents.py | Audit record written | PASS |
| 9 | `test_incident_pages_render_and_report_form_creates_a_request` | test_incidents.py | HTML pages render | PASS |
| 10 | `test_ui_evidence_upload_and_downtime_buttons_persist` | test_incidents.py | Downtime start/end rules | PASS |
| 11 | `test_other_tenant_cannot_open_incident_pages` | test_incidents.py | HTML pages render | PASS |
| 12 | `test_m05_seed_is_idempotent_and_goes_through_services` | test_incidents.py | Demo seed is idempotent | PASS |

Rules proven: report/list/retrieve/downtime/evidence over the API, transition needs the action permission (403 otherwise), unauthenticated denied, another organization gets 404 on every endpoint and page, foreign asset cannot be reported, site-scoped member sees only their site, auditor reads but cannot report, UI report form / evidence upload / downtime buttons persist and audit, seed_m05_demo is idempotent.
