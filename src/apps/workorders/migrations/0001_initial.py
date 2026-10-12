import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("accounts", "0001_initial"), ("core", "0001_initial"), ("files", "0001_initial"),
        ("sites", "0001_initial"), ("tenancy", "0001_initial"),
    ]
    operations = [
        migrations.CreateModel(
            name="WorkOrder",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("order_number", models.CharField(max_length=32)),
                ("asset_reference", models.CharField(blank=True, max_length=100)),
                ("service_request_reference", models.CharField(blank=True, max_length=100)),
                ("title", models.CharField(max_length=200)), ("description", models.TextField(blank=True)),
                ("work_type", models.CharField(choices=[("CORRECTIVE", "Corrective"), ("PREVENTIVE", "Preventive"),
                    ("EMERGENCY", "Emergency"), ("INSPECTION", "Inspection"), ("INSTALLATION", "Installation")],
                    default="CORRECTIVE", max_length=20)),
                ("priority", models.CharField(choices=[("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High"),
                    ("CRITICAL", "Critical")], default="MEDIUM", max_length=12)),
                ("status", models.CharField(choices=[("DRAFT", "Draft"), ("PLANNED", "Planned"),
                    ("ASSIGNED", "Assigned"), ("DISPATCHED", "Dispatched"), ("IN_PROGRESS", "In Progress"),
                    ("ON_HOLD", "On Hold"), ("COMPLETED", "Completed"), ("SUPERVISOR_REVIEW", "Supervisor Review"),
                    ("CLOSED", "Closed"), ("CANCELLED", "Cancelled")], db_index=True, default="DRAFT", max_length=24)),
                ("planned_start", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("planned_end", models.DateTimeField(blank=True, null=True)),
                ("estimated_hours", models.DecimalField(decimal_places=2, default=0, max_digits=6)),
                ("dispatched_at", models.DateTimeField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("hold_reason", models.CharField(blank=True, max_length=300)),
                ("resolution_notes", models.TextField(blank=True)),
                ("checklist_items", models.JSONField(blank=True, default=list)),
                ("supervisor_notes", models.TextField(blank=True)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to="tenancy.organization")),
                ("site", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="work_orders", to="sites.site")),
                ("assigned_to", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="assigned_work_orders", to="tenancy.membership")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="created_work_orders", to=settings.AUTH_USER_MODEL)),
            ], options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="WorkOrderEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("action", models.CharField(max_length=40)), ("from_status", models.CharField(blank=True, max_length=24)),
                ("to_status", models.CharField(blank=True, max_length=24)), ("reason", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to="tenancy.organization")),
                ("work_order", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="events", to="workorders.workorder")),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to=settings.AUTH_USER_MODEL)),
                ("assigned_to", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="work_order_assignment_events", to="tenancy.membership")),
            ], options={"ordering": ["created_at", "id"]},
        ),
        migrations.CreateModel(
            name="WorkOrderLabor",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("work_date", models.DateField()), ("hours", models.DecimalField(decimal_places=2, max_digits=4)),
                ("description", models.CharField(max_length=500)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to="tenancy.organization")),
                ("work_order", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="labor_entries", to="workorders.workorder")),
                ("technician", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="work_order_labor", to="tenancy.membership")),
                ("entered_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to=settings.AUTH_USER_MODEL)),
            ], options={"ordering": ["-work_date", "-created_at"]},
        ),
        migrations.CreateModel(
            name="WorkOrderMaterial",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("part_reference", models.CharField(blank=True, max_length=100)),
                ("item_name", models.CharField(max_length=150)),
                ("quantity", models.DecimalField(decimal_places=2, max_digits=10)),
                ("unit", models.CharField(blank=True, max_length=30)), ("notes", models.CharField(blank=True, max_length=500)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to="tenancy.organization")),
                ("work_order", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="material_entries", to="workorders.workorder")),
                ("entered_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to=settings.AUTH_USER_MODEL)),
            ], options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="WorkOrderEvidence",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to="tenancy.organization")),
                ("work_order", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="evidence_links", to="workorders.workorder")),
                ("attachment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="work_order_links", to="files.attachment")),
                ("linked_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(model_name="workorder", constraint=models.UniqueConstraint(
            fields=("organization", "order_number"), name="uniq_wo_number_org")),
        migrations.AddConstraint(model_name="workorder", constraint=models.CheckConstraint(
            condition=models.Q(planned_end__isnull=True) | models.Q(planned_start__isnull=True) |
            models.Q(planned_end__gt=models.F("planned_start")), name="wo_planned_window_valid")),
        migrations.AddConstraint(model_name="workorder", constraint=models.CheckConstraint(
            condition=models.Q(estimated_hours__gte=0), name="wo_estimated_hours_nonnegative")),
        migrations.AddConstraint(model_name="workorderlabor", constraint=models.CheckConstraint(
            condition=models.Q(hours__gt=0) & models.Q(hours__lte=24), name="wo_labor_hours_1_24")),
        migrations.AddConstraint(model_name="workordermaterial", constraint=models.CheckConstraint(
            condition=models.Q(quantity__gt=0), name="wo_material_qty_positive")),
        migrations.AddConstraint(model_name="workorderevidence", constraint=models.UniqueConstraint(
            fields=("work_order", "attachment"), name="uniq_wo_evidence")),
        migrations.AddIndex(model_name="workorder", index=models.Index(fields=["organization", "status"],
            name="workorders_org_status_idx")),
        migrations.AddIndex(model_name="workorder", index=models.Index(fields=["site", "status"],
            name="workorders_site_status_idx")),
        migrations.AddIndex(model_name="workorder", index=models.Index(fields=["assigned_to", "status"],
            name="workorders_assigned_idx")),
    ]
