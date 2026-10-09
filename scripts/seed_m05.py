"""Seeds Module 5 (Incident / Breakdown) demo data: incidents with severity, service impact, downtime, photo evidence
and workflow states, on demo assets. Needs the Module 1 demo organization first:

    python scripts/seed_m01.py
    python scripts/seed_m05.py

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

call_command("seed_m05_demo")
