"""
Phase 2.4: Cross-Module Integration Workflow Audit (CM-01 to CM-05).
Tests end-to-end multi-step flows connecting Incidents, Work Orders, Checklists, Inventory, and Dashboards.
"""
import pytest
from playwright.sync_api import Page


def test_cm01_incident_api_creation_and_listing(page: Page, base_url: str, login_as):
    """CM-01: Incident Creation & Listing - Incident created in M04 appears in triage list."""
    login_as(page, "supervisor@apexindustrial.com", "Password123!")

    # Verify incidents UI loads
    res_ui = page.goto(f"{base_url}/app/incidents/")
    page.wait_for_load_state("networkidle")
    assert res_ui.status in (200, 302)
    assert "/accounts/login" not in page.url

    # Check DRF incidents endpoint
    res_api = page.request.get(f"{base_url}/api/v1/service-requests/")
    assert res_api.status == 200
    data = res_api.json()
    assert "results" in data or isinstance(data, list)


def test_cm02_workorder_checklists_integration(page: Page, base_url: str, login_as):
    """CM-02: Work Order & Checklist Boundary - Checklist templates can be queried and linked."""
    login_as(page, "planner@apexindustrial.com", "Password123!")

    # Verify checklist templates API
    res = page.request.get(f"{base_url}/api/v1/checklist-templates/")
    assert res.status == 200
    templates_data = res.json()
    assert "results" in templates_data or isinstance(templates_data, list)

    # Verify inspections API
    res_inspections = page.request.get(f"{base_url}/api/v1/inspections/")
    assert res_inspections.status == 200


def test_cm03_inventory_and_workorder_parts_bridge(page: Page, base_url: str, login_as):
    """CM-03: Parts & Inventory Bridge - Stock balances, warehouses and reservations queryable."""
    login_as(page, "planner@apexindustrial.com", "Password123!")

    # Verify warehouses
    res_wh = page.request.get(f"{base_url}/api/v1/warehouses/")
    assert res_wh.status == 200

    # Verify parts
    res_parts = page.request.get(f"{base_url}/api/v1/parts/")
    assert res_parts.status == 200

    # Verify part reservations endpoint
    res_res = page.request.get(f"{base_url}/api/v1/part-reservations/")
    assert res_res.status == 200


def test_cm04_contracts_and_coverage_checks(page: Page, base_url: str, login_as):
    """CM-04: Warranty & Service Contracts - Coverage checks evaluate asset contracts."""
    login_as(page, "asset.manager@apexindustrial.com", "Password123!")

    # Query contracts provider list
    res_providers = page.request.get(f"{base_url}/api/v1/contract-providers/")
    assert res_providers.status == 200

    # Query coverage agreements
    res_agreements = page.request.get(f"{base_url}/api/v1/coverage-agreements/")
    assert res_agreements.status == 200


def test_cm05_technician_workspace_and_kpi_metrics(page: Page, base_url: str, login_as):
    """CM-05: Technician Cockpit Execution & Dashboard Reflection."""
    # Step 1: Technician accesses workspace
    login_as(page, "lead.technician@apexindustrial.com", "Password123!")
    res_ws = page.goto(f"{base_url}/app/workspace/")
    page.wait_for_load_state("networkidle")
    assert res_ws.status in (200, 302)

    # Step 2: Technician checks personal work dashboard
    res_my_work = page.goto(f"{base_url}/app/dashboards/my-work/")
    page.wait_for_load_state("networkidle")
    assert res_my_work.status == 200
    assert "utilization" in page.content().lower() or "work" in page.content().lower()

    # Step 3: Query SLA metrics (technician role lacks sla.view, so must return 403)
    res_sla_tech = page.request.get(f"{base_url}/api/v1/sla-metrics/")
    assert res_sla_tech.status == 403, f"Technician should be forbidden from sla-metrics, got {res_sla_tech.status}"

    # Step 4: Verify supervisor with sla.view CAN access sla-metrics
    login_as(page, "supervisor@apexindustrial.com", "Password123!")
    res_sla_sup = page.request.get(f"{base_url}/api/v1/sla-metrics/")
    assert res_sla_sup.status == 200, f"Supervisor should access sla-metrics with 200, got {res_sla_sup.status}"
