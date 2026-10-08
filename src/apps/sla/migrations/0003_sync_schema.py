# Generated migration to reconcile legacy schema with current models
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('sla', '0002_profile_coverage_only'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE sla_slaprofile
                ADD COLUMN IF NOT EXISTS description VARCHAR(300) DEFAULT '' NOT NULL;

            ALTER TABLE sla_escalationrule
                ADD COLUMN IF NOT EXISTS profile_id UUID REFERENCES sla_slaprofile(id) ON DELETE CASCADE,
                ADD COLUMN IF NOT EXISTS target_kind VARCHAR(20) DEFAULT 'RESPONSE' NOT NULL,
                ADD COLUMN IF NOT EXISTS notify_role_id UUID REFERENCES rbac_role(id) ON DELETE SET NULL,
                ADD COLUMN IF NOT EXISTS notify_assignee BOOLEAN DEFAULT FALSE NOT NULL;

            ALTER TABLE sla_slatracking
                ADD COLUMN IF NOT EXISTS request_id UUID REFERENCES incidents_servicerequest(id) ON DELETE CASCADE,
                ADD COLUMN IF NOT EXISTS site_id UUID REFERENCES sites_site(id) ON DELETE CASCADE,
                ADD COLUMN IF NOT EXISTS priority VARCHAR(10) DEFAULT 'MEDIUM' NOT NULL,
                ADD COLUMN IF NOT EXISTS response_minutes INTEGER DEFAULT 0 NOT NULL,
                ADD COLUMN IF NOT EXISTS resolution_minutes INTEGER DEFAULT 0 NOT NULL,
                ADD COLUMN IF NOT EXISTS warning_percent INTEGER DEFAULT 75 NOT NULL,
                ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
                ADD COLUMN IF NOT EXISTS response_met_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS resolution_met_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS response_state VARCHAR(20) DEFAULT 'PENDING' NOT NULL,
                ADD COLUMN IF NOT EXISTS resolution_state VARCHAR(20) DEFAULT 'PENDING' NOT NULL,
                ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS paused_seconds INTEGER DEFAULT 0 NOT NULL,
                ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ NULL;

            ALTER TABLE sla_slaevent
                ADD COLUMN IF NOT EXISTS target_kind VARCHAR(20) DEFAULT 'RESPONSE' NOT NULL,
                ADD COLUMN IF NOT EXISTS at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
                ADD COLUMN IF NOT EXISTS detail VARCHAR(300) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS rule_id UUID REFERENCES sla_escalationrule(id) ON DELETE SET NULL,
                ADD COLUMN IF NOT EXISTS notified INTEGER DEFAULT 0 NOT NULL,
                ADD COLUMN IF NOT EXISTS actor_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL;

            ALTER TABLE sla_slabreach
                ADD COLUMN IF NOT EXISTS profile_id UUID REFERENCES sla_slaprofile(id) ON DELETE CASCADE,
                ADD COLUMN IF NOT EXISTS site_id UUID REFERENCES sites_site(id) ON DELETE CASCADE,
                ADD COLUMN IF NOT EXISTS priority VARCHAR(10) DEFAULT 'MEDIUM' NOT NULL,
                ADD COLUMN IF NOT EXISTS severity VARCHAR(10) DEFAULT 'MEDIUM' NOT NULL,
                ADD COLUMN IF NOT EXISTS detected_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
                ADD COLUMN IF NOT EXISTS escalated_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS notified_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS acknowledged_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL,
                ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS closed_reason VARCHAR(300) DEFAULT '' NOT NULL;
            """,
            reverse_sql="",
        ),
    ]
