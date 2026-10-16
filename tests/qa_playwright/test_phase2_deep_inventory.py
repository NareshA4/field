"""
Phase 2: Deep Inventory, Warehouses, and Stock Management Playwright Tests.
Tests UI views, part creation, warehouse provisioning, and inventory ledger visibility.
"""
import pytest
from playwright.sync_api import Page, expect


def test_inventory_parts_catalog_and_creation(page: Page, base_url: str, login_as):
    """Admin creates a new part in catalog and views its detail page."""
    login_as(page, "admin@apexindustrial.com", "Password123!")

    # Step 1: Open Parts Catalog
    res = page.goto(f"{base_url}/app/inventory/parts/")
    page.wait_for_load_state("networkidle")
    assert res.status == 200
    assert "Parts" in page.content()

    # Step 2: Navigate to Create Part form
    res_new = page.goto(f"{base_url}/app/inventory/parts/new/")
    page.wait_for_load_state("networkidle")
    assert res_new.status == 200

    # Fill form
    page.fill("input[name='part_number']", "PRT-FLT-9000")
    page.fill("input[name='name']", "High Efficiency Oil Filter 50um")
    if page.locator("input[name='unit_of_measure']").is_visible():
        page.fill("input[name='unit_of_measure']", "EA")
    if page.locator("select[name='unit_of_measure']").is_visible():
        page.locator("select[name='unit_of_measure']").select_option("EA")
    if page.locator("textarea[name='description']").is_visible():
        page.fill("textarea[name='description']", "Replacement cartridge for chiller lubricating system.")

    # Submit
    page.click("button.btn-primary")
    page.wait_for_load_state("networkidle")

    # Verifies redirect to detail
    assert "/app/inventory/parts/" in page.url
    assert "PRT-FLT-9000" in page.content()
    assert "High Efficiency Oil Filter 50um" in page.content()


def test_inventory_warehouses_lifecycle(page: Page, base_url: str, login_as):
    """Admin provisions a warehouse and checks warehouse detail."""
    login_as(page, "admin@apexindustrial.com", "Password123!")

    # Step 1: Warehouses list
    res = page.goto(f"{base_url}/app/inventory/warehouses/")
    page.wait_for_load_state("networkidle")
    assert res.status == 200
    assert "Warehouses" in page.content()

    # Step 2: Open creation form
    res_new = page.goto(f"{base_url}/app/inventory/warehouses/new/")
    page.wait_for_load_state("networkidle")
    assert res_new.status == 200

    # Select site
    site_select = page.locator("select[name='site']")
    options = site_select.locator("option").all()
    chosen_site = None
    for opt in options:
        val = opt.get_attribute("value")
        if val:
            chosen_site = val
            break
    assert chosen_site, "No site found in warehouse creation dropdown"
    site_select.select_option(chosen_site)

    # Fill details
    page.fill("input[name='code']", "WH-TEST-01")
    page.fill("input[name='name']", "Secondary QA Storage Bay")
    if page.locator("textarea[name='description']").is_visible():
        page.fill("textarea[name='description']", "Buffer storage for HVAC spares.")

    # Submit
    page.click("button.btn-primary")
    page.wait_for_load_state("networkidle")

    # Verifies redirect to warehouse detail
    assert "/app/inventory/warehouses/" in page.url
    assert "WH-TEST-01" in page.content()


def test_inventory_stock_balance_ledger_view(page: Page, base_url: str, login_as):
    """Verify stock balances ledger renders cleanly."""
    login_as(page, "planner@apexindustrial.com", "Password123!")
    res = page.goto(f"{base_url}/app/inventory/stock/")
    page.wait_for_load_state("networkidle")
    assert res.status == 200
    assert "Stock" in page.content() or "Inventory" in page.content()


def test_inventory_reservations_and_movements_views(page: Page, base_url: str, login_as):
    """Verify reservations and stock movements audit views."""
    login_as(page, "planner@apexindustrial.com", "Password123!")

    # Reservations
    res_res = page.goto(f"{base_url}/app/inventory/reservations/")
    page.wait_for_load_state("networkidle")
    assert res_res.status == 200
    assert "Reservation" in page.content()

    # Movements
    res_mov = page.goto(f"{base_url}/app/inventory/movements/")
    page.wait_for_load_state("networkidle")
    assert res_mov.status == 200
    assert "Movement" in page.content()
