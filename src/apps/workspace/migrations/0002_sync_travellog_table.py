# Generated migration to ensure workspace_travellog table exists
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("workspace", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE TABLE IF NOT EXISTS workspace_travellog (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                organization_id UUID NOT NULL REFERENCES tenancy_organization(id) ON DELETE RESTRICT,
                work_order_id UUID NOT NULL REFERENCES workorders_workorder(id) ON DELETE RESTRICT,
                technician_id UUID NOT NULL REFERENCES tenancy_membership(id) ON DELETE RESTRICT,
                start_time TIMESTAMPTZ NULL,
                end_time TIMESTAMPTZ NULL,
                duration_hours NUMERIC(4, 2) NOT NULL DEFAULT 0.00,
                distance_km NUMERIC(6, 2) NULL,
                route_details VARCHAR(255) NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                labor_entry_id UUID NULL REFERENCES workorders_workorderlabor(id) ON DELETE SET NULL
            );
            """,
            reverse_sql="",
        ),
    ]
