# Generated migration to reconcile legacy schema columns across all apps
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('identification', '0002_sync_schema'),
        ('incidents', '0002_sync_schema'),
        ('workorders', '0004_sync_schema'),
        ('inventory', '0002_sync_schema'),
        ('sla', '0003_sync_schema'),
        ('contracts', '0003_sync_schema'),
        ('checklists', '0002_sync_schema'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            DO $$
            DECLARE
                rec RECORD;
                columns_to_drop_not_null TEXT[][] := ARRAY[
                    ['tenancy_organization', 'code'],
                    ['identification_assetidentifier', 'label'],
                    ['identification_scanevent', 'identifier_id'],
                    ['identification_scanevent', 'user_agent'],
                    ['incidents_servicerequest', 'triage_notes'],
                    ['incidents_servicerequest', 'resolution_notes'],
                    ['incidents_downtime', 'is_planned'],
                    ['incidents_downtime', 'service_request_id'],
                    ['incidents_downtime', 'reason'],
                    ['incidents_servicerequesthistory', 'service_request_id'],
                    ['incidents_servicerequesthistory', 'notes'],
                    ['workorders_workorder', 'instructions'],
                    ['workorders_workorder', 'completion_notes'],
                    ['workorders_workorderevent', 'event_type'],
                    ['workorders_workorderevent', 'notes'],
                    ['workorders_workorderlabor', 'date'],
                    ['workorders_workorderlabor', 'description'],
                    ['workorders_workordermaterial', 'unit_of_measure'],
                    ['workorders_workordermaterial', 'item_description'],
                    ['workorders_workordermaterial', 'part_code'],
                    ['inventory_warehouse', 'location_description'],
                    ['inventory_part', 'unit_cost'],
                    ['inventory_part', 'min_level'],
                    ['inventory_part', 'max_level'],
                    ['inventory_part', 'reorder_point'],
                    ['inventory_part', 'min_stock'],
                    ['inventory_part', 'reorder_quantity'],
                    ['inventory_part', 'category'],
                    ['inventory_stockbalance', 'min_level'],
                    ['inventory_stockbalance', 'reorder_quantity'],
                    ['inventory_workorderpart', 'requested_qty'],
                    ['inventory_workorderpart', 'issued_qty'],
                    ['inventory_workorderpart', 'consumed_qty'],
                    ['inventory_workorderpart', 'returned_qty'],
                    ['inventory_workorderpart', 'unit_cost'],
                    ['inventory_workorderpart', 'warehouse_id'],
                    ['inventory_partreservation', 'is_active'],
                    ['inventory_partreservation', 'work_order_part_id'],
                    ['inventory_stockmovement', 'unit_cost'],
                    ['sla_escalationrule', 'target_id'],
                    ['sla_escalationrule', 'role_key'],
                    ['sla_slatracking', 'total_paused_seconds'],
                    ['sla_slaevent', 'payload'],
                    ['contracts_serviceprovider', 'address'],
                    ['checklists_checklistitem', 'is_required'],
                    ['checklists_inspection', 'notes'],
                    ['checklists_inspectionresponse', 'notes'],
                    ['checklists_finding', 'title'],
                    ['checklists_finding', 'resolution']
                ];
                tbl TEXT;
                col TEXT;
                i INT;
            BEGIN
                FOR i IN 1..array_length(columns_to_drop_not_null, 1) LOOP
                    tbl := columns_to_drop_not_null[i][1];
                    col := columns_to_drop_not_null[i][2];
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = tbl AND column_name = col
                    ) THEN
                        EXECUTE format('ALTER TABLE %I ALTER COLUMN %I DROP NOT NULL', tbl, col);
                    END IF;
                END LOOP;
            END $$;
            """,
            reverse_sql="",
        ),
    ]
