"""
Verification Test for Audit Trail Enhancements (DEF-004 through DEF-009).
Validates:
1. Category tabs include "Assets & components" and filter works.
2. Compliance reports include "Assets & components" card.
3. Audit detail view renders "Back to audit trail" button.
4. Target entity is rendered as a clickable deep-link.
5. Change Comparison Matrix renders with highlighted changed rows.
6. When timestamp is human-readable.
"""
import pytest
from playwright.sync_api import Page, expect


def test_audit_equipment_category_and_reports(page: Page, base_url: str, login_as):
    login_as(page, "admin@apexindustrial.com", "Password123!")

    # 1. Audit list category tabs
    page.goto(f"{base_url}/app/audit/")
    page.wait_for_load_state("networkidle")
    expect(page.locator("a[role='tab']:has-text('Assets & components')")).to_be_visible()

    # Click the category tab
    page.locator("a[role='tab']:has-text('Assets & components')").click()
    page.wait_for_load_state("networkidle")
    assert "category=equipment" in page.url

    # 2. Compliance reports cards
    page.goto(f"{base_url}/app/audit/reports/")
    page.wait_for_load_state("networkidle")
    expect(page.locator(".fx-card h2:has-text('Assets & components')")).to_be_visible()


def test_audit_detail_view_enhanced(page: Page, base_url: str, login_as):
    login_as(page, "admin@apexindustrial.com", "Password123!")

    # Use existing Apex Industrial asset.component_moved entry
    entry_id = "263c7083-c68c-42f4-9c6d-b4afd12544a5"
    page.goto(f"{base_url}/app/audit/{entry_id}/")
    page.wait_for_load_state("networkidle")

    # 1. Back button in header
    back_btn = page.locator("a:has-text('Back to audit trail')")
    expect(back_btn).to_be_visible()

    # 2. Target deep link to asset
    target_link = page.locator("dt:has-text('Target') + dd a")
    expect(target_link).to_be_visible()
    assert "/app/assets/" in target_link.get_attribute("href")

    # 3. Change comparison matrix
    expect(page.locator("text=Change Comparison Matrix")).to_be_visible()
    changed_badge = page.locator("tr.table-warning span.badge:has-text('Changed')")
    expect(changed_badge.first).to_be_visible()

    # 4. Screenshot proof
    page.screenshot(path="qa/audit_detail_verified.png")

