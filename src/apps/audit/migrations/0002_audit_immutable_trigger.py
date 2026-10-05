from django.db import migrations

FORWARD = """
CREATE OR REPLACE FUNCTION audit_auditlog_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_auditlog is append-only (% blocked)', TG_OP USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_auditlog_immutable
BEFORE UPDATE OR DELETE ON audit_auditlog
FOR EACH ROW EXECUTE FUNCTION audit_auditlog_block_mutation();
"""

REVERSE = """
DROP TRIGGER IF EXISTS audit_auditlog_immutable ON audit_auditlog;
DROP FUNCTION IF EXISTS audit_auditlog_block_mutation();
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
