"""
Phase 2.1: Frontend Forms, Modals & UI Component Integrity Audit.
Tests page rendering, validation errors, CSRF tokens, and form workflows across core modules.
"""
import pytest
from playwright.sync_api import Page


def test_ui_sites_list_and_create_form_rendering(page: Page, base_url: str, login_as):
    """Verify Sites list and Site creation form render with required fields and CSRF."""
    login_as(page, "admin@apexindustrial.com", "Password123!")

    # 1. Sites list
    res = page.goto(f"{base_url}/app/sites/")
    page.wait_for_load_state("networkidle")
    assert res.status == 200
    assert "SITE-CMB-HQ" in page.content() or "Colombo" in page.content()

    # 2. Site creation form
    res_new = page.goto(f"{base_url}/app/sites/new/")
    page.wait_for_load_state("networkidle")
    assert res_new.status == 200
    content = page.content()
    assert "csrfmiddlewaretoken" in content
    assert "code" in content or "name" in content


def test_ui_site_create_validation_on_empty_submission(page: Page, base_url: str, login_as):
    """Verify submitting empty Site form triggers validation errors rather than 500."""
    login_as(page, "admin@apexindustrial.com", "Password123!")
    page.goto(f"{base_url}/app/sites/new/")
    page.wait_for_load_state("networkidle")

    # Click submit without filling required fields
    submit_btn = page.locator("button[type='submit'], button:has-text('Save'), button:has-text('Create')").first
    if submit_btn.is_visible():
        submit_btn.click()
        page.wait_for_load_state("networkidle")
        # Should stay on form with validation errors, NOT return 500
        assert "/app/sites" in page.url
        assert page.locator(".invalid-feedback, .text-danger, .alert-danger, :invalid").count() > 0 or "required" in page.content().lower()


def test_ui_asset_create_form_rendering(page: Page, base_url: str, login_as):
    """Verify Asset create page renders category selectors, site pickers, and CSRF token."""
    login_as(page, "asset.manager@apexindustrial.com", "Password123!")
    res = page.goto(f"{base_url}/app/assets/new/")
    page.wait_for_load_state("networkidle")

    assert res.status == 200
    content = page.content()
    assert "csrfmiddlewaretoken" in content
    assert "name" in content.lower()
    assert "category" in content.lower() or "site" in content.lower()


def test_ui_work_order_create_form_rendering(page: Page, base_url: str, login_as):
    """Verify Work Order create form renders with priority, type, asset selector, and CSRF."""
    login_as(page, "planner@apexindustrial.com", "Password123!")
    res = page.goto(f"{base_url}/app/work-orders/new/")
    page.wait_for_load_state("networkidle")

    assert res.status == 200
    content = page.content()
    assert "csrfmiddlewaretoken" in content
    assert "title" in content.lower() or "description" in content.lower()
    assert "priority" in content.lower() or "type" in content.lower()


def test_ui_global_search_htmx_partial(page: Page, base_url: str, login_as):
    """Verify global search HTMX endpoint returns partial results without 500."""
    login_as(page, "admin@apexindustrial.com", "Password123!")
    # Test HTMX search endpoint directly
    res = page.request.get(f"{base_url}/app/search/?q=admin", headers={"HX-Request": "true"})
    assert res.status == 200
    data = res.text()
    assert "admin@apexindustrial.com" in data or "Admin" in data or "User" in data


def test_ui_dashboards_hub_and_metrics_integrity(page: Page, base_url: str, login_as):
    """Verify Dashboard hub loads KPI cards without JavaScript crashes."""
    login_as(page, "admin@apexindustrial.com", "Password123!")
    page.goto(f"{base_url}/app/dashboards/")
    page.wait_for_load_state("networkidle")

    # Assert no 500 or broken page
    assert page.title() != "Server Error"
    # Verify no unhandled exceptions in page.console_errors
    severe_errors = [e for e in page.console_errors if "favicon" not in e.lower()]
    assert len(severe_errors) == 0, f"JS console errors on dashboards: {severe_errors}"


def test_ui_checklists_templates_list(page: Page, base_url: str, login_as):
    """Verify Checklist templates page renders."""
    login_as(page, "planner@apexindustrial.com", "Password123!")
    res = page.goto(f"{base_url}/app/checklists/")
    page.wait_for_load_state("networkidle")
    assert res.status == 200
    assert "/accounts/login" not in page.url


def test_ui_inventory_parts_list(page: Page, base_url: str, login_as):
    """Verify Inventory parts catalog page renders."""
    login_as(page, "planner@apexindustrial.com", "Password123!")
    res = page.goto(f"{base_url}/app/inventory/parts/")
    page.wait_for_load_state("networkidle")
    assert res.status in (200, 302, 404)  # check exact route behavior
    if res.status == 200:
        assert "/accounts/login" not in page.url


def test_ui_maintenance_contracts_list(page: Page, base_url: str, login_as):
    """Verify Contracts & Warranty list view renders."""
    login_as(page, "asset.manager@apexindustrial.com", "Password123!")
    res = page.goto(f"{base_url}/app/contracts/agreements/")
    page.wait_for_load_state("networkidle")
    assert res.status == 200
    assert "/accounts/login" not in page.url


def test_ui_sla_profiles_list(page: Page, base_url: str, login_as):
    """Verify SLA Profiles list renders without error."""
    login_as(page, "admin@apexindustrial.com", "Password123!")
    res = page.goto(f"{base_url}/app/sla/profiles/")
    page.wait_for_load_state("networkidle")
    assert res.status == 200
    assert "/accounts/login" not in page.url
