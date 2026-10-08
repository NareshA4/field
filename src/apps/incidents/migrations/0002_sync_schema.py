# Generated migration to reconcile legacy schema with current models
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('incidents', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE incidents_servicerequest
                ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
                ADD COLUMN IF NOT EXISTS triaged_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS decision_reason VARCHAR(500) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS impact_notes VARCHAR(500) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ NULL;

            ALTER TABLE incidents_downtime
                ADD COLUMN IF NOT EXISTS request_id UUID REFERENCES incidents_servicerequest(id) ON DELETE SET NULL,
                ADD COLUMN IF NOT EXISTS end_source VARCHAR(20) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS recorded_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL,
                ADD COLUMN IF NOT EXISTS ended_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL;

            ALTER TABLE incidents_servicerequesthistory
                ADD COLUMN IF NOT EXISTS request_id UUID REFERENCES incidents_servicerequest(id) ON DELETE CASCADE,
                ADD COLUMN IF NOT EXISTS action VARCHAR(30) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS reason VARCHAR(500) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS source VARCHAR(30) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS changed_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL;
            """,
            reverse_sql="",
        ),
    ]
