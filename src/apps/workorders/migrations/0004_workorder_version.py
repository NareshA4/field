from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("workorders", "0003_workorder_due_at_workorder_source_reference_and_more")]

    operations = [
        migrations.AddField(
            model_name="workorder",
            name="version",
            field=models.PositiveIntegerField(default=1, editable=False),
        ),
    ]
