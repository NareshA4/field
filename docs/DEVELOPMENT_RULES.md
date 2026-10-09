# Development rules

Authoritative short form is `docs/DEVELOPMENT_RULES.md`. Extra detail:

- **Git:** `main` (UAT-approved), `staging` (review builds), `feature/*`, `bugfix/*`. PRs only into main/staging. Meaningful commit messages (functional change, requirement ID). No history rewriting after a 15-day checkpoint. Tag each review build.
- **Migrations:** one app owns its tables; never edit an applied migration; new change = new migration; never delete migrations. Check: `makemigrations --check --dry-run`.
- **Tests:** dedicated Postgres; every sensitive endpoint gets allowed role / wrong role / unauthenticated / wrong org / wrong site-object / invalid state tests. Add IDOR tests for every new detail route.
- **Authentication/integration test rules (from the 2026-10-03 incident):** every auth test uses a CSRF-enforcing client (`Client(enforce_csrf_checks=True)` with GET-then-POST and Origin), the production PBKDF2 hasher, accounts created by the *real* creation path (management command / service), at least two users sharing one IP, and a simulated proxy (`REMOTE_ADDR` = proxy, `X-Forwarded-For` = client). Lockout tests must assert the stored identity, the response (429 + page) and that **other users stay unlocked**. A phase is not "verified" until a real browser login has been performed against the Docker stack.
- **Operator commands:** never put passwords inside double-quoted PowerShell strings or command-line arguments (see D-024).
- **Security:** no secrets in Git (`.env` ignored; `gitleaks` in CI); uploads only via `files.services.attach`; no `|safe`/`mark_safe` on user input; redirects only to relative URLs.
- **API:** `/api/v1/`, `TenantAPIMixin` + `permission_map`, pagination via `StandardPagination`, errors via the envelope, OpenAPI via drf-spectacular annotations.
- **UI:** new pages extend `shell.html`, reuse `components/`, register nav in `<app>/navigation.py`; state-changing controls use POST + CSRF + `data-confirm` when destructive.
- **Sample data:** realistic simulated data via management commands only for demos; never a substitute for working logic.
