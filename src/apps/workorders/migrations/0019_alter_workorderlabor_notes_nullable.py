# Generated migration to ensure workorders_workorderlabor notes column is nullable
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("workorders", "0018_sync_workorder_number_column"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'workorders_workorderlabor' AND column_name = 'notes'
                ) THEN
                    ALTER TABLE workorders_workorderlabor ALTER COLUMN notes DROP NOT NULL;
                    ALTER TABLE workorders_workorderlabor ALTER COLUMN notes SET DEFAULT '';
                END IF;
            END $$;
            """,
            reverse_sql="""
            -- No-op reverse
            """,
        ),
    ]
