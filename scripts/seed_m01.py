"""Seeds Module 1 (Site & Location Master) demo data: platform admin, demo organization, role users, sites, zones,
calendars and contacts. Run from the repository root after `python manage.py migrate`:

    python scripts/seed_m01.py

Uses the real service layer (same as the UI). Local / test environments only (needs DEBUG or
FIELDOPS_ALLOW_DEMO_SEED=true). Safe to re-run."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django  # noqa: E402

django.setup()
from django.core.management import call_command  # noqa: E402

call_command("seed_dev_demo")
