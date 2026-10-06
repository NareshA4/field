# Module 5: Unit test proof

**Module:** M05 Incident / Breakdown (failure reporting, severity, photos/files, asset linkage, downtime start/end, service impact)

**Environment:** Python 3.13.14, Django 5.2.17, pytest 9.1.1, PostgreSQL 16.15 (Docker test container; dedicated test_* DB created by pytest). Branch dev @ 3a5899c plus the M05 commits. Run date 2026-10-06.

**Scope:** Service layer: reporting, severity, service impact, asset linkage, downtime start/end, evidence validation, workflow state machine, M06 boundary.

**Command**
```bash
pytest tests/test_incidents.py -v   (service-level tests)
```

**Result: 17 passed, 0 failed, 17 total.**

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

Rules proven: INC-/SR- numbering, asset and site linkage, invalid severity/impact/kind/title refused, retired and cross-tenant asset refused, edits locked after approval, downtime end >= start and no future times, no downtime change on rejected requests, photo evidence validated and audited, disguised executable rejected, invalid transitions blocked, reason required to reject, system (work-order) actions refused by hand, work-order creation refuses with module_unavailable until M06 exists.
