# FieldOps Nexus

Multi-tenant Enterprise Asset, Maintenance & Field Service ERP (HPE-PRD-2026-FOPS02) for VPD Technologies.
Django 5.2 · DRF · PostgreSQL · Redis · Celery · HTMX/Bootstrap.

## What is in this branch
Working base: login/logout, roles & permissions (RBAC), organizations & members, audit trail, notifications, platform
admin console, and **M01 Site & Location Master** (sites, buildings/zones/service areas, operating calendars, contact
hierarchy), with both UI and API. The other module folders under `src/apps/` are empty: add your module there.
Read `docs/DEV_BRANCH_START.md` for the team workflow.

## Run it (Docker, easiest)
```bash
python scripts/bootstrap_env.py                      # creates .env with random secrets (git-ignored)
docker compose up -d --build                         # app at http://localhost:8080
docker compose exec -e FIELDOPS_ALLOW_DEMO_SEED=true web python manage.py seed_dev_demo
```
Open http://localhost:8080/accounts/login/ and sign in. All demo users share the local-only password
`FieldOps-Dev-2026!`:

| Login | Role | Sees |
|---|---|---|
| `platform@fieldops.local` | Platform Super Admin | Platform console (organizations) |
| `owner@demo.local` | Org Owner | Everything in the Demo Organization |
| `admin@demo.local` | Admin | Users, roles, sites |
| `supervisor@demo.local` | Supervisor | Sites (read) and team views |
| `technician@demo.local` | Technician | Limited, own work |

Then open **Sites** (`/app/sites/`): sites, locations tree, calendars, contacts. API docs: `/api/v1/docs/`.

## Run it without Docker for the app (Python venv + Postgres container)
```bash
python -m venv .venv && .venv/Scripts/activate       # Windows; source .venv/bin/activate on Mac/Linux
pip install -r requirements.lock pytest pytest-django pytest-cov ruff
docker run -d --name fieldops-pg -e POSTGRES_USER=fieldops -e POSTGRES_PASSWORD=fieldops -e POSTGRES_DB=fieldops -p 55432:5432 postgres:16-alpine
export DJANGO_SETTINGS_MODULE=config.settings.dev DJANGO_SECRET_KEY=dev-only-secret DATABASE_URL=postgres://fieldops:fieldops@localhost:55432/fieldops PYTHONPATH=src
python manage.py migrate
python manage.py seed_dev_demo
python manage.py runserver                           # http://localhost:8000/accounts/login/
```
(PowerShell: use `$env:NAME="value"` instead of `export`.) Inviting users by email needs Redis; seeding does not.

## Tests
```bash
export DJANGO_SETTINGS_MODULE=config.settings.test DATABASE_URL=postgres://fieldops:fieldops@localhost:55432/fieldops PYTHONPATH=src
pytest -q ; ruff check src tests ; python manage.py makemigrations --check --dry-run
```
Health: `/health/live/`, `/health/ready/`. More: `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT_RULES.md`, `docs/DEPLOYMENT.md`.
