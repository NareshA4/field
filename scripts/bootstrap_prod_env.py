#!/usr/bin/env python
"""Writes a git-ignored `.env.prod` for the Supabase production database with freshly generated secrets.

Env in: SUPABASE_PROJECT_REF, SUPABASE_ADMIN_PASSWORD (the `postgres` password you set in the Supabase dashboard).
Refuses to overwrite an existing .env.prod. Secret values are never printed.
"""
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import quote

target = Path(__file__).resolve().parent.parent / ".env.prod"
if target.exists():
    sys.exit(".env.prod already exists; refusing to overwrite.")
ref, admin_pw = os.environ.get("SUPABASE_PROJECT_REF"), os.environ.get("SUPABASE_ADMIN_PASSWORD")
if not ref or not admin_pw:
    sys.exit("Set SUPABASE_PROJECT_REF and SUPABASE_ADMIN_PASSWORD.")

app_pw = secrets.token_urlsafe(28)
host = f"db.{ref}.supabase.co"
lines = [
    "# PRODUCTION secrets. Git-ignored. Never commit or paste into chat.",
    "DJANGO_SETTINGS_MODULE=config.settings.prod",
    f"DJANGO_SECRET_KEY={secrets.token_urlsafe(64)}",
    f"DATABASE_URL=postgres://fieldops_app:{quote(app_pw, safe='')}@{host}:5432/postgres?sslmode=require",
    f"SUPABASE_ADMIN_DATABASE_URL=postgres://postgres:{quote(admin_pw, safe='')}@{host}:5432/postgres?sslmode=require",
    f"FIELDOPS_APP_PASSWORD={app_pw}",
    f"SUPABASE_PROJECT_REF={ref}",
    "DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1",
    "DJANGO_SECURE_SSL_REDIRECT=false",
]
target.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"Wrote {target.name} (git-ignored).")
