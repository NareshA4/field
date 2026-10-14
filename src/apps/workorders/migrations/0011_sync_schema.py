# Generated migration to reconcile legacy schema with current models
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('workorders', '0010_m08_checklist_boundary'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE workorders_workorder
                ADD COLUMN IF NOT EXISTS order_number VARCHAR(32) NULL,
                ADD COLUMN IF NOT EXISTS asset_reference VARCHAR(100) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS service_request_reference VARCHAR(100) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS supervisor_notes TEXT DEFAULT '' NOT NULL,
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
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'workorders_workorder' AND column_name = 'number'
                ) THEN
                    UPDATE workorders_workorder SET order_number = number WHERE (order_number IS NULL OR order_number = '') AND number IS NOT NULL;
                    ALTER TABLE workorders_workorder ALTER COLUMN number DROP NOT NULL;
                END IF;
            END $$;
            ALTER TABLE workorders_workorder DROP CONSTRAINT IF EXISTS work_order_source_pair;
            ALTER TABLE workorders_workorder DROP CONSTRAINT IF EXISTS uniq_work_order_number_per_org;

            ALTER TABLE workorders_workorderevent
                ADD COLUMN IF NOT EXISTS action VARCHAR(30) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS reason VARCHAR(500) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
                ADD COLUMN IF NOT EXISTS assigned_to_id UUID REFERENCES tenancy_membership(id) ON DELETE SET NULL;

            ALTER TABLE workorders_workorderlabor
                ADD COLUMN IF NOT EXISTS work_date DATE DEFAULT CURRENT_DATE NOT NULL,
                ADD COLUMN IF NOT EXISTS notes VARCHAR(500) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS description VARCHAR(500) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS recorded_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL,
                ADD COLUMN IF NOT EXISTS entered_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL;
            ALTER TABLE workorders_workorderlabor ALTER COLUMN notes DROP NOT NULL;
            ALTER TABLE workorders_workorderlabor ALTER COLUMN notes SET DEFAULT '';

            ALTER TABLE workorders_workordermaterial
                ADD COLUMN IF NOT EXISTS part_number VARCHAR(80) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS description VARCHAR(200) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS item_name VARCHAR(150) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS part_reference VARCHAR(100) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS notes VARCHAR(500) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS unit VARCHAR(20) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS recorded_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL,
                ADD COLUMN IF NOT EXISTS entered_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL;
            ALTER TABLE workorders_workordermaterial ALTER COLUMN description DROP NOT NULL;
            ALTER TABLE workorders_workordermaterial ALTER COLUMN description SET DEFAULT '';
            ALTER TABLE workorders_workordermaterial ALTER COLUMN part_number DROP NOT NULL;
            ALTER TABLE workorders_workordermaterial ALTER COLUMN part_number SET DEFAULT '';
            """,
            reverse_sql="",
        ),
    ]
