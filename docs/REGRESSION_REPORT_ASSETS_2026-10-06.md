# Regression Test Report — Assets Module

**Date:** 2026-10-06
**Module:** `src/apps/assets` (tests: `tests/test_assets.py`)
**Environment:** Docker Compose stack (`fieldops_nexus_internal-web-1`), Django 5.2.17, pytest 9.1.1, PostgreSQL (container `db-1`)

## Scope

Regression coverage for the Assets module:

- Asset CRUD, validation, duplicate/serial rules, commissioning dates
- Cross-tenant / cross-site / inactive-site / inactive-category rejection
- Asset update rules, location history, status-change guards
- Lifecycle state machine (complete chain, invalid transitions, terminal states, reason required)
- Status/location history immutability
- Category custom attributes (valid/missing/invalid choice/unknown key)
- Meter lifecycle & monotonic readings
- Hierarchy (add/tree build, self-reference, circular dependency, cross-site)
- RBAC site-scoped views, tenant isolation
- API: list/filters, create, transition endpoint, meter create/reading, patch guard, cross-tenant access
- Transaction rollback on failed asset creation

## Result

```
45 passed in 63.31s
```

- **Passed:** 45
- **Failed:** 0
- **Errors:** 0
- **Skipped:** 0

All tests in `tests/test_assets.py` passed (verbose list verified per-test).

## Additional Checks

| Check | Command | Result |
|---|---|---|
| Pending migrations | `python manage.py makemigrations --check --dry-run` | ✅ No changes detected |
| Ruff lint | — | ⚠️ Not executed (ruff not installed in web container / local env) |

## Notable Observation

An intermediate run reported 2 failures in the negative hierarchy tests
(`test_neg_hierarchy_self_reference_rejected`, `test_neg_hierarchy_cross_site_linking_rejected`),
caused by an expected-vs-actual error-code mismatch (`self_reference` vs `self_parent`,
`cross_site_hierarchy` vs `site_mismatch`). After the test file was updated
(`tests/test_assets.py`, mtime 06-10-2026 12:52), the same suite ran fully green —
suggesting the earlier failure was from a mid-edit version of the test file.
Recommend re-running the suite once more after any further changes to confirm stability.

## How to Reproduce

```bash
docker compose exec web python -m pytest tests/test_assets.py -q
docker compose exec web python manage.py makemigrations --check --dry-run
```
