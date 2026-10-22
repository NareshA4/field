from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('checklists', '0003_convert_template_key_to_uuid'),
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE checklists_finding
                DROP CONSTRAINT IF EXISTS checklists_finding_created_by_id_fkey,
                DROP CONSTRAINT IF EXISTS checklists_finding_resolved_by_id_fkey;

            ALTER TABLE checklists_finding
                ADD CONSTRAINT checklists_finding_created_by_id_fkey
                    FOREIGN KEY (created_by_id) REFERENCES accounts_user(id) ON DELETE SET NULL,
                ADD CONSTRAINT checklists_finding_resolved_by_id_fkey
                    FOREIGN KEY (resolved_by_id) REFERENCES accounts_user(id) ON DELETE SET NULL;
            """,
            reverse_sql="""
            ALTER TABLE checklists_finding
                DROP CONSTRAINT IF EXISTS checklists_finding_created_by_id_fkey,
                DROP CONSTRAINT IF EXISTS checklists_finding_resolved_by_id_fkey;

            ALTER TABLE checklists_finding
                ADD CONSTRAINT checklists_finding_created_by_id_fkey
                    FOREIGN KEY (created_by_id) REFERENCES tenancy_membership(id) ON DELETE SET NULL,
                ADD CONSTRAINT checklists_finding_resolved_by_id_fkey
                    FOREIGN KEY (resolved_by_id) REFERENCES tenancy_membership(id) ON DELETE SET NULL;
            """,
        ),
    ]
