# Generated migration to reconcile legacy schema with current models
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('identification', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- Reconcile AssetIdentifier schema
            ALTER TABLE identification_assetidentifier 
                ADD COLUMN IF NOT EXISTS revoked_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL;

            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='identification_assetidentifier' AND column_name='revocation_reason'
                ) THEN
                    ALTER TABLE identification_assetidentifier RENAME COLUMN revocation_reason TO revoked_reason;
                ELSE
                    ALTER TABLE identification_assetidentifier ADD COLUMN IF NOT EXISTS revoked_reason VARCHAR(300) DEFAULT '' NOT NULL;
                END IF;
            END $$;

            ALTER TABLE identification_assetidentifier ALTER COLUMN token TYPE VARCHAR(64);

            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='identification_assetidentifier' AND column_name='label'
                ) THEN
                    ALTER TABLE identification_assetidentifier ALTER COLUMN label DROP NOT NULL;
                END IF;
            END $$;

            -- Reconcile ScanEvent schema
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='identification_scanevent' AND column_name='scanner_id'
                ) THEN
                    ALTER TABLE identification_scanevent RENAME COLUMN scanner_id TO scanned_by_id;
                ELSE
                    ALTER TABLE identification_scanevent ADD COLUMN IF NOT EXISTS scanned_by_id UUID REFERENCES accounts_user(id) ON DELETE RESTRICT;
                END IF;
            END $$;

            ALTER TABLE identification_scanevent 
                ADD COLUMN IF NOT EXISTS notes VARCHAR(300) DEFAULT '' NOT NULL;

            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='identification_scanevent' AND column_name='user_agent'
                ) THEN
                    ALTER TABLE identification_scanevent ALTER COLUMN user_agent DROP NOT NULL;
                END IF;
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='identification_scanevent' AND column_name='identifier_id'
                ) THEN
                    ALTER TABLE identification_scanevent ALTER COLUMN identifier_id DROP NOT NULL;
                END IF;
            END $$;
            """,
            reverse_sql="",
        ),
    ]
