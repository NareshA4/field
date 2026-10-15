"""
Phase 1 Baseline Playwright Acceptance Test Suite (BL-01 to BL-12).
Verifies baseline health across primary user journeys and critical application areas.
"""
import json
import pytest
from playwright.sync_api import Page, expect


def test_bl01_application_health(page: Page, base_url: str):
    """BL-01: Application Health - /health/live/ and /health/ready/ return HTTP 200 OK."""
    # Test live endpoint
    response_live = page.goto(f"{base_url}/health/live/")
    assert response_live is not None
    assert response_live.status == 200, f"Expected 200 for /health/live/, got {response_live.status}"
    body_live = response_live.json()
    assert body_live.get("status") == "ok", f"Expected live status 'ok', got {body_live}"

    # Test ready endpoint
    response_ready = page.goto(f"{base_url}/health/ready/")
    assert response_ready is not None
    assert response_ready.status == 200, f"Expected 200 for /health/ready/, got {response_ready.status}"
    body_ready = response_ready.json()
    assert body_ready.get("status") == "ok", f"Expected ready status 'ok', got {body_ready}"
    assert body_ready.get("checks", {}).get("database") == "ok", "Database health check failed"
    assert body_ready.get("checks", {}).get("cache") == "ok", "Cache health check failed"


def test_bl02_unauthenticated_redirect(page: Page, base_url: str):
    """BL-02: Unauthenticated Redirect - Accessing protected /app/ redirects to login."""
    page.goto(f"{base_url}/app/")
    page.wait_for_load_state("networkidle")
    assert "/accounts/login" in page.url, f"Expected redirect to login, but current URL is: {page.url}"
    assert "next=%2Fapp%2F" in page.url or "next=/app/" in page.url


def test_bl03_valid_authentication(page: Page, base_url: str):
    """BL-03: Valid Authentication - Authenticates successfully and redirects to /app/."""
    page.goto(f"{base_url}/accounts/login/")
    page.fill("input[name='username']", "admin@apexindustrial.com")
    page.fill("input[name='password']", "Password123!")
    page.click("button:has-text('Sign in')")
    page.wait_for_load_state("networkidle")

    assert "/accounts/login" not in page.url, f"Login failed; remained on {page.url}"
    assert "/app" in page.url or page.url.endswith("/"), f"Unexpected post-login URL: {page.url}"

    # Verify session cookie was set
    cookies = page.context.cookies()
    session_cookie = next((c for c in cookies if "session" in c["name"].lower()), None)
    assert session_cookie is not None, "Django session cookie was not set after login"


def test_bl04_invalid_authentication(page: Page, base_url: str):
    """BL-04: Invalid Authentication - Rejects bad credentials and remains on login."""
    page.goto(f"{base_url}/accounts/login/")
    page.fill("input[name='username']", "admin@apexindustrial.com")
    page.fill("input[name='password']", "InvalidPassword999!")
    page.click("button:has-text('Sign in')")
    page.wait_for_load_state("networkidle")

    # Should remain on login page with error indication
    assert "/accounts/login" in page.url, f"Expected to remain on login page, but URL was {page.url}"
    content = page.content()
    # Check for authentication failure alert/error
    has_error_message = (
        "Please enter a correct" in content
        or "alert-danger" in content
        or "invalid" in content.lower()
        or "error" in content.lower()
    )
    assert has_error_message, "No error feedback displayed upon invalid credentials submission"


def test_bl05_session_persistence(page: Page, base_url: str, login_as):
    """BL-05: Session Persistence - Preserves authenticated state across deep link navigation."""
    login_as(page, "admin@apexindustrial.com", "Password123!")

    # Navigate to sites
    response_sites = page.goto(f"{base_url}/app/sites/")
    page.wait_for_load_state("networkidle")
    assert response_sites is not None
    assert response_sites.status == 200
    assert "/accounts/login" not in page.url

    # Navigate to assets
    response_assets = page.goto(f"{base_url}/app/assets/")
    page.wait_for_load_state("networkidle")
    assert response_assets is not None
    assert response_assets.status == 200
    assert "/accounts/login" not in page.url


def test_bl06_shell_navigation(page: Page, base_url: str, login_as):
    """BL-06: Shell Navigation - Sidebar contains functional links to core modules."""
    login_as(page, "admin@apexindustrial.com", "Password123!")
    page.goto(f"{base_url}/app/")
    page.wait_for_load_state("networkidle")

    content = page.content()
    # Verify core navigation items exist in DOM / sidebar
    expected_links = ["/app/sites/", "/app/assets/", "/app/work-orders/", "/app/dashboards/"]
    for link in expected_links:
        assert link in content, f"Expected navigation link {link} not found in shell layout"


def test_bl07_asset_hierarchy_hub(page: Page, base_url: str, login_as):
    """BL-07: Asset Hierarchy Hub - /app/assets/hierarchy/ loads without unhandled JS exceptions."""
    login_as(page, "asset.manager@apexindustrial.com", "Password123!")
    response = page.goto(f"{base_url}/app/assets/hierarchy/")
    page.wait_for_load_state("networkidle")

    assert response is not None
    assert response.status == 200, f"Expected 200 for /app/assets/hierarchy/, got {response.status}"

    # Verify seeded asset tag exists in rendered DOM
    content = page.content()
    assert "AST-CHL-001" in content or "Trane Centrifugal" in content or "Chiller" in content, (
        "Seeded root asset AST-CHL-001 not rendered on hierarchy page"
    )

    # Check for severe JavaScript console errors
    severe_js_errors = [e for e in page.console_errors if "favicon" not in e.lower()]
    assert len(severe_js_errors) == 0, f"Unhandled JS errors detected on hierarchy hub: {severe_js_errors}"


def test_bl08_work_order_board_and_list(page: Page, base_url: str, login_as):
    """BL-08: Work Order Board - Schedule board and work order list load successfully."""
    login_as(page, "planner@apexindustrial.com", "Password123!")

    # Check Work Orders List
    res_list = page.goto(f"{base_url}/app/work-orders/")
    page.wait_for_load_state("networkidle")
    assert res_list is not None
    assert res_list.status == 200, f"Expected 200 for /app/work-orders/, got {res_list.status}"

    # Check Schedule Board
    res_sched = page.goto(f"{base_url}/app/work-orders/schedule/")
    page.wait_for_load_state("networkidle")
    assert res_sched is not None
    assert res_sched.status == 200, f"Expected 200 for /app/work-orders/schedule/, got {res_sched.status}"


def test_bl09_technician_workspace(page: Page, base_url: str, login_as):
    """BL-09: Technician Workspace - /app/workspace/ loads cockpit for technician."""
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")
    response = page.goto(f"{base_url}/app/workspace/")
    page.wait_for_load_state("networkidle")

    assert response is not None
    assert response.status in (200, 302), f"Expected 200 or redirect to my-jobs, got {response.status}"
    if response.status == 302:
        page.wait_for_load_state("networkidle")

    assert "/accounts/login" not in page.url
    # Ensure page rendered without 500 server error
    assert response.status != 500


def test_bl10_operational_dashboard(page: Page, base_url: str, login_as):
    """BL-10: Operational Dashboard - /app/dashboards/ loads without 500 error."""
    login_as(page, "admin@apexindustrial.com", "Password123!")
    response = page.goto(f"{base_url}/app/dashboards/")
    page.wait_for_load_state("networkidle")

    assert response is not None
    assert response.status in (200, 302), f"Expected 200/302 for /app/dashboards/, got {response.status}"
    if response.status == 302:
        page.wait_for_load_state("networkidle")

    # Navigate to my-work dashboard
    res_my_work = page.goto(f"{base_url}/app/dashboards/my-work/")
    page.wait_for_load_state("networkidle")
    assert res_my_work is not None
    assert res_my_work.status == 200, f"Expected 200 for /app/dashboards/my-work/, got {res_my_work.status}"


def test_bl11_static_asset_integrity(page: Page, base_url: str):
    """BL-11: Static Asset Integrity - Crucial CSS and JavaScript assets return 200."""
    critical_assets = [
        "/static/css/app.css?v=2.8",
        "/static/lib/bootstrap.min.css",
        "/static/lib/bootstrap.bundle.min.js",
        "/static/lib/htmx.min.js",
        "/static/js/app.js?v=2.8",
    ]
    for asset_path in critical_assets:
        res = page.goto(f"{base_url}{asset_path}")
        assert res is not None
        assert res.status == 200, f"Static asset {asset_path} failed with HTTP {res.status}"


def test_bl12_api_schema_delivery(page: Page, base_url: str):
    """BL-12: API Schema Delivery - OpenAPI schema and Swagger documentation render."""
    # Test OpenAPI schema endpoint (serves YAML download attachment)
    res_schema = page.request.get(f"{base_url}/api/v1/schema/")
    assert res_schema is not None
    assert res_schema.status == 200, f"OpenAPI schema returned {res_schema.status}"
    schema_text = res_schema.text()
    assert "openapi" in schema_text.lower() or "paths" in schema_text.lower() or "fieldops" in schema_text.lower()

    # Test Swagger UI
    res_docs = page.goto(f"{base_url}/api/v1/docs/")
    page.wait_for_load_state("networkidle")
    assert res_docs is not None
    assert res_docs.status == 200, f"Swagger UI returned {res_docs.status}"
    assert "swagger" in page.title().lower() or "swagger" in page.content().lower()
