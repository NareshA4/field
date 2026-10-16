"""
Phase 2: Deep Incident Management & Conversion Playwright Tests.
Tests UI workflows for reporting, triaging, approving, and converting Incidents to Work Orders.
"""
import pytest
from playwright.sync_api import Page, expect


def test_incident_create_role_guard(page: Page, base_url: str, login_as):
    """Verify role gate: supervisor lacking incident.create receives 403 Forbidden."""
    login_as(page, "supervisor@apexindustrial.com", "Password123!")
    res = page.goto(f"{base_url}/app/incidents/new/")
    page.wait_for_load_state("networkidle")
    assert res.status == 403, f"Expected 403 for supervisor lacking incident.create, got {res.status}"


def test_incident_create_page_renders_cleanly(page: Page, base_url: str, login_as):
    """Verify incident creation form renders with all required form controls for technician/admin."""
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")
    res = page.goto(f"{base_url}/app/incidents/new/")
    page.wait_for_load_state("networkidle")
    assert res.status == 200
    assert "Report an incident / request" in page.content()

    # Form fields exist
    expect(page.locator("select[name='asset'], input[name='asset']").first).to_be_visible()
    expect(page.locator("input[name='title']")).to_be_visible()
    expect(page.locator("textarea[name='description']")).to_be_visible()


def test_incident_end_to_end_lifecycle_to_work_order(page: Page, base_url: str, login_as):
    """
    CM-01 Deep E2E Flow:
    1. Technician logs in and visits /app/incidents/new/.
    2. Submits a new incident for chiller asset.
    3. Verifies redirected to incident detail.
    4. Admin/Planner logs in and approves incident.
    5. Clicks 'Create Work Order' (POST to /create-work-order/).
    6. Verifies redirected to Work Order detail page with linked reference!
    """
    # Step 1: Technician reports incident
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")
    page.goto(f"{base_url}/app/incidents/new/")
    page.wait_for_load_state("networkidle")

    # Select the asset (AST-CHL-001)
    asset_select = page.locator("select[name='asset']")
    asset_options = asset_select.locator("option").all()
    chosen_val = None
    for opt in asset_options:
        val = opt.get_attribute("value")
        if val:
            chosen_val = val
            break
    assert chosen_val, "No asset available in incident creation dropdown!"
    asset_select.select_option(chosen_val)

    import uuid
    run_id = uuid.uuid4().hex[:6]
    incident_title = f"Chiller Safety Trip {run_id}"
    incident_desc = f"Drift reading anomaly detected on secondary pressure valve {run_id}."
    # Fill title and description
    page.fill("input[name='title']", incident_title)
    page.fill("textarea[name='description']", incident_desc)
    
    if page.locator("select[name='kind']").is_visible():
        page.locator("select[name='kind']").select_option("INCIDENT")
    if page.locator("select[name='severity']").is_visible():
        page.locator("select[name='severity']").select_option("CRITICAL")
    if page.locator("select[name='service_impact']").is_visible():
        page.locator("select[name='service_impact']").select_option("PARTIAL_OUTAGE")

    # If duplicate alert appears or submit button is disabled by duplicate detection
    override_btn = page.locator("#enableOverrideButton")
    if override_btn.is_visible():
        override_btn.click()
        page.fill("textarea[name='override_reason'], input[name='override_reason']", "Isolated separate valve anomaly on circuit B")

    submit_btn = page.locator("#submitBtn, button[type='submit'], button:has-text('Submit report')")
    submit_btn.first.click()
    page.wait_for_load_state("networkidle")

    # If duplicate alert appeared on post-submission 409
    if override_btn.is_visible() and page.url.endswith("/new/"):
        override_btn.click()
        page.fill("textarea[name='override_reason'], input[name='override_reason']", "Isolated separate valve anomaly on circuit B")
        page.locator("#submitBtn").click()
        page.wait_for_load_state("networkidle")

    assert "/app/incidents/" in page.url and not page.url.endswith("/new/"), f"Failed to redirect from /new/: {page.content()[:300]}"
    detail_url = page.url
    assert incident_title in page.content()
    incident_pk = detail_url.rstrip("/").split("/")[-1].split("?")[0]

    # Step 2: Admin/Operations logs in to triage and approve
    login_as(page, "admin@apexindustrial.com", "Password123!")
    page.goto(detail_url)
    page.wait_for_load_state("networkidle")

    # Triage if in NEW
    triage_btn = page.locator("button:has-text('Triage')")
    if triage_btn.is_visible():
        triage_btn.click()
        page.wait_for_load_state("networkidle")

    # Approve
    approve_btn = page.locator("button:has-text('Approve')")
    if approve_btn.is_visible():
        approve_btn.click()
        page.wait_for_load_state("networkidle")

    # Step 3: Click Create Work Order
    page.goto(f"{base_url}/app/incidents/{incident_pk}/")
    page.wait_for_load_state("networkidle")
    create_wo_btn = page.locator("button:has-text('Create work order'), button:has-text('Create Work Order')")
    assert create_wo_btn.is_visible(), "Create Work Order button not visible on APPROVED incident!"
    create_wo_btn.click()
    page.wait_for_load_state("networkidle")

    # Verifies redirected to work-orders detail page
    assert "/app/work-orders/" in page.url
    assert incident_title in page.content() or "WO-" in page.content()


def test_incident_duplicate_detection_api(page: Page, base_url: str, login_as):
    """Test the duplicate incident check AJAX/REST endpoint for user with incident.create."""
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")

    # Fetch an asset ID first
    res_assets = page.request.get(f"{base_url}/api/v1/assets/")
    assert res_assets.status == 200
    assets = res_assets.json().get("results", [])
    if not assets:
        pytest.skip("No assets found for duplicate incident test")
    asset_id = assets[0]["id"]

    # Check duplicate endpoint
    res = page.request.get(f"{base_url}/app/incidents/check-duplicate/?asset={asset_id}&kind=INCIDENT")
    assert res.status == 200
    data = res.json()
    assert "duplicate_found" in data
    assert "duplicates" in data
