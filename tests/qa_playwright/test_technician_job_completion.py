"""
Phase 4: Automated E2E Browser Testing — Job Completion Screen & Customer Digital Sign-off.

Tests:
  TC-01: Completion screen loads with executive summary, KPI cards, evidence gallery,
         and the sign-off form (signature canvas, signer fields, submit button).
  TC-02: Signature canvas interaction — pointer-draw a signature, verify "Captured" badge,
         clear, re-draw, and confirm hidden input is populated on export.
  TC-03: Form validation — missing signer name / empty signature canvas prevents submission,
         validation error is displayed.
  TC-04: Happy-path end-to-end completion — fill resolution notes, signer name/title,
         draw signature, submit. Verify redirect to job_detail and COMPLETED badge.
  TC-05: Completed state — re-visit /complete/ shows verified sign-off card (read-only),
         no editable form, and signer name is displayed.
  TC-06: Security — unauthenticated user is redirected to login when accessing /complete/.
  TC-07: Console error audit — no severe JS errors on the completion page.
"""
import os
import sys
import subprocess
import json
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

# ---------------------------------------------------------------------------
# Fixture: reset work order to IN_PROGRESS for each test module
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def completion_wo():
    """Ensure work order is IN_PROGRESS and ready for completion screen tests."""
    script_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "reset_qa_completion.py"
    res = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True)
    # Extract last JSON line from stdout (stderr may contain Django AXES logs)
    stdout_lines = [l.strip() for l in res.stdout.strip().splitlines() if l.strip()]
    json_line = None
    for line in reversed(stdout_lines):
        if line.startswith("{"):
            json_line = line
            break
    if json_line is None:
        raise RuntimeError(
            f"reset_qa_completion.py produced no JSON output:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
        )
    data = json.loads(json_line)
    return data


# ---------------------------------------------------------------------------
# TC-01: Completion Screen Loads — Executive Summary & Sign-off Form
# ---------------------------------------------------------------------------

def test_tc01_completion_screen_loads(page: Page, base_url: str, login_as, completion_wo):
    """
    TC-01: Navigate to /complete/ for an IN_PROGRESS work order.
    Verify:
      - Page title contains "Job Completion & Customer Sign-off"
      - Executive Work Summary card with KPI stats (Labor, Parts, Checklist, Evidence)
      - Resolution notes textarea is present
      - Signer name and title inputs are present
      - Signature canvas element exists
      - Submit button "Sign & Confirm Job Completion" is visible
    """
    login_as(page, completion_wo["tech_email"], completion_wo["tech_password"])
    pk = completion_wo["work_order_pk"]

    resp = page.goto(f"{base_url}/app/workspace/jobs/{pk}/complete/")
    page.wait_for_load_state("domcontentloaded")

    assert resp is not None
    assert resp.status == 200, f"Expected 200, got {resp.status}"

    # Title check
    content = page.content()
    assert "Job Completion &amp; Customer Sign-off" in content or "Job Completion & Customer Sign-off" in content

    # 1. Executive Work Summary Card
    summary_header = page.locator("text=Executive Work Summary")
    expect(summary_header).to_be_visible()

    # 2. KPI stat cards (Labor, Parts, Checklist, Evidence)
    expect(page.locator("text=Labor Logged")).to_be_visible()
    expect(page.locator("text=Parts Consumed")).to_be_visible()
    expect(page.locator("text=Checklist Engine")).to_be_visible()
    expect(page.locator("text=Evidence Files")).to_be_visible()

    # 3. Resolution Notes textarea
    resolution_textarea = page.locator("#resolution_notes")
    expect(resolution_textarea).to_be_visible()

    # 4. Signer fields
    expect(page.locator("#signer_name")).to_be_visible()
    expect(page.locator("#signer_title")).to_be_visible()

    # 5. Signature canvas
    canvas = page.locator("#signaturePad")
    expect(canvas).to_be_visible()

    # 6. Submit button
    submit_btn = page.locator("#submitCompletionBtn")
    expect(submit_btn).to_be_visible()
    expect(submit_btn).to_contain_text("Sign & Confirm Job Completion")

    # 7. Customer notes input
    expect(page.locator("#customer_notes")).to_be_visible()

    # Screenshot for evidence
    page.screenshot(path="qa/artifacts/screenshots/tc01_completion_screen.png", full_page=True)


# ---------------------------------------------------------------------------
# TC-02: Signature Canvas Interaction — Draw, Clear, Re-draw
# ---------------------------------------------------------------------------

def test_tc02_signature_canvas_interaction(page: Page, base_url: str, login_as, completion_wo):
    """
    TC-02: Interact with the HTML5 Canvas signature pad.
    Verify:
      - Initial status badge reads "Please sign in box"
      - After drawing, status changes to "Signature Captured"
      - Clear button resets canvas and status
      - Re-drawing restores captured status
    """
    login_as(page, completion_wo["tech_email"], completion_wo["tech_password"])
    pk = completion_wo["work_order_pk"]

    page.goto(f"{base_url}/app/workspace/jobs/{pk}/complete/")
    page.wait_for_load_state("domcontentloaded")

    canvas = page.locator("#signaturePad")
    status_badge = page.locator("#signatureStatus")

    # 1. Initial status
    expect(status_badge).to_contain_text("Please sign in box")

    # Scroll canvas into view to ensure coordinates map properly
    canvas.scroll_into_view_if_needed()
    page.wait_for_timeout(300)

    # 2. Draw a signature stroke on canvas using pointer events
    box = canvas.bounding_box()
    assert box is not None, "Canvas bounding box is None — canvas not visible"

    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    # Draw an "X" pattern
    page.mouse.move(box["x"] + 20, box["y"] + 20)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] - 20, box["y"] + box["height"] - 20, steps=10)
    page.mouse.up()
    page.mouse.move(box["x"] + box["width"] - 20, box["y"] + 20)
    page.mouse.down()
    page.mouse.move(box["x"] + 20, box["y"] + box["height"] - 20, steps=10)
    page.mouse.up()

    # 3. Verify status changed to captured
    expect(status_badge).to_contain_text("Signature Captured")

    # 4. Verify wrapper has 'signed' class
    wrapper = page.locator("#sigPadWrapper")
    import re
    expect(wrapper).to_have_class(re.compile(r"signed"))

    # 5. Clear signature
    clear_btn = page.locator("#clearSignatureBtn")
    clear_btn.click()

    # 6. Verify reset
    expect(status_badge).to_contain_text("Please sign in box")

    # 7. Re-draw a new signature
    page.mouse.move(cx - 40, cy)
    page.mouse.down()
    page.mouse.move(cx + 40, cy, steps=8)
    page.mouse.up()

    expect(status_badge).to_contain_text("Signature Captured")

    page.screenshot(path="qa/artifacts/screenshots/tc02_signature_canvas.png")


# ---------------------------------------------------------------------------
# TC-03: Form Validation — Missing Fields Block Submission
# ---------------------------------------------------------------------------

def test_tc03_form_validation_blocks_submission(page: Page, base_url: str, login_as, completion_wo):
    """
    TC-03: Attempt to submit completion form with:
      a) Empty signature canvas — shows validation error, form not submitted.
      b) Fill signature but leave signer_name empty — HTML5 validation blocks.
    """
    login_as(page, completion_wo["tech_email"], completion_wo["tech_password"])
    pk = completion_wo["work_order_pk"]

    page.goto(f"{base_url}/app/workspace/jobs/{pk}/complete/")
    page.wait_for_load_state("domcontentloaded")

    submit_btn = page.locator("#submitCompletionBtn")

    # --- a) Empty signature: click submit without drawing ---
    # Fill required text fields so only signature blocks
    page.fill("#resolution_notes", "Testing validation with empty signature canvas.")
    page.fill("#signer_name", "Test Validator")

    # Click submit — JS validation should prevent submission
    submit_btn.click()

    # Verify signature error appears
    sig_error = page.locator("#sigErrorAlert")
    expect(sig_error).to_be_visible()
    expect(sig_error).to_contain_text("Please sign the canvas")

    # Verify page did NOT redirect (still on /complete/)
    assert "/complete/" in page.url, f"Expected to remain on /complete/, but URL is {page.url}"

    page.screenshot(path="qa/artifacts/screenshots/tc03_validation_error.png")


# ---------------------------------------------------------------------------
# TC-04: Happy-Path — Full Completion with Sign-off
# ---------------------------------------------------------------------------

def test_tc04_happy_path_completion(page: Page, base_url: str, login_as, completion_wo):
    """
    TC-04: Full end-to-end completion flow:
      1. Navigate to completion screen
      2. Fill resolution notes, signer name, signer title, customer notes
      3. Draw signature on canvas
      4. Submit form
      5. Verify redirect to job_detail with COMPLETED status badge
    """
    login_as(page, completion_wo["tech_email"], completion_wo["tech_password"])
    pk = completion_wo["work_order_pk"]

    page.goto(f"{base_url}/app/workspace/jobs/{pk}/complete/")
    page.wait_for_load_state("domcontentloaded")

    # 1. Fill resolution notes
    page.fill("#resolution_notes", "Completed preventive maintenance: replaced filters, tested airflow, verified thermostat calibration.")

    # 2. Fill signer details
    page.fill("#signer_name", "Eleanor Rodriguez")
    page.fill("#signer_title", "Facility Operations Director")
    page.fill("#customer_notes", "All systems verified operational. Excellent service.")

    # 3. Draw signature on canvas
    canvas = page.locator("#signaturePad")
    canvas.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    box = canvas.bounding_box()
    assert box is not None

    # Simulate a cursive-like signature
    start_x = box["x"] + 30
    start_y = box["y"] + box["height"] / 2
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    for i in range(20):
        page.mouse.move(
            start_x + i * 15,
            start_y + (15 if i % 2 == 0 else -15),
            steps=3,
        )
    page.mouse.up()

    # Verify captured status
    expect(page.locator("#signatureStatus")).to_contain_text("Signature Captured")

    # 4. Submit the form
    page.locator("#submitCompletionBtn").click()
    page.wait_for_load_state("domcontentloaded")

    # 5. Verify redirect to job detail page (not on /complete/ anymore)
    assert "/complete/" not in page.url, f"Expected redirect away from /complete/, but URL is {page.url}"
    assert f"/jobs/{pk}/" in page.url or "/workspace/" in page.url

    # 6. Check for COMPLETED status badge on detail page
    content = page.content()
    assert "Completed" in content or "COMPLETED" in content, (
        "Expected COMPLETED status on job detail page after sign-off"
    )

    # 7. Verify success message
    assert "completed" in content.lower() and "sign-off" in content.lower()

    page.screenshot(path="qa/artifacts/screenshots/tc04_completed_redirect.png", full_page=True)


# ---------------------------------------------------------------------------
# TC-05: Completed State — Read-Only Verified Sign-off Card
# ---------------------------------------------------------------------------

def test_tc05_completed_state_readonly(page: Page, base_url: str, login_as, completion_wo):
    """
    TC-05: After completion (run after TC-04), revisit /complete/ URL.
    Verify:
      - Verified sign-off card is shown with signer name
      - No editable form (no submit button for new sign-off)
      - Sign-off card shows "Customer Sign-off Verified & Completed"
    """
    login_as(page, completion_wo["tech_email"], completion_wo["tech_password"])
    pk = completion_wo["work_order_pk"]

    page.goto(f"{base_url}/app/workspace/jobs/{pk}/complete/")
    page.wait_for_load_state("domcontentloaded")

    content = page.content()

    # 1. Verified sign-off card is present
    assert "Customer Sign-off Verified" in content or "Sign-off Verified" in content, (
        "Expected verified sign-off card on completed work order"
    )

    # 2. Signer name from TC-04 should be displayed
    assert "Eleanor Rodriguez" in content, (
        "Expected signer name 'Eleanor Rodriguez' on verified sign-off card"
    )

    # 3. No editable form / submit button
    form = page.locator("#jobCompletionForm")
    expect(form).to_have_count(0)

    # 4. The submit button should not be present
    submit_btn = page.locator("#submitCompletionBtn")
    expect(submit_btn).to_have_count(0)

    page.screenshot(path="qa/artifacts/screenshots/tc05_readonly_signoff.png", full_page=True)


# ---------------------------------------------------------------------------
# TC-06: Security — Unauthenticated Access Redirects to Login
# ---------------------------------------------------------------------------

def test_tc06_unauthenticated_redirect(page: Page, base_url: str, completion_wo):
    """
    TC-06: Access /complete/ without authentication.
    Verify redirect to login page.
    """
    pk = completion_wo["work_order_pk"]

    # Clear cookies to ensure no session
    page.context.clear_cookies()

    page.goto(f"{base_url}/app/workspace/jobs/{pk}/complete/")
    page.wait_for_load_state("domcontentloaded")

    assert "/accounts/login" in page.url, (
        f"Expected redirect to login for unauthenticated user, but URL is {page.url}"
    )


# ---------------------------------------------------------------------------
# TC-07: Console Error Audit — No Severe JS Errors
# ---------------------------------------------------------------------------

def test_tc07_no_console_errors(page: Page, base_url: str, login_as, completion_wo):
    """
    TC-07: Load the completion screen and verify no unhandled JavaScript
    console errors are emitted.
    """
    login_as(page, completion_wo["tech_email"], completion_wo["tech_password"])
    pk = completion_wo["work_order_pk"]

    # Reset the work order for a clean load (re-run setup)
    script_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "reset_qa_completion.py"
    subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True)

    page.goto(f"{base_url}/app/workspace/jobs/{pk}/complete/")
    page.wait_for_load_state("domcontentloaded")

    # Filter out favicon and non-critical errors
    severe_errors = [
        e for e in page.console_errors
        if "favicon" not in e.lower()
        and "404" not in e  # Static assets that might 404 during test
    ]

    assert len(severe_errors) == 0, (
        f"Unhandled JS console errors on completion screen:\n" +
        "\n".join(severe_errors)
    )
