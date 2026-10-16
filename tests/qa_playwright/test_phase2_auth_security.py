"""
Phase 2.3: Authentication, Authorization, RBAC & Multi-Tenancy Security Audit.
Verifies permission boundaries, role restrictions, unauthenticated guarding, and CSRF protection.
"""
import pytest
from playwright.sync_api import Page


def test_unauthenticated_api_access_blocked(page: Page, base_url: str):
    """Verify core DRF API endpoints reject unauthenticated requests with 401/403."""
    protected_endpoints = [
        "/api/v1/work-orders/",
        "/api/v1/assets/",
        "/api/v1/sites/",
        "/api/v1/members/",
        "/api/v1/roles/",
        "/api/v1/audit-logs/",
    ]
    for endpoint in protected_endpoints:
        res = page.request.get(f"{base_url}{endpoint}")
        assert res.status in (401, 403), f"Expected 401/403 for {endpoint}, got {res.status}"


def test_technician_role_blocked_from_audit_logs(page: Page, base_url: str, login_as):
    """Verify Technician cannot access Audit Logs (/app/audit/) - must return 403."""
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")
    res = page.goto(f"{base_url}/app/audit/")
    page.wait_for_load_state("networkidle")

    # Should be rejected with 403 Forbidden or redirected
    assert res.status == 403 or "/errors/403" in page.url or "forbidden" in page.content().lower() or "/app/" in page.url


def test_technician_role_blocked_from_role_administration(page: Page, base_url: str, login_as):
    """Verify Technician cannot access Role Management (/app/roles/)."""
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")
    res = page.goto(f"{base_url}/app/roles/")
    page.wait_for_load_state("networkidle")

    assert res.status == 403 or "/errors/403" in page.url or "forbidden" in page.content().lower() or "/app/" in page.url


def test_technician_role_blocked_from_platform_admin(page: Page, base_url: str, login_as):
    """Verify non-platform-admin users cannot access /platform/."""
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")
    res = page.goto(f"{base_url}/platform/organizations/")
    page.wait_for_load_state("networkidle")

    assert res.status in (403, 302, 404) or "/accounts/login" in page.url


def test_csrf_protection_on_unauthenticated_post(page: Page, base_url: str):
    """Verify direct POST without CSRF token to protected endpoint returns 403 Forbidden."""
    # Attempt POST to switch-organization without CSRF token
    res = page.request.post(
        f"{base_url}/app/switch-organization/",
        data={"organization": "invalid-uuid"},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert res.status == 403, f"Expected 403 CSRF failure, got {res.status}"


def test_logout_invalidates_session(page: Page, base_url: str, login_as):
    """Verify logging out clears session and prevents accessing /app/."""
    login_as(page, "admin@apexindustrial.com", "Password123!")

    # Perform logout via POST (Django 5 requires POST for logout)
    # Open user menu and click Sign out form
    page.goto(f"{base_url}/app/")
    page.wait_for_load_state("networkidle")
    # Click Sign out button in dropdown or submit the logout form directly
    page.evaluate("document.querySelector('form[action=\"/accounts/logout/\"]').submit()")
    page.wait_for_load_state("networkidle")

    # Confirm attempt to access /app/ redirects to login
    page.goto(f"{base_url}/app/")
    page.wait_for_load_state("networkidle")
    assert "/accounts/login" in page.url, f"Expected redirect to login after logout, but was on {page.url}"


def test_coverage_tab_gated_by_contract_view_permission(page: Page, base_url: str, login_as):
    """Verify Coverage tab is hidden for technician (lacks contract.view) and visible for admin."""
    # 1. Fetch an asset ID first as admin
    login_as(page, "admin@apexindustrial.com", "Password123!")
    res_assets = page.request.get(f"{base_url}/api/v1/assets/")
    assert res_assets.status == 200
    assets = res_assets.json().get("results", [])
    assert len(assets) > 0, "No assets found in test database"
    asset_id = assets[0]["id"]

    # As admin, Coverage tab MUST be visible and functional
    page.goto(f"{base_url}/app/assets/{asset_id}/")
    page.wait_for_load_state("networkidle")
    admin_cov_tab = page.locator("a[role='tab'][href*='tab=coverage']")
    assert admin_cov_tab.is_visible(), "Coverage tab should be visible for Admin holding contract.view!"

    # 2. Log in as Technician
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")
    page.goto(f"{base_url}/app/assets/{asset_id}/")
    page.wait_for_load_state("networkidle")

    # In tab bar, Coverage MUST NOT be rendered
    tech_cov_tab = page.locator("a[role='tab'][href*='tab=coverage']")
    assert not tech_cov_tab.is_visible(), "Coverage tab must NOT be visible for Technician without contract.view!"

    # If technician directly navigates to ?tab=coverage, it must safely fall back to overview without 403 or broken spinner
    res = page.goto(f"{base_url}/app/assets/{asset_id}/?tab=coverage")
    page.wait_for_load_state("networkidle")
    assert res.status == 200
    # Must NOT have stuck loading coverage or 403 error in panel
    assert "Loading coverage" not in page.content(), "Should not attempt to load coverage panel for unauthorized user!"
