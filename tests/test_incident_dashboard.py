import pytest
from django.test import Client
from django.urls import reverse

from apps.incidents.models import ServiceRequest

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(("role_key", "expected_title"), [
    ("owner", "Organization Owner"),
    ("operations_manager", "Operations Manager"),
    ("asset_manager", "Asset Manager"),
    ("maintenance_planner", "Maintenance Planner"),
    ("supervisor", "Maintenance Supervisor"),
    ("technician", "Lead Field Technician"),
    ("stores_manager", "Stores / Inventory Manager"),
    ("service_manager", "Service &amp; SLA Manager"),
    ("auditor", "Compliance &amp; Audit Officer"),
    ("client_requester", "Client / Requester"),
])
def test_incident_dashboard_renders_for_each_module_5_role(org_a, make_member, role_key, expected_title):
    member = make_member(org_a, f"dashboard-{role_key}@alpha.test", role_key)
    client = Client()
    client.force_login(member.user)

    response = client.get(reverse("incidents:dashboard"))

    assert response.status_code == 200
    assert expected_title.encode() in response.content


def test_technician_dashboard_does_not_expose_unassigned_incidents(org_a, site_a1, make_asset, make_member):
    from apps.incidents import services as incident_services

    asset = make_asset(org_a, site_a1, "DASHBOARD-PUMP")
    reporter = make_member(org_a, "dashboard-report@alpha.test", "operations_manager")
    technician = make_member(org_a, "dashboard-tech@alpha.test", "technician")
    incident_services.create_request(org_a, asset=asset, reporter=reporter, actor=reporter.user,
                                     title="Visible only after assignment")
    client = Client()
    client.force_login(technician.user)

    response = client.get(reverse("incidents:dashboard"))

    assert response.status_code == 200
    assert b"Visible only after assignment" not in response.content


def test_platform_incident_dashboard_is_cross_organization_and_restricted(platform_admin):
    client = Client()
    client.force_login(platform_admin)

    response = client.get(reverse("platform_admin:incident_dashboard"))

    assert response.status_code == 200
    assert b"Platform Super Admin" in response.content


def test_client_requester_can_report_only_against_an_asset_assigned_to_them(
        org_a, site_a1, make_asset, make_member):
    member = make_member(org_a, "dashboard-client@alpha.test", "client_requester")
    owned = make_asset(org_a, site_a1, "CLIENT-OWNED")
    owned.owner = member
    owned.save(update_fields=["owner"])
    other = make_asset(org_a, site_a1, "CLIENT-OTHER")
    client = Client()
    client.force_login(member.user)

    page = client.get(reverse("incidents:create"))
    assert page.status_code == 200 and b"CLIENT-OWNED" in page.content and b"CLIENT-OTHER" not in page.content
    created = client.post(reverse("incidents:create"), {
        "asset": str(owned.pk), "kind": "INCIDENT", "title": "My assigned asset failed",
        "description": "Noise from the drive.", "severity": "HIGH", "service_impact": "DEGRADED",
    })
    assert created.status_code == 302
    sr = ServiceRequest.objects.get(title="My assigned asset failed")
    assert sr.reported_by_id == member.pk and sr.asset_id == owned.pk
    assert client.get(reverse("incidents:detail", args=[sr.pk])).status_code == 200

    denied = client.post(reverse("incidents:create"), {
        "asset": str(other.pk), "kind": "INCIDENT", "title": "Foreign asset failure",
        "severity": "HIGH", "service_impact": "NONE",
    })
    assert denied.status_code == 400
    assert not ServiceRequest.objects.filter(title="Foreign asset failure").exists()


def test_non_platform_user_cannot_open_cross_organization_dashboard(client, owner_a):
    client.force_login(owner_a)

    response = client.get(reverse("platform_admin:incident_dashboard"))

    assert response.status_code == 403
