"""
Phase 4: Automated E2E Browser Testing for Technician Parts Consumption Panel.
Tests:
1. Job Detail View: Allocated Inventory Parts card, dropdown selector, dynamic summary, and ledger table.
2. Form Action: Partial consumption with auto-fill "Use All" and submission.
3. HTMX Slide-in Drawer: Quick-consumption form updates drawer dynamically.
4. Terminal State: Consuming remaining balance transitions status to CONSUMED and shows completion state.
5. Security / RBAC: Unauthorized user cannot execute parts consumption.
"""
import os
import sys
import subprocess
from pathlib import Path
import pytest
from playwright.sync_api import Page, expect

WO_PK = "55f095fb-fe9f-4b99-be70-605112f668a2"
PART_NUMBER = "PUMP-SEAL-20"
PART_LINE_PK = "3605ddba-fe86-4f62-8413-a9f2b9cbced0"


@pytest.fixture(scope="module")
def setup_parts_work_order():
    """Ensure WO-000003 is IN_PROGRESS and has an issued part line with 5.000 outstanding."""
    script_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "reset_qa_parts.py"
    res = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Reset script failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
    return {
        "work_order_pk": WO_PK,
        "order_number": "WO-000003",
        "part_number": PART_NUMBER,
        "part_line_pk": PART_LINE_PK,
    }


def test_technician_job_detail_allocated_parts_ui(page: Page, base_url: str, login_as, setup_parts_work_order):
    """
    Test Step 1:
    Verify Allocated Inventory Parts panel, dropdown selection, dynamic info card,
    'Use All' helper button, and initial ledger table on Job Detail page.
    """
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")
    pk = setup_parts_work_order["work_order_pk"]
    part_number = setup_parts_work_order["part_number"]

    page.goto(f"{base_url}/app/workspace/jobs/{pk}/")
    page.wait_for_load_state("networkidle")

    # 1. Verify Allocated Inventory Parts Card exists
    parts_section = page.locator("#parts-consumption-section")
    expect(parts_section).to_be_visible()
    expect(parts_section.locator("h5")).to_contain_text("Allocated Inventory Parts")

    # 2. Verify badge indicates available parts to consume
    expect(parts_section.locator(".badge:has-text('available to consume')")).to_be_visible()

    # 3. Verify dropdown selector contains the issued part
    select = page.locator("#part-line-select")
    expect(select).to_be_visible()
    expect(select).to_contain_text(part_number)

    # 4. Verify selection summary shows warehouse and available outstanding
    expect(page.locator("#summary-wh")).not_to_have_text("-")
    expect(page.locator("#summary-outstanding")).to_contain_text("5")

    # 5. Verify "Use All" button populates the input field
    qty_input = page.locator("#part-quantity-input")
    expect(qty_input).to_be_visible()
    page.locator("#btn-use-all").click()
    assert qty_input.input_value().startswith("5")

    # 6. Verify Allocated Parts Ledger Table renders with initial status ISSUED
    ledger_table = page.locator("#parts-consumption-section table")
    expect(ledger_table).to_be_visible()
    expect(ledger_table.locator("tbody tr").first).to_contain_text(part_number)
    expect(ledger_table.locator("span.badge:has-text('ISSUED')")).to_be_visible()

    # Screenshot
    page.screenshot(path="qa/artifacts/parts_panel_initial.png")


def test_technician_consumes_part_on_job_detail_success(page: Page, base_url: str, login_as, setup_parts_work_order):
    """
    Test Step 2:
    Technician enters partial quantity (2.000) and clicks 'Confirm Quantity Used'.
    Verifies outstanding drops from 5 to 3 and Used increments to 2.
    """
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")
    pk = setup_parts_work_order["work_order_pk"]

    page.goto(f"{base_url}/app/workspace/jobs/{pk}/")
    page.wait_for_load_state("networkidle")

    # Fill 2.000 quantity
    qty_input = page.locator("#part-quantity-input")
    qty_input.fill("2.000")

    # Submit
    page.locator("#btn-confirm-consumption").click()
    page.wait_for_load_state("networkidle")

    # Verify updated values in ledger table
    ledger_row = page.locator("#parts-consumption-section table tbody tr").first
    expect(ledger_row.locator("td:nth-child(4)")).to_contain_text("2")  # Used column
    expect(ledger_row.locator("td:nth-child(5)")).to_contain_text("3")  # Outstanding column

    # Verify summary outstanding reflects 3
    expect(page.locator("#summary-outstanding")).to_contain_text("3")

    # Screenshot
    page.screenshot(path="qa/artifacts/parts_consumed_partial.png")


def test_technician_drawer_quick_consume_htmx(page: Page, base_url: str, login_as, setup_parts_work_order):
    """
    Test Step 3:
    Technician opens slide-in execution drawer and uses inline quick-consume widget via HTMX.
    Consumes 1.000 unit; verifies remaining in drawer updates to 2.000 without full page reload.
    """
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")
    pk = setup_parts_work_order["work_order_pk"]

    page.goto(f"{base_url}/app/workspace/jobs/{pk}/")
    page.wait_for_load_state("networkidle")

    # Open Execution Drawer via button
    page.locator("button:has-text('Execution Drawer')").click()
    page.wait_for_selector("#technicianExecutionDrawer.show, #technicianExecutionDrawer .card:has-text('Allocated Parts')")

    # Verify drawer allocated parts card
    drawer_parts = page.locator("#technicianExecutionDrawer .card:has-text('Allocated Parts')")
    expect(drawer_parts).to_be_visible()

    # Fill quantity 1.000 in drawer form
    drawer_form = drawer_parts.locator("form")
    drawer_form.locator("input[name='quantity']").fill("1.000")

    # Submit HTMX form
    drawer_form.locator("button:has-text('Consume Part')").click()
    page.wait_for_load_state("networkidle")

    # Assert drawer updated and outstanding shows 2
    expect(page.locator("#jobDrawer")).to_be_visible()
    expect(page.locator("#jobDrawer")).to_contain_text("Rem: 2")

    # Screenshot
    page.screenshot(path="qa/artifacts/drawer_parts_consumed.png")


def test_technician_consume_remaining_transitions_to_consumed(page: Page, base_url: str, login_as, setup_parts_work_order):
    """
    Test Step 4:
    Technician consumes remaining 2.000 units on Job Detail page.
    Verifies line transitions to CONSUMED status and all-consumed alert appears.
    """
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")
    pk = setup_parts_work_order["work_order_pk"]

    page.goto(f"{base_url}/app/workspace/jobs/{pk}/")
    page.wait_for_load_state("networkidle")

    # Fill remaining 2.000
    qty_input = page.locator("#part-quantity-input")
    page.locator("#btn-use-all").click()
    if not qty_input.input_value() or qty_input.input_value() == "0":
        qty_input.fill("2.000")

    page.locator("#btn-confirm-consumption").click()
    page.wait_for_load_state("networkidle")

    # Assert completion alert is shown
    expect(page.locator("text=All warehouse parts allocated for this job have been consumed")).to_be_visible()

    # Assert ledger table status badge is CONSUMED
    expect(page.locator("span.badge:has-text('CONSUMED')")).to_be_visible()

    # Screenshot
    page.screenshot(path="qa/artifacts/parts_fully_consumed.png")


def test_security_unauthorized_user_cannot_consume_part(page: Page, base_url: str, login_as, setup_parts_work_order):
    """
    Test Step 5:
    A user lacking permissions cannot consume parts on this work order.
    Direct POST to consume endpoint returns 403 Forbidden.
    """
    login_as(page, "client.requester@acmecorp.example.com", "Password123!")
    pk = setup_parts_work_order["work_order_pk"]
    line_pk = setup_parts_work_order["part_line_pk"]

    # Attempt POST using page request API
    res = page.request.post(
        f"{base_url}/app/workspace/jobs/{pk}/parts/consume/",
        data={"part_line_id": line_pk, "quantity": "1.000"},
    )
    assert res.status in (403, 302, 404), f"Expected forbidden or redirect, got {res.status}"
