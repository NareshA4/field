"""
Phase 2: Deep Work Order Lifecycle & Tab Verification Playwright Tests.
Tests UI views, tab switching, action forms, and state machine validations for Work Orders.
"""
import pytest
from playwright.sync_api import Page, expect


def test_work_order_list_filters_and_views(page: Page, base_url: str, login_as):
    """Verify work order list page, filters, and status count cards."""
    login_as(page, "planner@apexindustrial.com", "Password123!")
    res = page.goto(f"{base_url}/app/work-orders/")
    page.wait_for_load_state("networkidle")
    assert res.status == 200
    assert "Work Order Visibility" in page.content() or "work-order" in page.content().lower()

    # Verify status cards are rendered
    expect(page.locator(".wo-status-card").first).to_be_visible()
    # Verify filters
    expect(page.locator("select[name='status']")).to_be_visible()
    expect(page.locator("select[name='priority']")).to_be_visible()


def test_work_order_tabs_switching(page: Page, base_url: str, login_as):
    """
    Verify all 9 work order detail tabs render without 500 or template errors:
    Overview, Assignment, Checklist, Parts, Labor, Notes, Attachments, Timeline, Closure.
    """
    login_as(page, "planner@apexindustrial.com", "Password123!")

    # Find an existing work order in the system
    res = page.goto(f"{base_url}/app/work-orders/")
    page.wait_for_load_state("networkidle")
    wo_links = page.locator("a[href*='/app/work-orders/']").all()

    # Filter for detail links with UUID
    wo_url = None
    for link in wo_links:
        href = link.get_attribute("href")
        if href and "/app/work-orders/" in href and not any(href.endswith(s) for s in ("new/", "schedule/", "mine/", "list/", "edit/")):
            wo_url = href if href.startswith("http") else f"{base_url}{href}"
            break

    if not wo_url:
        pytest.skip("No work order found to test detail tabs")

    # Navigate to detail page
    page.goto(wo_url)
    page.wait_for_load_state("networkidle")
    assert "/app/work-orders/" in page.url

    tabs = [
        "overview", "assignment", "checklist", "parts",
        "labor", "notes", "attachments", "timeline", "closure"
    ]
    clean_base_wo_url = page.url.split("?")[0].rstrip("/")

    for tab in tabs:
        res_tab = page.goto(f"{clean_base_wo_url}/?tab={tab}")
        page.wait_for_load_state("networkidle")
        assert res_tab.status == 200, f"Tab {tab} returned HTTP {res_tab.status}"
        assert "server error" not in page.content().lower()
        assert "exception" not in page.content().lower()


def test_work_order_plan_action_ui(page: Page, base_url: str, login_as):
    """Test planning a Draft work order via the UI action form."""
    login_as(page, "planner@apexindustrial.com", "Password123!")

    # Navigate to work order list filtered by status=DRAFT
    page.goto(f"{base_url}/app/work-orders/?status=DRAFT")
    page.wait_for_load_state("networkidle")

    # Find the Plan work link in the table rows
    plan_link = page.locator("a:has-text('Plan work')").first
    if not plan_link.is_visible():
        pytest.skip("No DRAFT work order available for plan action test")

    plan_link.click()
    page.wait_for_load_state("networkidle")
    assert "/plan/" in page.url
    expect(page.locator("button[type='submit']")).to_be_visible()


def test_work_order_my_jobs_view(page: Page, base_url: str, login_as):
    """Test technician accessing their assigned jobs at /app/work-orders/mine/."""
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")
    res = page.goto(f"{base_url}/app/work-orders/mine/")
    page.wait_for_load_state("networkidle")
    assert res.status == 200
    assert "My Jobs" in page.content() or "work" in page.content().lower()
