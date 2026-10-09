# Architecture

```
Browser/API client -> Nginx (TLS, rate limit, headers) -> Gunicorn (Django 5.2 + DRF)
                                                           |-> PostgreSQL (all business data, audit trigger)
                                                           |-> Redis (cache; Celery broker)
Celery worker + beat (same image) -> Postgres / Redis / SMTP
```

## Request pipeline (tenant + RBAC)
1. `RequestIdMiddleware` assigns `X-Request-ID` (log correlation).
2. Session/JWT authenticates the user.
3. `TenantContextMiddleware` starts the request in NO_TENANT mode (tenant querysets empty), then (UI) resolves the session's active org against the user's ACTIVE memberships. API: `TenantAPIMixin.perform_authentication` does the same after DRF authentication, honouring `X-Organization`.
4. A suspended org or non-member selection yields 403 (`organization_suspended` / `not_a_member`).
5. Permission check: `rbac.services.has_permission(membership, code)` (HTML: `TenantPermissionMixin`, API: `HasOrgPermission` + `permission_map`).
6. Object access goes through tenant-scoped managers; other tenants' IDs are 404.
7. Services validate state (StateMachine), run in `transaction.atomic`, and write `AuditLog` in the same transaction.

## Layers
`views/api_views` (thin) -> `services` (rules, transactions, audit) -> `models`; reads via `selectors`. Async work in `tasks.py` (Celery) using `tenant_context(org)`.

## Platform vs tenant
Platform console (`/platform/`, `/api/v1/platform/`) runs in PLATFORM mode for `is_platform_admin` users and exposes only organization management and cross-org audit. Tenant URLs never accept platform admins without a membership.

## Frontend
Server-rendered Django templates, `shell.html` (sidebar from `ui.navigation` registry filtered by permission, org switcher, search, notification bell via HTMX), reusable `components/`. Technician/client screens are responsive pages of the same shell (Phases 3/8).

## Operations
JSON logs (stdout) with request id; `/health/live`, `/health/ready` (DB + cache); Docker multi-stage-style image runs non-root; Compose for local/staging; CI: ruff, migration drift check, `check --deploy`, tests with coverage, pip-audit, gitleaks, image build.
