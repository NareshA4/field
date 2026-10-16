#!/usr/bin/env python
"""Least-privilege runtime role for the production database (audit finding S-2).

Problem: when the Django runtime role OWNS the tables, it can run ``TRUNCATE`` (a table owner always may), and TRUNCATE
does not fire the append-only row trigger on ``audit_auditlog``. Fix: split the roles.

  * ``fieldops_migrator``  owns the schema and the tables; used ONLY for ``manage.py migrate`` (deploy step).
  * ``fieldops_app``       runtime role (web + Celery): USAGE on the schema, SELECT/INSERT/UPDATE/DELETE on tables,
                           USAGE/SELECT on sequences. NO TRUNCATE, NO DDL, NO REFERENCES/TRIGGER.

Idempotent, never drops anything, never touches data. NOT executed by CI or by the audit sessions: run it once against
production by an operator (``SUPABASE_ADMIN_DATABASE_URL``, ``FIELDOPS_MIGRATOR_PASSWORD``), then point
``DATABASE_URL`` at ``fieldops_app`` for web/worker and at ``fieldops_migrator`` for the migrate step only.
"""
from __future__ import annotations

import os
import sys


def grant_statements(schema: str = "fieldops", owner: str = "fieldops_migrator", app: str = "fieldops_app") -> list[str]:
    q = lambda ident: '"' + ident.replace('"', '""') + '"'  # noqa: E731
    s, o, a = q(schema), q(owner), q(app)
    return [
        f"REVOKE CREATE ON SCHEMA {s} FROM {a}",  # no DDL, ever
        f"GRANT USAGE ON SCHEMA {s} TO {a}",
        # existing objects
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {s} TO {a}",
        f"REVOKE TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA {s} FROM {a}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {s} TO {a}",
        # objects created by later migrations (run as the owner role)
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA {s} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {a}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA {s} GRANT USAGE, SELECT ON SEQUENCES TO {a}",
    ]


def role_attribute_statements(app: str = "fieldops_app") -> list[str]:
    """The runtime role must never be a superuser, bypass row security, or create roles / databases."""
    return [f'ALTER ROLE "{app}" NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOREPLICATION']


def main() -> None:
    import psycopg
    from psycopg import sql

    admin_url = os.environ.get("SUPABASE_ADMIN_DATABASE_URL")
    mig_pw = os.environ.get("FIELDOPS_MIGRATOR_PASSWORD")
    if not admin_url or not mig_pw:
        sys.exit("Set SUPABASE_ADMIN_DATABASE_URL and FIELDOPS_MIGRATOR_PASSWORD.")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'fieldops_migrator'").fetchone():
            conn.execute(sql.SQL("CREATE ROLE fieldops_migrator LOGIN NOINHERIT PASSWORD {}").format(sql.Literal(mig_pw)))
            print("created role fieldops_migrator")
        conn.execute("GRANT fieldops_migrator TO CURRENT_USER")
        conn.execute("GRANT fieldops_app TO CURRENT_USER")
        # hand ownership of the schema and its tables/sequences to the migrator (no data is touched)
        conn.execute("ALTER SCHEMA fieldops OWNER TO fieldops_migrator")
        rows = conn.execute(
            "SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'fieldops' AND c.relkind IN ('r', 'S', 'p', 'v')").fetchall()
        for name, kind in rows:
            what = "SEQUENCE" if kind == "S" else "TABLE"
            conn.execute(sql.SQL("ALTER {} fieldops.{} OWNER TO fieldops_migrator").format(
                sql.SQL(what), sql.Identifier(name)))
        for stmt in grant_statements() + role_attribute_statements():
            conn.execute(stmt)
        conn.execute("ALTER ROLE fieldops_migrator SET search_path = fieldops")
        conn.execute("REVOKE ALL ON SCHEMA public FROM fieldops_app")
        print("roles split: fieldops_migrator owns the schema, fieldops_app has DML only (no TRUNCATE / DDL / SUPERUSER / BYPASSRLS)")


if __name__ == "__main__":
    main()
