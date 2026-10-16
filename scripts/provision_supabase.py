#!/usr/bin/env python
"""One-time, idempotent provisioning of the production database on Supabase.

Creates a least-privilege role `fieldops_app` and a dedicated schema `fieldops` that it owns, so Django's tables
are NOT in the `public` schema that Supabase exposes through its REST Data API. Never drops anything.

Env: SUPABASE_ADMIN_DATABASE_URL (admin connection), FIELDOPS_APP_PASSWORD (new role's password).
"""
import os
import sys

import psycopg
from psycopg import sql

admin_url = os.environ.get("SUPABASE_ADMIN_DATABASE_URL")
app_pw = os.environ.get("FIELDOPS_APP_PASSWORD")
if not admin_url or not app_pw:
    sys.exit("Set SUPABASE_ADMIN_DATABASE_URL and FIELDOPS_APP_PASSWORD.")

with psycopg.connect(admin_url, autocommit=True) as conn:
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'fieldops_app'").fetchone()
    if exists:
        print("role fieldops_app already exists (password left unchanged)")
    else:
        conn.execute(sql.SQL("CREATE ROLE fieldops_app LOGIN NOINHERIT PASSWORD {}").format(sql.Literal(app_pw)))
        print("created role fieldops_app")
    conn.execute("GRANT fieldops_app TO CURRENT_USER")
    conn.execute("CREATE SCHEMA IF NOT EXISTS fieldops AUTHORIZATION fieldops_app")
    conn.execute("ALTER ROLE fieldops_app SET search_path = fieldops")
    conn.execute("REVOKE ALL ON SCHEMA public FROM fieldops_app")
    print("schema fieldops ready; search_path pinned for fieldops_app")
