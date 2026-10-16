import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
env_path = root / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

# Ensure src is on python path
sys.path.insert(0, str(root / "src"))
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.dev"

import django
django.setup()

from decimal import Decimal
from apps.workorders.models import WorkOrder, WorkOrderMaterial
from apps.inventory.models import Part, Warehouse, WorkOrderPart, StockBalance
from apps.inventory import services as inv_services
from apps.tenancy.models import Membership

def reset_test_parts():
    wo = WorkOrder.objects.get(order_number="WO-000003")
    if wo.status != WorkOrder.Status.IN_PROGRESS:
        wo.status = WorkOrder.Status.IN_PROGRESS
        wo.save(update_fields=["status"])

    wh, _ = Warehouse.objects.get_or_create(
        organization=wo.organization,
        site=wo.site,
        defaults={"code": "WH-DSC-MAIN", "name": "DSC Site Warehouse", "is_active": True},
    )
    part = Part.objects.filter(organization=wo.organization).first()
    stores = Membership.objects.get(user__email="stores.manager@apexindustrial.example.com")
    planner = Membership.objects.get(user__email="planner@apexindustrial.com")

    # Ensure warehouse stock
    sb = StockBalance.objects.filter(warehouse=wh, part=part).first()
    if not sb or sb.on_hand < 10:
        inv_services.receive(wh, part, "20.000", actor=stores.user, reference="QA-RESET", reason="Stock for QA test")

    line = WorkOrderPart.objects.filter(work_order=wo, part=part).first()
    if not line:
        line = inv_services.request_part(wo, part, "5.000", actor=planner.user, membership=planner)

    # Clean existing material records
    WorkOrderMaterial.objects.filter(work_order=wo, part_line=line).delete()

    line.quantity_requested = Decimal("5.000")
    line.quantity_issued = Decimal("0.000")
    line.quantity_consumed = Decimal("0.000")
    line.quantity_returned = Decimal("0.000")
    line.status = WorkOrderPart.Status.REQUESTED
    line.save()

    # Reserve and issue 5 units
    inv_services.reserve(line, wh, Decimal("5.000"), actor=stores.user)
    inv_services.issue(line, wh, Decimal("5.000"), actor=stores.user)
    line.refresh_from_db()
    print(f"RESET_OK: WO {wo.order_number} ({wo.pk}) Part {part.part_number} Line {line.pk} Outstanding: {line.outstanding}")

if __name__ == "__main__":
    reset_test_parts()
