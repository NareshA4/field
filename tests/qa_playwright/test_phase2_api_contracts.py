"""
Phase 2.2: Django Backend & DRF API Contracts Audit.
Tests response envelope shapes, HTTP status codes, query filtering, pagination, and error responses.
"""
import pytest
from playwright.sync_api import Page


def test_api_endpoints_under_authenticated_session(page: Page, base_url: str, login_as):
    """Verify primary DRF viewsets respond with 200 and standard pagination envelopes."""
    login_as(page, "admin@apexindustrial.com", "Password123!")

    endpoints = [
        "/api/v1/assets/",
        "/api/v1/asset-categories/",
        "/api/v1/sites/",
        "/api/v1/zones/",
        "/api/v1/service-requests/",
        "/api/v1/checklist-templates/",
        "/api/v1/parts/",
        "/api/v1/warehouses/",
        "/api/v1/sla-profiles/",
        "/api/v1/sla-metrics/",
    ]

    for ep in endpoints:
        res = page.request.get(f"{base_url}{ep}")
        assert res.status == 200, f"Endpoint {ep} failed with status {res.status}: {res.text()[:200]}"
        data = res.json()
        if isinstance(data, dict) and "results" in data:
            assert "count" in data, f"Missing 'count' in paginated response for {ep}"
            assert isinstance(data["results"], list), f"'results' should be a list for {ep}"


def test_reproduce_defect_work_orders_api_500(page: Page, base_url: str, login_as):
    """
    DEF-001 Reproduction Test:
    GET /api/v1/work-orders/ triggers AttributeError: module 'apps.workorders.selectors' has no attribute 'filter_work_orders'.
    """
    login_as(page, "admin@apexindustrial.com", "Password123!")
    res = page.request.get(f"{base_url}/api/v1/work-orders/")
    # Currently fails with 500 on unpatched codebase
    assert res.status == 200, f"GET /api/v1/work-orders/ returned HTTP {res.status}: {res.text()}"


def test_api_nonexistent_resource_404_error_envelope(page: Page, base_url: str, login_as):
    """Verify requesting a non-existent UUID returns a structured 404 response."""
    login_as(page, "admin@apexindustrial.com", "Password123!")
    non_existent_uuid = "00000000-0000-0000-0000-000000000000"
    res = page.request.get(f"{base_url}/api/v1/assets/{non_existent_uuid}/")
    assert res.status == 404, f"Expected 404 for non-existent asset, got {res.status}"
    data = res.json()
    assert "detail" in data or "error" in data, f"Expected error detail in 404 response, got {data}"


def test_api_x_request_id_header_present(page: Page, base_url: str, login_as):
    """Verify API responses include X-Request-ID traceability header."""
    login_as(page, "admin@apexindustrial.com", "Password123!")
    res = page.request.get(f"{base_url}/api/v1/assets/")
    headers = res.headers
    assert "x-request-id" in headers or "x-correlation-id" in headers, (
        "Traceability header X-Request-ID missing from DRF API response"
    )
