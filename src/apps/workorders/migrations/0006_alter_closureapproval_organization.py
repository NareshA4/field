import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("workorders", "0005_closure_approval_and_closed_by")]

    operations = [
        migrations.AlterField(
            model_name="closureapproval",
            name="organization",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="+", to="tenancy.organization"),
        ),
    ]
