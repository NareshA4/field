import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("tenancy", "0001_initial"),
        ("workorders", "0004_workorder_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="workorder",
            name="closed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="closed_work_orders",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="ClosureApproval",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("decision", models.CharField(choices=[("APPROVED", "Approved"),
                                                       ("RETURNED", "Returned for rework")], max_length=12)),
                ("comment", models.TextField()),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                    related_name="+", to="tenancy.organization")),
                ("approver", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                related_name="work_order_closure_decisions",
                                                to=settings.AUTH_USER_MODEL)),
                ("work_order", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                  related_name="closure_approvals",
                                                  to="workorders.workorder")),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
    ]
