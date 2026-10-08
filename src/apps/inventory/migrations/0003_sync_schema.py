# Generated migration to reconcile legacy schema with current models
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0002_alter_stockmovement_quantity'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE inventory_warehouse
                ADD COLUMN IF NOT EXISTS description VARCHAR(300) DEFAULT '' NOT NULL;

            ALTER TABLE inventory_part
                ADD COLUMN IF NOT EXISTS min_stock INTEGER DEFAULT 0 NOT NULL,
                ADD COLUMN IF NOT EXISTS max_stock INTEGER NULL,
                ADD COLUMN IF NOT EXISTS reorder_quantity INTEGER DEFAULT 1 NOT NULL;

            ALTER TABLE inventory_stockbalance
                ADD COLUMN IF NOT EXISTS min_level INTEGER DEFAULT 0 NOT NULL,
                ADD COLUMN IF NOT EXISTS max_level INTEGER NULL,
                ADD COLUMN IF NOT EXISTS reorder_quantity INTEGER DEFAULT 1 NOT NULL;

            ALTER TABLE inventory_workorderpart
                ADD COLUMN IF NOT EXISTS quantity_requested INTEGER DEFAULT 1 NOT NULL,
                ADD COLUMN IF NOT EXISTS quantity_issued INTEGER DEFAULT 0 NOT NULL,
                ADD COLUMN IF NOT EXISTS quantity_consumed INTEGER DEFAULT 0 NOT NULL,
                ADD COLUMN IF NOT EXISTS quantity_returned INTEGER DEFAULT 0 NOT NULL,
                ADD COLUMN IF NOT EXISTS notes VARCHAR(300) DEFAULT '' NOT NULL,
                ADD COLUMN IF NOT EXISTS requested_by_id UUID REFERENCES accounts_user(id) ON DELETE SET NULL;

            ALTER TABLE inventory_partreservation
                ADD COLUMN IF NOT EXISTS part_line_id UUID REFERENCES inventory_workorderpart(id) ON DELETE CASCADE,
                ADD COLUMN IF NOT EXISTS work_order_id UUID REFERENCES workorders_workorder(id) ON DELETE CASCADE,
                ADD COLUMN IF NOT EXISTS warehouse_id UUID REFERENCES inventory_warehouse(id) ON DELETE RESTRICT,
                ADD COLUMN IF NOT EXISTS part_id UUID REFERENCES inventory_part(id) ON DELETE RESTRICT,
                ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'ACTIVE' NOT NULL;

            ALTER TABLE inventory_stockmovement
                ADD COLUMN IF NOT EXISTS warehouse_id UUID REFERENCES inventory_warehouse(id) ON DELETE RESTRICT,
                ADD COLUMN IF NOT EXISTS part_id UUID REFERENCES inventory_part(id) ON DELETE RESTRICT,
                ADD COLUMN IF NOT EXISTS part_line_id UUID REFERENCES inventory_workorderpart(id) ON DELETE SET NULL,
                ADD COLUMN IF NOT EXISTS transfer_ref UUID NULL,
                ADD COLUMN IF NOT EXISTS reason VARCHAR(300) DEFAULT '' NOT NULL;
            """,
            reverse_sql="",
        ),
    ]
