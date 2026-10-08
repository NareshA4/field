# Generated migration to reconcile legacy schema with current models
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contracts', '0002_coverage_sla_profile'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- 1. contracts_serviceprovider
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='contracts_serviceprovider' AND column_name='contact_person'
                ) THEN
                    ALTER TABLE contracts_serviceprovider RENAME COLUMN contact_person TO contact_name;
                ELSE
                    ALTER TABLE contracts_serviceprovider ADD COLUMN IF NOT EXISTS contact_name VARCHAR(120) DEFAULT '' NOT NULL;
                END IF;
            END $$;
            ALTER TABLE contracts_serviceprovider ADD COLUMN IF NOT EXISTS notes VARCHAR(500) DEFAULT '' NOT NULL;

            -- 2. contracts_coverageagreement
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='contracts_coverageagreement' AND column_name='agreement_number'
                ) THEN
                    ALTER TABLE contracts_coverageagreement RENAME COLUMN agreement_number TO reference;
                ELSE
                    ALTER TABLE contracts_coverageagreement ADD COLUMN IF NOT EXISTS reference VARCHAR(80) DEFAULT '' NOT NULL;
                END IF;
            END $$;

            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='contracts_coverageagreement' AND column_name='agreement_type'
                ) THEN
                    ALTER TABLE contracts_coverageagreement RENAME COLUMN agreement_type TO kind;
                ELSE
                    ALTER TABLE contracts_coverageagreement ADD COLUMN IF NOT EXISTS kind VARCHAR(20) DEFAULT '' NOT NULL;
                END IF;
            END $$;

            ALTER TABLE contracts_coverageagreement ADD COLUMN IF NOT EXISTS title VARCHAR(200) DEFAULT '' NOT NULL;
            ALTER TABLE contracts_coverageagreement ADD COLUMN IF NOT EXISTS sla_terms VARCHAR(300) DEFAULT '' NOT NULL;
            ALTER TABLE contracts_coverageagreement ADD COLUMN IF NOT EXISTS renewal_alerted_at TIMESTAMPTZ NULL;
            ALTER TABLE contracts_coverageagreement ADD COLUMN IF NOT EXISTS deactivation_reason VARCHAR(300) DEFAULT '' NOT NULL;
            ALTER TABLE contracts_coverageagreement ADD COLUMN IF NOT EXISTS created_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL;

            -- 3. contracts_coveragecheck
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='contracts_coveragecheck' AND column_name='is_eligible'
                ) THEN
                    ALTER TABLE contracts_coveragecheck RENAME COLUMN is_eligible TO eligible;
                ELSE
                    ALTER TABLE contracts_coveragecheck ADD COLUMN IF NOT EXISTS eligible BOOLEAN DEFAULT TRUE NOT NULL;
                END IF;
            END $$;

            ALTER TABLE contracts_coveragecheck ADD COLUMN IF NOT EXISTS checked_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL;
            """,
            reverse_sql="",
        ),
    ]
