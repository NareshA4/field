# Module 5: Regression test proof

**Module:** M05 Incident / Breakdown (failure reporting, severity, photos/files, asset linkage, downtime start/end, service impact)

**Environment:** Python 3.13.14, Django 5.2.17, pytest 9.1.1, PostgreSQL 16.15 (Docker test container; dedicated test_* DB created by pytest). Branch dev @ 3a5899c plus the M05 commits. Run date 2026-10-06.

**Scope:** Full suite on current dev plus this module, run after the latest merge (PR #6 asset-hierarchy) to prove nothing regressed.

**Command**
```bash
pytest -v --ignore=tests/test_m10_contracts.py --ignore=tests/test_m10_m11_coverage_sla.py
```

**Full suite: 203 passed, 1 failed, 204 total (2m54s).**

**Module 5 tests inside the full run: 29/29 passed.**

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

## Findings not caused by Module 5

1. `test_tenant_isolation.py::test_unscoped_is_only_used_in_trusted_modules` fails because `apps/accounts/management/commands/seed_user_roles.py` (added by another team member) calls `.unscoped()` but is not on the allow-list in `tests/test_tenant_isolation.py`. Fix: add it to the allow-list or remove the call.
2. `tests/test_m10_contracts.py` and `tests/test_m10_m11_coverage_sla.py` fail at collection on dev (`apps.contracts` is not in `INSTALLED_APPS`); excluded from this run. Owner: M10.

## Module 5 tests in the full run

| # | Test | File | Covers | Result |
|---|---|---|---|---|
| 1 | `test_report_links_asset_and_site_numbers_and_audits` | test_incidents.py | Audit record written | PASS |
| 2 | `test_service_request_gets_its_own_number_prefix` | test_incidents.py | Numbering | PASS |
| 3 | `test_invalid_values_are_refused[severity-EXTREME-invalid_severity]` | test_incidents.py | Input validation | PASS |
| 4 | `test_invalid_values_are_refused[service_impact-WHATEVER-invalid_impact]` | test_incidents.py | Input validation | PASS |
| 5 | `test_invalid_values_are_refused[kind-OTHER-invalid_kind]` | test_incidents.py | Input validation | PASS |
| 6 | `test_invalid_values_are_refused[title-ab-title_required]` | test_incidents.py | Input validation | PASS |
| 7 | `test_retired_asset_cannot_get_a_report` | test_incidents.py | Retired asset refused | PASS |
| 8 | `test_cross_tenant_asset_is_refused` | test_incidents.py | Cross-tenant asset refused | PASS |
| 9 | `test_update_only_before_approval_and_audits_changes` | test_incidents.py | Audit record written | PASS |
| 10 | `test_downtime_start_and_end_with_duration_and_audit` | test_incidents.py | Audit record written | PASS |
| 11 | `test_downtime_end_before_start_and_future_times_are_refused` | test_incidents.py | Downtime start/end rules | PASS |
| 12 | `test_downtime_cannot_change_on_a_rejected_request` | test_incidents.py | Downtime start/end rules | PASS |
| 13 | `test_photo_evidence_is_validated_stored_and_audited` | test_incidents.py | Audit record written | PASS |
| 14 | `test_disguised_executable_is_rejected` | test_incidents.py | Upload content validation | PASS |
| 15 | `test_triage_approve_path_records_history_and_blocks_invalid_jumps` | test_incidents.py | Input validation | PASS |
| 16 | `test_reject_needs_a_reason_and_system_actions_are_refused` | test_incidents.py | Reject requires a reason | PASS |
| 17 | `test_work_order_creation_is_unavailable_until_m06_is_installed` | test_incidents.py | M06 boundary (module_unavailable) | PASS |
| 18 | `test_api_report_list_retrieve_and_downtime` | test_incidents.py | Downtime start/end rules | PASS |
| 19 | `test_api_evidence_upload_and_list` | test_incidents.py | Photos / file evidence | PASS |
| 20 | `test_api_transition_requires_the_action_permission` | test_incidents.py | Workflow state machine | PASS |
| 21 | `test_api_requires_authentication` | test_incidents.py | REST API | PASS |
| 22 | `test_other_organization_sees_and_touches_nothing` | test_incidents.py | Tenant isolation | PASS |
| 23 | `test_cannot_report_against_a_foreign_asset_via_api` | test_incidents.py | Cross-tenant asset refused | PASS |
| 24 | `test_site_scoped_member_only_sees_their_site` | test_incidents.py | Site scope | PASS |
| 25 | `test_auditor_reads_but_cannot_report` | test_incidents.py | Audit record written | PASS |
| 26 | `test_incident_pages_render_and_report_form_creates_a_request` | test_incidents.py | HTML pages render | PASS |
| 27 | `test_ui_evidence_upload_and_downtime_buttons_persist` | test_incidents.py | Downtime start/end rules | PASS |
| 28 | `test_other_tenant_cannot_open_incident_pages` | test_incidents.py | HTML pages render | PASS |
| 29 | `test_m05_seed_is_idempotent_and_goes_through_services` | test_incidents.py | Demo seed is idempotent | PASS |
