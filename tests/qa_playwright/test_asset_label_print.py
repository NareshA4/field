"""
Defect Verification Test for DEF-003:
Validates that asset label sheet print view hides web application shell
(sidebar, topbar, search, user dropdown, page header buttons) during print emulation,
ensuring physical label stickers print cleanly without navigation artifacts.
"""
import pytest
from playwright.sync_api import Page, expect


def test_asset_label_print_view_hides_chrome_shell(page: Page, base_url: str, login_as):
    # 1. Login as Asset Manager or Admin
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")

    # 2. Navigate to asset label view
    asset_id = "fc239415-4344-4911-a3cd-12bdb7b6ad25"
    page.goto(f"{base_url}/app/assets/{asset_id}/label/")
    page.wait_for_load_state("networkidle")

    # In screen mode, label sheet is visible, and page header/topbar/sidebar are visible
    expect(page.locator("#label-sheet")).to_be_visible()
    expect(page.locator(".fx-topbar")).to_be_visible()
    expect(page.locator(".fx-page-header")).to_be_visible()

    # 3. Emulate print media
    page.emulate_media(media="print")

    # 4. Under print media, verify shell elements are NOT visible
    expect(page.locator(".fx-topbar")).not_to_be_visible()
    expect(page.locator(".fx-sidebar")).not_to_be_visible()
    expect(page.locator(".fx-page-header")).not_to_be_visible()

    # 5. Label sheet and asset tag MUST remain visible
    expect(page.locator("#label-sheet")).to_be_visible()
    expect(page.locator("#label-sheet .fw-semibold")).to_contain_text("AFSAF")

    # 6. Capture proof screenshot under print emulation
    page.screenshot(path="qa/print_preview_verified.png")
