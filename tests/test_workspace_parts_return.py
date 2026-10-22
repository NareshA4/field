"""
Tests for Add-On 3: "Return Unused Parts" Action.
Validates technician stock return directly from the workspace:
- apps.workspace.services.return_job_part
- apps.workspace.selectors (returned_parts, has_returned_parts)
- apps.workspace.views.JobPartReturnView (standard POST + HTMX drawer swap)
- apps.workspace.api_views.JobPartReturnAPIView (REST endpoint)
- Authoritative warehouse balance replenishment and audit recording
"""
from decimal import Decimal
import pytest

from apps.audit.models import AuditLog
from apps.core.exceptions import Conflict, PermissionDenied, ValidationFailed
from apps.inventory import services as inventory
from apps.inventory.models import StockBalance, StockMovement
from apps.workspace import selectors as ws_selectors
from apps.workspace import services as ws_services
from tests.phase3_support import wo_in_progress

pytestmark = pytest.mark.django_db


def test_return_job_part_service_happy_path(inv_):
    """
    Technician returns issued-but-unused parts to warehouse:
    - Originating warehouse balance is incremented.
    - line.quantity_returned is incremented.
    - line.outstanding is decremented.
    - StockMovement is recorded with Type.RETURN.
    - Audit entry 'workspace.part_returned' is created.
    """
    wo = wo_in_progress(inv_)
    tech = inv_["tech"]
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]

    # Receive 10 units, request 5, issue 5
    inventory.receive(wh, part, "10.000", actor=stores.user, reference="PO-RET-01", reason="Initial stock")
    line = inventory.request_part(wo, part, "5.000", actor=inv_["planner"].user, membership=inv_["planner"])
    inventory.issue(line, wh, "5.000", actor=stores.user)

    line.refresh_from_db()
    assert line.quantity_issued == Decimal("5.000")
    assert line.outstanding == Decimal("5.000")
    assert line.quantity_returned == Decimal("0.000")

    # Warehouse balance after issue should be 5 on hand (10 received - 5 issued)
    bal = StockBalance.objects.get(warehouse=wh, part=part)
    assert bal.on_hand == Decimal("5.000")

    # Technician returns 2 units of surplus stock
    updated_line = ws_services.return_job_part(
        wo,
        part_line_id=str(line.pk),
        quantity="2.000",
        reason="Surplus unused on job site",
        membership=tech,
        actor=tech.user,
    )

    assert updated_line.quantity_returned == Decimal("2.000")
    assert updated_line.outstanding == Decimal("3.000")

    # Warehouse on-hand balance is replenished from 5 to 7
    bal.refresh_from_db()
    assert bal.on_hand == Decimal("7.000")

    # StockMovement of Type.RETURN created
    mv = StockMovement.objects.filter(part_line=line, movement_type=StockMovement.Type.RETURN).first()
    assert mv is not None
    assert mv.quantity == Decimal("2.000")
    assert mv.on_hand_delta == Decimal("2.000")

    # Audit recorded
    audit_entry = AuditLog.objects.filter(
        target_id=str(wo.pk),
        action="workspace.part_returned",
    ).first()
    assert audit_entry is not None
    assert audit_entry.metadata["quantity_returned"] == "2.000"
    assert audit_entry.metadata["part_number"] == part.part_number


def test_return_job_part_selectors_and_drawer_summary(inv_):
    """
    Verify returned_parts and has_returned_parts in selectors and drawer summary:
    - Before return: returned_parts is empty, has_returned_parts is False.
    - After partial return: returned_parts contains the line, has_returned_parts is True.
    """
    wo = wo_in_progress(inv_)
    tech = inv_["tech"]
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]

    inventory.receive(wh, part, "10.000", actor=stores.user, reference="PO-RET-02", reason="Stock in")
    line = inventory.request_part(wo, part, "4.000", actor=inv_["planner"].user, membership=inv_["planner"])
    inventory.issue(line, wh, "4.000", actor=stores.user)

    detail = ws_selectors.get_job_detail(tech, str(wo.pk))
    assert detail["returned_parts"] == []
    assert detail["has_returned_parts"] is False

    # Return 1 unit
    ws_services.return_job_part(
        wo,
        part_line_id=str(line.pk),
        quantity="1.000",
        reason="Minor surplus",
        membership=tech,
        actor=tech.user,
    )

    detail_after = ws_selectors.get_job_detail(tech, str(wo.pk))
    assert len(detail_after["returned_parts"]) == 1
    assert detail_after["has_returned_parts"] is True
    assert detail_after["returned_parts"][0].pk == line.pk
    assert detail_after["returned_parts"][0].quantity_returned == Decimal("1.000")

    drawer = ws_services.get_drawer_summary(wo, membership=tech)
    assert drawer["has_returned_parts"] is True
    assert drawer["returned_parts_count"] == 1


def test_consume_and_return_full_cycle(inv_):
    """
    Full lifecycle combination:
    - 4 units issued.
    - Technician consumes 2 units.
    - Technician returns remaining 2 units.
    - Outstanding becomes 0, consumable_parts becomes empty.
    """
    wo = wo_in_progress(inv_)
    tech = inv_["tech"]
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]

    inventory.receive(wh, part, "10.000", actor=stores.user, reference="PO-RET-03", reason="Stock in")
    line = inventory.request_part(wo, part, "4.000", actor=inv_["planner"].user, membership=inv_["planner"])
    inventory.issue(line, wh, "4.000", actor=stores.user)

    # 1. Consume 2 units
    ws_services.consume_job_part(
        wo,
        part_line_id=str(line.pk),
        quantity="2.000",
        membership=tech,
        actor=tech.user,
    )
    line.refresh_from_db()
    assert line.quantity_consumed == Decimal("2.000")
    assert line.outstanding == Decimal("2.000")

    # 2. Return remaining 2 units
    ws_services.return_job_part(
        wo,
        part_line_id=str(line.pk),
        quantity="2.000",
        reason="Job completed with 2 unused units",
        membership=tech,
        actor=tech.user,
    )
    line.refresh_from_db()
    assert line.quantity_consumed == Decimal("2.000")
    assert line.quantity_returned == Decimal("2.000")
    assert line.outstanding == Decimal("0.000")

    # Outstanding is now 0, so no consumable/returnable parts left
    assert ws_selectors.consumable_parts_for(wo) == []


def test_return_job_part_negative_paths(inv_):
    """
    Negative tests:
    - Returning more than outstanding raises Conflict(code='exceeds_outstanding').
    - Non-assignee technician cannot return parts (PermissionDenied).
    - Invalid part line ID raises ValidationFailed.
    - Closed line raises Conflict.
    """
    wo = wo_in_progress(inv_)
    tech = inv_["tech"]
    tech2 = inv_["tech2"]
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]

    inventory.receive(wh, part, "10.000", actor=stores.user, reference="PO-RET-04", reason="Stock in")
    line = inventory.request_part(wo, part, "2.000", actor=inv_["planner"].user, membership=inv_["planner"])
    inventory.issue(line, wh, "2.000", actor=stores.user)

    # 1. Exceeds outstanding
    with pytest.raises(Conflict) as exc:
        ws_services.return_job_part(
            wo,
            part_line_id=str(line.pk),
            quantity="3.000",
            membership=tech,
            actor=tech.user,
        )
    assert exc.value.code == "exceeds_outstanding"

    # 2. Non-assigned technician denied
    with pytest.raises(PermissionDenied):
        ws_services.return_job_part(
            wo,
            part_line_id=str(line.pk),
            quantity="1.000",
            membership=tech2,
            actor=tech2.user,
        )

    # 3. Invalid part line
    with pytest.raises(ValidationFailed) as exc:
        ws_services.return_job_part(
            wo,
            part_line_id="00000000-0000-0000-0000-000000000000",
            quantity="1.000",
            membership=tech,
            actor=tech.user,
        )
    assert exc.value.code == "part_line_not_found"


def test_job_part_return_html_view(inv_):
    """
    Test standard POST redirect and HTMX POST drawer swap for JobPartReturnView.
    """
    from django.test import Client

    wo = wo_in_progress(inv_)
    tech = inv_["tech"]
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]

    inventory.receive(wh, part, "10.000", actor=stores.user, reference="PO-RET-05", reason="Stock in")
    line = inventory.request_part(wo, part, "4.000", actor=inv_["planner"].user, membership=inv_["planner"])
    inventory.issue(line, wh, "4.000", actor=stores.user)

    client = Client()
    client.force_login(tech.user)

    # 1. Standard POST redirects to job_detail and returns stock
    resp = client.post(
        f"/app/workspace/jobs/{wo.pk}/parts/return/",
        {"part_line_id": str(line.pk), "quantity": "1.500", "reason": "Surplus seal"},
    )
    assert resp.status_code == 302
    assert resp.url == f"/app/workspace/jobs/{wo.pk}/"

    line.refresh_from_db()
    assert line.quantity_returned == Decimal("1.500")
    assert line.outstanding == Decimal("2.500")

    # 2. HTMX POST returns 200 with refreshed drawer HTML
    resp_htmx = client.post(
        f"/app/workspace/jobs/{wo.pk}/parts/return/",
        {"part_line_id": str(line.pk), "quantity": "1.000", "reason": "Second return"},
        HTTP_HX_REQUEST="true",
    )
    assert resp_htmx.status_code == 200
    content = resp_htmx.content.decode()
    assert "jobDrawer" in content
    assert "Allocated Parts" in content
    assert "Returned to stores" in content

    line.refresh_from_db()
    assert line.quantity_returned == Decimal("2.500")
    assert line.outstanding == Decimal("1.500")


def test_job_part_return_api_view(inv_):
    """
    Test REST API endpoint for returning unused parts:
    POST /api/v1/workspace/jobs/<uuid:pk>/parts/return/
    """
    from rest_framework.test import APIClient

    wo = wo_in_progress(inv_)
    tech = inv_["tech"]
    wh, part, stores = inv_["wh"], inv_["part"], inv_["stores"]

    inventory.receive(wh, part, "10.000", actor=stores.user, reference="PO-RET-06", reason="Stock in")
    line = inventory.request_part(wo, part, "3.000", actor=inv_["planner"].user, membership=inv_["planner"])
    inventory.issue(line, wh, "3.000", actor=stores.user)

    api = APIClient()
    api.force_authenticate(user=tech.user)

    # Return 1.250 units via REST API
    post_res = api.post(
        f"/api/v1/workspace/jobs/{wo.pk}/parts/return/",
        {
            "part_line_id": str(line.pk),
            "quantity": "1.250",
            "reason": "Not needed for repair",
        },
        format="json",
    )
    assert post_res.status_code == 200
    assert Decimal(str(post_res.data["quantity_returned"])) == Decimal("1.250")
    assert Decimal(str(post_res.data["outstanding"])) == Decimal("1.750")

    line.refresh_from_db()
    assert line.quantity_returned == Decimal("1.250")
    assert line.outstanding == Decimal("1.750")

    # Negative API case: attempt to return more than outstanding
    err_res = api.post(
        f"/api/v1/workspace/jobs/{wo.pk}/parts/return/",
        {
            "part_line_id": str(line.pk),
            "quantity": "5.000",
            "reason": "Too much",
        },
        format="json",
    )
    assert err_res.status_code == 400
    assert err_res.data["error"]["code"] == "exceeds_outstanding"
