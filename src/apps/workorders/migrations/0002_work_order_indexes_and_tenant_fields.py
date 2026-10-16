import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("workorders", "0001_initial")]
    operations = [
        migrations.AlterField(
            model_name="workorder", name="organization",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="+", to="tenancy.organization"),
        ),
        migrations.AlterField(
            model_name="workorderevent", name="organization",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="+", to="tenancy.organization"),
        ),
        migrations.AlterField(
            model_name="workorderevidence", name="organization",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="+", to="tenancy.organization"),
        ),
        migrations.AlterField(
            model_name="workorderlabor", name="organization",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="+", to="tenancy.organization"),
        ),
        migrations.AlterField(
            model_name="workordermaterial", name="organization",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="+", to="tenancy.organization"),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameIndex(model_name="workorder", old_name="workorders_org_status_idx",
                                       new_name="workorders__organiz_90137b_idx"),
                migrations.RenameIndex(model_name="workorder", old_name="workorders_site_status_idx",
                                       new_name="workorders__site_id_f50dee_idx"),
                migrations.RenameIndex(model_name="workorder", old_name="workorders_assigned_idx",
                                       new_name="workorders__assigne_598c53_idx"),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="""
                    DO $$
                    BEGIN
                        IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'workorders_org_status_idx') THEN
                            ALTER INDEX workorders_org_status_idx RENAME TO workorders__organiz_90137b_idx;
                        END IF;
                        IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'workorders_site_status_idx') THEN
                            ALTER INDEX workorders_site_status_idx RENAME TO workorders__site_id_f50dee_idx;
                        END IF;
                        IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'workorders_assigned_idx') THEN
                            ALTER INDEX workorders_assigned_idx RENAME TO workorders__assigne_598c53_idx;
                        END IF;
                    END $$;
                    """,
                    reverse_sql="",
                )
            ]
        ),
    ]
