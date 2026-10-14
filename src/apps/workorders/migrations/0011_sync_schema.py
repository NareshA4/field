# Generated migration to reconcile legacy schema with current models
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('workorders', '0011_merge_0004_sync_schema_0010_m08_checklist_boundary'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE workorders_workorder
                ADD COLUMN IF NOT EXISTS estimated_hours NUMERIC(6, 2) NULL,
                ADD COLUMN IF NOT EXISTS dispatched_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS review_started_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS hold_reason VARCHAR(500) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS resolution_notes TEXT DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS created_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL,
                ADD COLUMN IF NOT EXISTS closed_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL;

            ALTER TABLE workorders_workorderevent
                ADD COLUMN IF NOT EXISTS action VARCHAR(30) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS reason VARCHAR(500) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS assigned_to_id UUID REFERENCES tenancy_membership(id) ON DELETE SET NULL;

            ALTER TABLE workorders_workorderlabor
                ADD COLUMN IF NOT EXISTS work_date DATE DEFAULT CURRENT_DATE NOT NULL,
                ADD COLUMN IF NOT EXISTS notes VARCHAR(500) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS recorded_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL;

            ALTER TABLE workorders_workordermaterial
                ADD COLUMN IF NOT EXISTS part_number VARCHAR(80) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS description VARCHAR(200) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS unit VARCHAR(20) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS recorded_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL;
            """,
            reverse_sql="",
        ),
    ]
