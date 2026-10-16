"""
Test Suite: FON-M09 Warehouse Inventory Overview UI and Min-Max Automated Reorder Alerts
Location: tests/test_m09_overview_alerts.py
"""
from decimal import Decimal
import pytest
from django.conf import settings
from django.test import Client

from apps.inventory import selectors, services
from apps.inventory.models import StockBalance
from apps.inventory.tasks import check_reorder_alerts_for_org, fan_out_reorder_alerts
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db


def client_for(user):
    c = Client()
    c.force_login(user)
    return c


def test_warehouse_overview_metrics_calculation(inv_):
    """Selectors correctly calculate aggregate inventory KPIs for the tenant."""
    org, wh, wh2, part, stores = inv_["org"], inv_["wh"], inv_["wh2"], inv_["part"], inv_["stores"]

    # Initial metrics before stock
    m = selectors.warehouse_overview_metrics(stores, org)
    assert m["total_warehouses"] == 3
    assert m["active_warehouses"] == 3
    assert m["total_skus"] == 0
    assert m["total_on_hand"] == Decimal("0")
    assert m["low_stock_count"] == 0

    # Receive stock for part (min_stock=2, max_stock=20, reorder=10)
    # Receive 1 -> below min -> is_low=True
    services.receive(wh, part, "1.000", actor=stores.user)
    m = selectors.warehouse_overview_metrics(stores, org)
    assert m["total_skus"] == 1
    assert m["total_on_hand"] == Decimal("1.000")
    assert m["total_available"] == Decimal("1.000")
    assert m["low_stock_count"] == 1

    # Receive 5 more -> on_hand=6 -> above min -> is_low=False
    services.receive(wh, part, "5.000", actor=stores.user)
    m = selectors.warehouse_overview_metrics(stores, org)
    assert m["total_on_hand"] == Decimal("6.000")
    assert m["low_stock_count"] == 0


def test_warehouses_with_summary_per_warehouse(inv_):
    """Warehouses selector attaches SKU count, on-hand units, and low-stock count."""
    org, wh, wh2, part, part2, stores = (
        inv_["org"], inv_["wh"], inv_["wh2"], inv_["part"], inv_["part2"], inv_["stores"]
    )

    services.receive(wh, part, "1.000", actor=stores.user)  # Part 1 low in WH 1
    services.receive(wh, part2, "10.000", actor=stores.user)  # Part 2 healthy in WH 1
    services.receive(wh2, part2, "5.000", actor=stores.user)  # Part 2 in WH 2

    summaries = selectors.warehouses_with_summary(stores, org)
    by_code = {w.code: w for w in summaries}

    assert by_code["MAIN"].sku_count == 2
    assert by_code["MAIN"].total_on_hand == Decimal("11.000")
    assert by_code["MAIN"].low_stock_count == 1  # part is low

    assert by_code["VAN1"].sku_count == 1
    assert by_code["VAN1"].total_on_hand == Decimal("5.000")
    assert by_code["VAN1"].low_stock_count == 0


def test_warehouse_overview_ui_rendering_and_alerts(inv_):
    """Warehouse overview UI renders KPI cards, reorder alert banner, and warehouse metrics."""
    org, wh, part, stores = inv_["org"], inv_["wh"], inv_["part"], inv_["stores"]
    c = client_for(stores.user)

    # 1. Under minimum stock (on_hand=1 <= min_stock=2)
    services.receive(wh, part, "1.000", actor=stores.user)

    resp = c.get("/app/inventory/warehouses/")
    assert resp.status_code == 200
    html = resp.content.decode()

    # KPI cards rendered
    assert "Warehouse Inventory Overview" in html
    assert "Stocked SKUs" in html
    assert "Min-Max Reorder Alerts" in html
    assert "Low Stock" in html

    # Warning banner when low stock items exist
    assert "Automated Min-Max Alert" in html or "Requires replenishment" in html

    # Stock overview filter supports low=1
    resp_stock_low = c.get("/app/inventory/stock/?low=1")
    assert resp_stock_low.status_code == 200
    assert "SEAL-100" in resp_stock_low.content.decode()


def test_warehouse_detail_ui_rendering(inv_):
    """Warehouse detail view shows specific KPI summary cards and reorder status."""
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]
    c = client_for(stores.user)

    services.receive(wh, part, "1.000", actor=stores.user)
    resp = c.get(f"/app/inventory/warehouses/{wh.pk}/")
    assert resp.status_code == 200
    html = resp.content.decode()

    assert "Stocked SKUs" in html
    assert "On Hand Stock" in html
    assert "Reorder Alerts" in html
    assert "Low Stock" in html


def test_min_max_reorder_celery_task_and_notifications(inv_):
    """Automated min-max Celery task generates warning notifications to store managers."""
    org, wh, part, stores = inv_["org"], inv_["wh"], inv_["part"], inv_["stores"]

    # Incur low stock
    services.receive(wh, part, "1.000", actor=stores.user)
    bal = StockBalance.objects.get(warehouse=wh, part=part)
    assert bal.is_low

    # Celery Beat schedule verification
    beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})
    assert "inventory-reorder-alerts" in beat_schedule
    assert beat_schedule["inventory-reorder-alerts"]["task"] == "apps.inventory.tasks.fan_out_reorder_alerts"

    # Run check task via Celery apply
    result = check_reorder_alerts_for_org.apply(args=[str(org.pk)]).result
    assert result["alerts_created"] >= 1

    # Notification created for store manager
    notif = Notification.objects.filter(
        organization=org,
        source="inventory.reorder",
    ).first()
    assert notif is not None
    assert "Low stock alert" in notif.title
    assert "SEAL-100" in notif.title
    assert notif.level == Notification.Level.WARNING

    # Idempotent (running again doesn't spam duplicate unread notifications)
    result_repeat = check_reorder_alerts_for_org.apply(args=[str(org.pk)]).result
    assert result_repeat["alerts_created"] == 0
