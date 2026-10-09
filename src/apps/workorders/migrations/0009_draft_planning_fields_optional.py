from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("workorders", "0008_workorder_asset_workorder_component_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workorder",
            name="work_type",
            field=models.CharField(blank=True, choices=[("CORRECTIVE", "Corrective"), ("PREVENTIVE", "Preventive"), ("EMERGENCY", "Emergency"), ("INSPECTION", "Inspection"), ("INSTALLATION", "Installation")], default=None, max_length=20, null=True),
        ),
        migrations.AlterField(
            model_name="workorder",
            name="priority",
            field=models.CharField(blank=True, choices=[("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High"), ("CRITICAL", "Critical")], default=None, max_length=12, null=True),
        ),
        migrations.AlterField(
            model_name="workorder",
            name="estimated_hours",
            field=models.DecimalField(blank=True, decimal_places=2, default=None, max_digits=6, null=True),
        ),
    ]
