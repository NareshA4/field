from datetime import timedelta
import threading
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from apps.accounts.models import User
from apps.assets.models import Asset
from apps.incidents import services as incident_services
from apps.sites.models import Site
from apps.tenancy.models import Membership, Organization
from apps.workorders.models import WorkOrder
from apps.workorders import services as wo_services


def _create_approved_incident(org, asset, membership, title="Equipment failure"):
    source = incident_services.create_request(
        org,
        asset=asset,
        reporter=membership,
        title=title,
        description="Machine reported critical anomaly.",
        kind="INCIDENT",
        severity="HIGH",
        occurred_at=timezone.now() - timedelta(hours=1),
        actor=membership.user,
    )
    source = incident_services.transition(source, action="triage", actor=membership.user)
    return incident_services.transition(source, action="approve", actor=membership.user)


def test_browser_ui_creation_with_linked_incident(org_a, site_a1, owner_a, make_asset):
    """
    Verify creating a Work Order via browser UI form path (/app/work-orders/new/)
    with an approved incident.
    Ensures work_order.number is non-null, unique, and matches expected format.
    """
    membership = Membership.objects.get(organization=org_a, user=owner_a)
    asset = make_asset(org_a, site_a1, "PUMP-101")
    incident = _create_approved_incident(org_a, asset, membership, title="Pump 2 motor trip")

    ui = Client()
    ui.force_login(owner_a)

    payload = {
        "source_type": "INCIDENT",
        "source_request": incident.pk,
        "site": site_a1.pk,
        "asset": asset.pk,
        "title": "Pump 2 repair",
        "description": "Fix motor trip issue",
        "priority": "HIGH",
    }

    initial_count = WorkOrder.objects.filter(organization=org_a).count()

    response = ui.post(reverse("workorders:create"), payload)

    # Must return 302 redirect to detail view
    assert response.status_code == 302
    assert WorkOrder.objects.filter(organization=org_a).count() == initial_count + 1

    wo = WorkOrder.objects.get(source_request=incident)
    assert response.url == reverse("workorders:detail", kwargs={"pk": wo.pk})

    # Both number and order_number must be populated and equal
    assert wo.number is not None
    assert wo.order_number is not None
    assert wo.number == wo.order_number
    assert wo.number.startswith("WO-")

    # Linked incident must be associated and transitioned
    incident.refresh_from_db()
    assert wo.source_request_id == incident.pk
    assert wo.source_reference == incident.number
    assert wo.source_type == "INCIDENT"
    assert incident.status == "WORK_ORDER_CREATED"


def test_api_creation_path(as_user, org_a, site_a1, owner_a, make_asset):
    """
    Verify Work Order creation via API endpoint /api/v1/work-orders/.
    """
    asset = make_asset(org_a, site_a1, "GEN-200")
    client = as_user(owner_a, org_a)

    payload = {
        "site": str(site_a1.pk),
        "asset": str(asset.pk),
        "title": "Generator oil check",
        "description": "Scheduled oil check",
        "work_type": "PREVENTIVE",
        "priority": "MEDIUM",
    }

    initial_count = WorkOrder.objects.filter(organization=org_a).count()

    response = client.post("/api/v1/work-orders/", payload, format="json")
    assert response.status_code == status.HTTP_201_CREATED, response.data
    assert WorkOrder.objects.filter(organization=org_a).count() == initial_count + 1

    data = response.json()
    assert "id" in data
    assert data["number"].startswith("WO-")
    assert data["order_number"] == data["number"]

    wo = WorkOrder.objects.get(pk=data["id"])
    assert wo.number == data["number"]
    assert wo.order_number == data["number"]


def test_number_generation_sequence_and_tenant_scoping(org_a, org_b, site_a1, site_b1, owner_a, owner_b, make_asset):
    """
    Verify numbering starts at WO-000001, increments cleanly, and is isolated per tenant.
    """
    membership_a = Membership.objects.get(organization=org_a, user=owner_a)
    membership_b = Membership.objects.get(organization=org_b, user=owner_b)

    asset_a = make_asset(org_a, site_a1, "ASSET-A-1")
    asset_b = make_asset(org_b, site_b1, "ASSET-B-1")

    # First WO in org_a
    wo_a1 = wo_services.create_work_order(
        org_a,
        asset=asset_a,
        actor=owner_a,
        membership=membership_a,
        title="First WO in Org A",
    )
    # Second WO in org_a
    wo_a2 = wo_services.create_work_order(
        org_a,
        asset=asset_a,
        actor=owner_a,
        membership=membership_a,
        title="Second WO in Org A",
    )

    # First WO in org_b
    wo_b1 = wo_services.create_work_order(
        org_b,
        asset=asset_b,
        actor=owner_b,
        membership=membership_b,
        title="First WO in Org B",
    )

    assert wo_a1.number == "WO-000001"
    assert wo_a1.order_number == "WO-000001"

    assert wo_a2.number == "WO-000002"
    assert wo_a2.order_number == "WO-000002"

    # Tenant isolation: org_b gets its own WO-000001 independently
    assert wo_b1.number == "WO-000001"
    assert wo_b1.order_number == "WO-000001"


import pytest


@pytest.mark.django_db(transaction=True)
def test_concurrent_work_order_creation_handles_sequences_without_duplicates(org_a, site_a1, owner_a, make_asset):
    """
    Verify concurrent creation requests produce unique numbers with no duplicates.
    """
    membership = Membership.objects.get(organization=org_a, user=owner_a)
    asset = make_asset(org_a, site_a1, "CONCUR-ASSET")

    created_orders = []
    errors = []

    def _worker(idx):
        from django.db import connection
        try:
            wo = wo_services.create_work_order(
                org_a,
                asset=asset,
                actor=owner_a,
                membership=membership,
                title=f"Concurrent order {idx}",
            )
            created_orders.append(wo)
        except Exception as e:
            errors.append(e)
        finally:
            connection.close()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Errors during concurrent creation: {errors}"
    assert len(created_orders) == 5

    numbers = [wo.number for wo in created_orders]
    assert len(numbers) == len(set(numbers)), f"Duplicate numbers found: {numbers}"
    for n in numbers:
        assert n.startswith("WO-")


def test_rollback_behavior_on_failure(org_a, site_a1, owner_a, make_asset):
    """
    Verify rollback when creation fails after numbering generation:
    no corrupt or partially created records.
    """
    membership = Membership.objects.get(organization=org_a, user=owner_a)
    asset = make_asset(org_a, site_a1, "ROLLBACK-ASSET")

    initial_count = WorkOrder.objects.filter(organization=org_a).count()

    try:
        with transaction.atomic():
            wo = wo_services.create_work_order(
                org_a,
                asset=asset,
                actor=owner_a,
                membership=membership,
                title="Valid title that gets rolled back",
            )
            assert wo.number.startswith("WO-")
            # Trigger deliberate failure in transaction
            raise RuntimeError("Database transaction aborted after numbering")
    except RuntimeError:
        pass

    assert WorkOrder.objects.filter(organization=org_a).count() == initial_count
