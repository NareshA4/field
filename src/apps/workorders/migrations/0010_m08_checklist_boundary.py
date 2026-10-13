from django.db import migrations, models


WORK_TYPE_TEMPLATES = {
    "CORRECTIVE": "M08-WO-CORRECTIVE",
    "PREVENTIVE": "M08-WO-PREVENTIVE",
    "EMERGENCY": "M08-WO-EMERGENCY",
    "INSPECTION": "M08-WO-INSPECTION",
    "INSTALLATION": "M08-WO-INSTALLATION",
}


def set_initial_checklist_state(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    cursor.execute("""
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'workorders_workorder' AND column_name = 'order_number'
    """)
    if not cursor.fetchone():
        return
    WorkOrder = apps.get_model("workorders", "WorkOrder")
    try:
        for order in WorkOrder.objects.all().iterator():
            template_ref = WORK_TYPE_TEMPLATES.get(order.work_type)
            if template_ref:
                order.checklist_required = True
                order.checklist_template_ref = template_ref
                order.checklist_status = "PENDING"
                order.checklist_validation_state = "PENDING"
                order.save(update_fields=["checklist_required", "checklist_template_ref", "checklist_status",
                                          "checklist_validation_state"])
    except Exception:
        pass


class Migration(migrations.Migration):
    dependencies = [("workorders", "0009_draft_planning_fields_optional")]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameField(
                    model_name="workorder",
                    old_name="checklist_items",
                    new_name="provisional_checklist",
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'workorders_workorder' AND column_name = 'checklist_items'
                        ) THEN
                            ALTER TABLE workorders_workorder RENAME COLUMN checklist_items TO provisional_checklist;
                        ELSIF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'workorders_workorder' AND column_name = 'provisional_checklist'
                        ) THEN
                            ALTER TABLE workorders_workorder ADD COLUMN provisional_checklist JSONB DEFAULT '[]'::jsonb;
                        END IF;
                    END $$;
                    """,
                    reverse_sql="",
                )
            ]
        ),
        migrations.AddField(
            model_name="workorder", name="checklist_required",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="workorder", name="checklist_template_ref",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="workorder", name="checklist_instance_ref",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="workorder", name="checklist_status",
            field=models.CharField(choices=[("NOT_REQUIRED", "Not required"), ("PENDING", "Pending"),
                ("IN_PROGRESS", "In progress"), ("COMPLETED", "Completed"), ("FAILED", "Failed")],
                db_index=True, default="NOT_REQUIRED", max_length=20),
        ),
        migrations.AddField(
            model_name="workorder", name="checklist_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workorder", name="checklist_validated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workorder", name="checklist_validation_state",
            field=models.CharField(choices=[("NOT_REQUIRED", "Not required"), ("PENDING", "Pending"),
                ("VALID", "Valid"), ("INVALID", "Invalid")], db_index=True,
                default="NOT_REQUIRED", max_length=20),
        ),
        migrations.RunPython(set_initial_checklist_state, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="workorder",
            index=models.Index(fields=["organization", "checklist_status"], name="workorders_org_checklist_idx"),
        ),
    ]
