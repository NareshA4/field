# Generated migration to reconcile legacy workorders schema with current Django models
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("workorders", "0013_merge_20261013_1054"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- 1. workorders_workorder
            ALTER TABLE workorders_workorder
                ADD COLUMN IF NOT EXISTS order_number VARCHAR(32),
                ADD COLUMN IF NOT EXISTS supervisor_notes TEXT DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS service_request_reference VARCHAR(100) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS asset_reference VARCHAR(100) DEFAULT '' NOT NULL;

            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'workorders_workorder' AND column_name = 'number'
                ) THEN
                    UPDATE workorders_workorder 
                    SET order_number = number 
                    WHERE (order_number IS NULL OR order_number = '') AND number IS NOT NULL;
                END IF;
            END $$;

            -- 2. workorders_workorderevent
            ALTER TABLE workorders_workorderevent
                ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb NOT NULL;

            -- 3. workorders_workorderlabor
            ALTER TABLE workorders_workorderlabor
                ADD COLUMN IF NOT EXISTS entered_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL;

            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'workorders_workorderlabor' AND column_name = 'recorded_by_id'
                ) THEN
                    UPDATE workorders_workorderlabor 
                    SET entered_by_id = recorded_by_id 
                    WHERE entered_by_id IS NULL AND recorded_by_id IS NOT NULL;
                END IF;
            END $$;

            -- 4. workorders_workordermaterial
            ALTER TABLE workorders_workordermaterial
                ADD COLUMN IF NOT EXISTS part_reference VARCHAR(100) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS item_name VARCHAR(150) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS notes VARCHAR(500) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS entered_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL;

            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'workorders_workordermaterial' AND column_name = 'description'
                ) THEN
                    UPDATE workorders_workordermaterial 
                    SET item_name = description 
                    WHERE (item_name IS NULL OR item_name = '') AND description IS NOT NULL;
                END IF;
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'workorders_workordermaterial' AND column_name = 'part_number'
                ) THEN
                    UPDATE workorders_workordermaterial 
                    SET part_reference = part_number 
                    WHERE (part_reference IS NULL OR part_reference = '') AND part_number IS NOT NULL;
                END IF;
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'workorders_workordermaterial' AND column_name = 'recorded_by_id'
                ) THEN
                    UPDATE workorders_workordermaterial 
                    SET entered_by_id = recorded_by_id 
                    WHERE entered_by_id IS NULL AND recorded_by_id IS NOT NULL;
                END IF;
            END $$;

            -- 5. workorders_workorderevidence
            CREATE TABLE IF NOT EXISTS workorders_workorderevidence (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                organization_id UUID NOT NULL REFERENCES tenancy_organization(id) ON DELETE RESTRICT,
                work_order_id UUID NOT NULL REFERENCES workorders_workorder(id) ON DELETE RESTRICT,
                attachment_id UUID NOT NULL REFERENCES files_attachment(id) ON DELETE RESTRICT,
                linked_by_id UUID NOT NULL REFERENCES accounts_user(id) ON DELETE RESTRICT,
                CONSTRAINT uniq_wo_evidence UNIQUE (work_order_id, attachment_id)
            );
            """,
            reverse_sql="",
        ),
    ]
