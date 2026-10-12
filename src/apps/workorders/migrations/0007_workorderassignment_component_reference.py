import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import F, Q


def backfill_assignments(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    cursor.execute("""
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'workorders_workorder' AND column_name = 'order_number'
    """)
    if not cursor.fetchone():
        return
    WorkOrder = apps.get_model("workorders", "WorkOrder")
    Assignment = apps.get_model("workorders", "WorkOrderAssignment")
    alias = schema_editor.connection.alias
    try:
        for order in WorkOrder.objects.using(alias).filter(assigned_to__isnull=False,
                planned_start__isnull=False, planned_end__isnull=False):
            if order.planned_end <= order.planned_start:
                continue
            event = order.events.using(alias).filter(action="assigned").order_by("-created_at").first()
            Assignment.objects.using(alias).get_or_create(
                work_order_id=order.pk,
                defaults={"organization_id": order.organization_id, "technician_id": order.assigned_to_id,
                          "scheduled_start": order.planned_start, "scheduled_end": order.planned_end,
                          "assigned_by_id": event.actor_id if event and event.actor_id else order.created_by_id},
            )
    except Exception:
        pass


class Migration(migrations.Migration):
    dependencies = [
        ("workorders", "0006_alter_closureapproval_organization"),
        ("tenancy", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="workorder",
            name="component_reference",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.CreateModel(
            name="WorkOrderAssignment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                         primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("scheduled_start", models.DateTimeField()),
                ("scheduled_end", models.DateTimeField()),
                ("assigned_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="created_work_order_assignments", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", editable=False, to="tenancy.organization")),
                ("technician", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="work_order_assignments", to="tenancy.membership")),
                ("work_order", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                    related_name="assignment", to="workorders.workorder")),
            ],
            options={"ordering": ["scheduled_start", "id"]},
        ),
        migrations.RunPython(backfill_assignments, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="workorderassignment",
            constraint=models.CheckConstraint(condition=Q(scheduled_end__gt=F("scheduled_start")),
                                              name="wo_assignment_window_valid"),
        ),
    ]
