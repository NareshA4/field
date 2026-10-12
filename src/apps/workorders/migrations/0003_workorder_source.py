# Adds an integration identifier after the M06 source fields are established.
from django.db import migrations, models


def backfill_source_id(apps, schema_editor):
    WorkOrder = apps.get_model("workorders", "WorkOrder")
    alias = schema_editor.connection.alias
    WorkOrder.objects.using(alias).filter(
        source_request__isnull=False,
        source_type__in=("REQUEST", "INCIDENT"),
    ).update(source_id=models.F("source_request_id"))


class Migration(migrations.Migration):
    dependencies = [
        ("workorders", "0002_workordermaterial_part_line"),
        ("workorders", "0003_workorder_due_at_workorder_source_reference_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="workorder",
            name="source_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_source_id, migrations.RunPython.noop),
    ]
