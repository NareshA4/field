# Generated migration to reconcile legacy schema with current models
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('checklists', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE checklists_checklisttemplate
                ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS created_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL;

            ALTER TABLE checklists_checklistitem
                ADD COLUMN IF NOT EXISTS guidance TEXT DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS required BOOLEAN DEFAULT TRUE NOT NULL;

            ALTER TABLE checklists_inspection
                ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
                ADD COLUMN IF NOT EXISTS summary TEXT DEFAULT '' NOT NULL;

            ALTER TABLE checklists_inspectionresponse
                ADD COLUMN IF NOT EXISTS answered_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
                ADD COLUMN IF NOT EXISTS answered_by_id UUID REFERENCES tenancy_membership(id) ON DELETE PROTECT;

            ALTER TABLE checklists_finding
                ADD COLUMN IF NOT EXISTS resolution_notes TEXT DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS resolved_by_id UUID REFERENCES tenancy_membership(id) ON DELETE SET NULL,
                ADD COLUMN IF NOT EXISTS created_by_id UUID REFERENCES tenancy_membership(id) ON DELETE SET NULL;
            """,
            reverse_sql="",
        ),
    ]
