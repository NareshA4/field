# Generated migration to reconcile legacy workorders number column with current Django models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workorders", "0017_workordersignoff"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="workorder",
                    name="number",
                    field=models.CharField(blank=True, db_index=True, max_length=32, null=True),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="""
                    DO $$
                    BEGIN
                        -- 1. If legacy 'number' column does not exist (fresh db), add it as nullable
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns 
                            WHERE table_name = 'workorders_workorder' AND column_name = 'number'
                        ) THEN
                            ALTER TABLE workorders_workorder ADD COLUMN number VARCHAR(32) NULL;
                        ELSE
                            -- 2. Detect conflicting business identifiers between 'number' and 'order_number'
                            IF EXISTS (
                                SELECT 1 FROM workorders_workorder 
                                WHERE number IS NOT NULL AND order_number IS NOT NULL 
                                  AND number <> '' AND order_number <> ''
                                  AND number <> order_number
                            ) THEN
                                RAISE EXCEPTION 'Migration 0018 aborted: conflicting business identifiers detected between number and order_number in workorders_workorder.';
                            END IF;

                            -- 3. Populate missing values bidirectionally
                            UPDATE workorders_workorder 
                            SET order_number = number 
                            WHERE (order_number IS NULL OR order_number = '') AND number IS NOT NULL;

                            UPDATE workorders_workorder 
                            SET number = order_number 
                            WHERE (number IS NULL OR number = '') AND order_number IS NOT NULL;

                            -- 4. Drop NOT NULL on legacy 'number' column
                            ALTER TABLE workorders_workorder ALTER COLUMN number DROP NOT NULL;

                            -- 5. Drop legacy unique constraint on 'number' if present
                            ALTER TABLE workorders_workorder DROP CONSTRAINT IF EXISTS uniq_work_order_number_per_org;
                        END IF;

                        -- 6. Ensure canonical 'order_number' column is strictly NOT NULL
                        ALTER TABLE workorders_workorder ALTER COLUMN order_number SET NOT NULL;

                        -- 7. Ensure tenant-scoped uniqueness on canonical 'order_number'
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint WHERE conname = 'uniq_wo_number_org'
                        ) AND NOT EXISTS (
                            SELECT 1 FROM pg_indexes WHERE indexname = 'uniq_wo_number_org'
                        ) THEN
                            ALTER TABLE workorders_workorder 
                            ADD CONSTRAINT uniq_wo_number_org UNIQUE (organization_id, order_number);
                        END IF;
                    END $$;
                    """,
                    reverse_sql="""
                    -- Reverse operations for migration 0018
                    """,
                ),
            ],
        ),
    ]
