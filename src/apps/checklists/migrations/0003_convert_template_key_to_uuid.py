# Generated migration to reconcile template key column with UUID type in PostgreSQL
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("checklists", "0002_sync_schema"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'checklists_checklisttemplate' 
                      AND column_name = 'key' 
                      AND data_type = 'character varying'
                ) THEN
                    DROP INDEX IF EXISTS checklists_checklisttemplate_key_76e9061e_like;
                    ALTER TABLE checklists_checklisttemplate DROP CONSTRAINT IF EXISTS uniq_checklist_template_key_version;
                    ALTER TABLE checklists_checklisttemplate ALTER COLUMN key TYPE UUID USING key::uuid;
                    ALTER TABLE checklists_checklisttemplate ADD CONSTRAINT uniq_checklist_template_key_version UNIQUE (organization_id, key, version);
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_indexes WHERE indexname = 'uniq_active_checklist_per_key'
                    ) THEN
                        CREATE UNIQUE INDEX uniq_active_checklist_per_key ON checklists_checklisttemplate (organization_id, key) WHERE status = 'ACTIVE';
                    END IF;
                END IF;
            END $$;
            """,
            reverse_sql="""
            -- Reverse operation
            """,
        ),
    ]
