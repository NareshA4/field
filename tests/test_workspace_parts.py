"""
Tests for Phase 1 of Technician Parts Consumption:
Validates data selector layer in apps.workspace.selectors for inventory part lines.
"""
from decimal import Decimal
import pytest

from apps.inventory import services as inventory
from apps.workspace import selectors as ws_selectors
from apps.workspace import services as ws_services
from tests.phase3_support import wo_in_progress

pytestmark = pytest.mark.django_db


def test_workspace_selectors_empty_parts(p3):
    """A work order without inventory parts returns empty lists and False flags."""
    wo = wo_in_progress(p3)
    tech = p3["tech"]

    detail = ws_selectors.get_job_detail(tech, str(wo.pk))

    assert detail["part_lines"] == []
    assert detail["consumable_parts"] == []
    assert detail["has_consumable_parts"] is False
    assert detail["consumed_parts"] == []
    assert detail["has_consumed_parts"] is False

    drawer = ws_services.get_drawer_summary(wo, membership=tech)
    assert drawer["consumable_parts"] == []
    assert drawer["consumable_parts_count"] == 0


def test_workspace_selectors_with_issued_parts(inv_):
    """
    When parts are requested and issued to a work order:
    - part_lines_for returns all lines.
    - consumable_parts_for returns issued lines with positive outstanding.
    - get_job_detail context contains consumable_parts and has_consumable_parts=True.
    """
    wo = wo_in_progress(inv_)
    tech = inv_["tech"]
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]

    # 1. Receive stock into warehouse
    inventory.receive(wh, part, "10.000", actor=stores.user, reference="PO-100", reason="Stock in")

    # 2. Request part for the work order
    line = inventory.request_part(wo, part, "3.000", actor=inv_["planner"].user, membership=inv_["planner"])

    # Before issue: requested but quantity_issued is 0
    all_lines = ws_selectors.part_lines_for(wo)
    assert len(all_lines) == 1
    assert all_lines[0].pk == line.pk
    assert ws_selectors.consumable_parts_for(wo) == []

    # 3. Reserve and Issue part
    inventory.reserve(line, wh, "3.000", actor=stores.user)
    inventory.issue(line, wh, "3.000", actor=stores.user)

    line.refresh_from_db()
    assert line.quantity_issued == Decimal("3.000")
    assert line.outstanding == Decimal("3.000")

    # Now consumable_parts_for should return this line
    consumable = ws_selectors.consumable_parts_for(wo)
    assert len(consumable) == 1
    assert consumable[0].pk == line.pk
    assert consumable[0].outstanding == Decimal("3.000")

    # Verify get_job_detail context
    detail = ws_selectors.get_job_detail(tech, str(wo.pk))
    assert len(detail["part_lines"]) == 1
    assert len(detail["consumable_parts"]) == 1
    assert detail["has_consumable_parts"] is True
    assert detail["consumable_parts"][0].part.part_number == "SEAL-100"
    assert detail["has_consumed_parts"] is False

    # Drawer summary should also reflect the consumable parts
    drawer = ws_services.get_drawer_summary(wo, membership=tech)
    assert len(drawer["consumable_parts"]) == 1
    assert drawer["consumable_parts_count"] == 1


def test_consume_job_part_service_happy_path(inv_):
    """
    Technician consumes issued parts:
    - WorkOrderMaterial record is created with part_line link.
    - line.quantity_consumed is incremented.
    - line.outstanding is decremented.
    - Status transitions appropriately.
    """
    from apps.workorders.models import WorkOrderMaterial

    wo = wo_in_progress(inv_)
    tech = inv_["tech"]
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]

    inventory.receive(wh, part, "10.000", actor=stores.user, reference="PO-101", reason="Stock in")
    line = inventory.request_part(wo, part, "3.000", actor=inv_["planner"].user, membership=inv_["planner"])
    inventory.reserve(line, wh, "3.000", actor=stores.user)
    inventory.issue(line, wh, "3.000", actor=stores.user)

    # Technician consumes 2.000 units on-site
    updated_line = ws_services.consume_job_part(
        wo,
        part_line_id=str(line.pk),
        quantity="2.000",
        membership=tech,
        actor=tech.user,
    )

    assert updated_line.quantity_consumed == Decimal("2.000")
    assert updated_line.outstanding == Decimal("1.000")
    assert updated_line.status == "ISSUED"

    # Material ledger record created
    mat = WorkOrderMaterial.objects.get(work_order=wo, part_line=line)
    assert mat.quantity == Decimal("2.000")
    assert mat.part_reference == "SEAL-100"

    # Consume the remaining 1.000 unit
    updated_line2 = ws_services.consume_job_part(
        wo,
        part_line_id=str(line.pk),
        quantity="1.000",
        membership=tech,
        actor=tech.user,
    )

    assert updated_line2.quantity_consumed == Decimal("3.000")
    assert updated_line2.outstanding == Decimal("0.000")
    assert updated_line2.status == "CONSUMED"

    # Now consumable_parts should be empty
    assert ws_selectors.consumable_parts_for(wo) == []


def test_consume_job_part_negative_paths(inv_):
    """
    Negative tests:
    - Attempting to consume more than outstanding raises Conflict.
    - Non-assignee technician attempting consumption raises PermissionDenied.
    - Invalid part line ID raises ValidationFailed.
    """
    from apps.core.exceptions import Conflict, PermissionDenied, ValidationFailed

    wo = wo_in_progress(inv_)
    tech = inv_["tech"]
    tech2 = inv_["tech2"]
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]

    inventory.receive(wh, part, "10.000", actor=stores.user, reference="PO-102", reason="Stock in")
    line = inventory.request_part(wo, part, "2.000", actor=inv_["planner"].user, membership=inv_["planner"])
    inventory.issue(line, wh, "2.000", actor=stores.user)

    # 1. Overconsumption exceeds outstanding
    with pytest.raises(Conflict) as exc:
        ws_services.consume_job_part(
            wo,
            part_line_id=str(line.pk),
            quantity="5.000",
            membership=tech,
            actor=tech.user,
        )
    assert exc.value.code == "exceeds_outstanding"

    # 2. Non-assignee technician cannot consume
    with pytest.raises(PermissionDenied):
        ws_services.consume_job_part(
            wo,
            part_line_id=str(line.pk),
            quantity="1.000",
            membership=tech2,
            actor=tech2.user,
        )

    # 3. Invalid part line
    with pytest.raises(ValidationFailed):
        ws_services.consume_job_part(
            wo,
            part_line_id="00000000-0000-0000-0000-000000000000",
            quantity="1.000",
            membership=tech,
            actor=tech.user,
        )


def test_job_part_consume_html_view(inv_):
    """
    Test standard POST and HTMX POST to workspace:consume_part view.
    """
    from django.test import Client

    wo = wo_in_progress(inv_)
    tech = inv_["tech"]
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]

    inventory.receive(wh, part, "10.000", actor=stores.user, reference="PO-103", reason="Stock in")
    line = inventory.request_part(wo, part, "4.000", actor=inv_["planner"].user, membership=inv_["planner"])
    inventory.issue(line, wh, "4.000", actor=stores.user)

    client = Client()
    client.force_login(tech.user)

    # 1. Standard POST redirects to job_detail
    resp = client.post(
        f"/app/workspace/jobs/{wo.pk}/parts/consume/",
        {"part_line_id": str(line.pk), "quantity": "2.000"},
    )
    assert resp.status_code == 302
    assert resp.url == f"/app/workspace/jobs/{wo.pk}/"

    line.refresh_from_db()
    assert line.quantity_consumed == Decimal("2.000")
    assert line.outstanding == Decimal("2.000")

    # 2. HTMX POST returns 200 with updated drawer content
    resp_htmx = client.post(
        f"/app/workspace/jobs/{wo.pk}/parts/consume/",
        {"part_line_id": str(line.pk), "quantity": "1.000"},
        HTTP_HX_REQUEST="true",
    )
    assert resp_htmx.status_code == 200
    content = resp_htmx.content.decode()
    assert "jobDrawer" in content
    assert "Allocated Parts" in content

    line.refresh_from_db()
    assert line.quantity_consumed == Decimal("3.000")
    assert line.outstanding == Decimal("1.000")


def test_job_part_consume_api_view(inv_):
    """
    Test REST API endpoint for part consumption.
    """
    from rest_framework.test import APIClient

    wo = wo_in_progress(inv_)
    tech = inv_["tech"]
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]

    inventory.receive(wh, part, "10.000", actor=stores.user, reference="PO-104", reason="Stock in")
    line = inventory.request_part(wo, part, "3.000", actor=inv_["planner"].user, membership=inv_["planner"])
    inventory.issue(line, wh, "3.000", actor=stores.user)

    api = APIClient()
    api.force_authenticate(user=tech.user)

    # GET list of part lines
    get_res = api.get(f"/api/v1/workspace/jobs/{wo.pk}/parts/consume/")
    assert get_res.status_code == 200
    assert len(get_res.data) == 1
    assert get_res.data[0]["part_number"] == "SEAL-100"
    assert Decimal(str(get_res.data[0]["outstanding"])) == Decimal("3.000")

    # POST consumption
    post_res = api.post(
        f"/api/v1/workspace/jobs/{wo.pk}/parts/consume/",
        {"part_line_id": str(line.pk), "quantity": "1.500"},
        format="json",
    )
    assert post_res.status_code == 200
    assert Decimal(str(post_res.data["quantity_consumed"])) == Decimal("1.500")
    assert Decimal(str(post_res.data["outstanding"])) == Decimal("1.500")
