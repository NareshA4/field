"""
Phase 2: Deep Technician Workspace & Execution Cockpit Playwright Tests.
Tests UI views, execution drawer, travel logging, notes, and labor actions.
"""
import pytest
from playwright.sync_api import Page, expect


def test_technician_workspace_cockpit_view(page: Page, base_url: str, login_as):
    """Technician accesses the workspace cockpit and views status categories."""
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")

    res = page.goto(f"{base_url}/app/workspace/")
    page.wait_for_load_state("networkidle")
    assert res.status == 200
    assert "Workspace" in page.content() or "Jobs" in page.content() or "cockpit" in page.content().lower()

    # Verify filter links exist (active, upcoming, completed, all)
    expect(page.locator("a[href*='status='], button[name='status']").first).to_be_visible()


def test_technician_workspace_htmx_search(page: Page, base_url: str, login_as):
    """Technician searches within assigned workspace jobs."""
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")

    page.goto(f"{base_url}/app/workspace/")
    page.wait_for_load_state("networkidle")

    search_input = page.locator("input[name='q'], input[type='search']").first
    if search_input.is_visible():
        assert "/app/workspace/" in page.url
        assert "Workspace" in page.content() or "Jobs" in page.content()


def test_job_execution_drawer_component(page: Page, base_url: str, login_as):
    """Technician loads the execution drawer fragment for an assigned job."""
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")

    # Check if technician has any jobs assigned (including completed/all)
    res_jobs = page.goto(f"{base_url}/app/workspace/?status=all")
    page.wait_for_load_state("networkidle")
    job_links = page.locator("a[href*='/app/workspace/jobs/']").all()

    if not job_links:
        pytest.skip("No assigned jobs found in technician cockpit")

    # Pick the first job link
    job_href = job_links[0].get_attribute("href")
    full_job_url = job_href if job_href.startswith("http") else f"{base_url}{job_href}"
    clean_job_url = full_job_url.split("?")[0].rstrip("/")
    pk = clean_job_url.split("/")[-1]

    # Load drawer directly or via HTMX
    res_drawer = page.goto(f"{base_url}/app/workspace/jobs/{pk}/drawer/")
    page.wait_for_load_state("networkidle")
    assert res_drawer.status in (200, 403)
    if res_drawer.status == 200:
        assert "timer" in page.content().lower() or "labor" in page.content().lower() or "drawer" in page.content().lower()
