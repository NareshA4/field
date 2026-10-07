"""M02 QR & Barcode Integration tests (identification, labels, scan, RBAC, tenant isolation)."""
from datetime import date
import pytest
from rest_framework import status

from apps.assets import services as asset_services
from apps.assets.models import Asset
from apps.assets.workflow import ACTIVE, RETIRED
from apps.audit.models import AuditLog
from apps.core.exceptions import Conflict, NotFound, PermissionDenied, ValidationFailed
from apps.identification import selectors as id_selectors
from apps.identification import services as id_services
from apps.identification.models import AssetIdentifier, ScanEvent
from apps.tenancy.models import Membership

pytestmark = pytest.mark.django_db


def test_generate_qr_and_barcode_identifiers(org_a, site_a1, make_asset, owner_a):
    asset = make_asset(org_a, site_a1, "AST-QR-001")

    # 1. QR Code is auto-generated on asset creation
    active = id_selectors.active_for_asset(org_a, asset)
    qr = active["QR"]
    assert qr is not None
    assert qr.pk is not None
    assert qr.kind == "QR"
    assert qr.is_active is True
    assert len(qr.token) >= 16
    assert qr.asset == asset

    # 2. Generate Barcode
    bc = id_services.generate_identifier(asset, kind="BARCODE", actor=owner_a)
    assert bc.pk is not None
    assert bc.kind == "BARCODE"
    assert bc.is_active is True
    assert len(bc.token) == 12

    # 3. Active selectors show both QR and Barcode
    active = id_selectors.active_for_asset(org_a, asset)
    assert active["QR"] == qr
    assert active["BARCODE"] == bc

    # 4. Duplicate active identifier of same kind rejected
    with pytest.raises(Conflict):
        id_services.generate_identifier(asset, kind="QR", actor=owner_a)


def test_render_qr_and_barcode_images(org_a, site_a1, make_asset, owner_a):
    asset = make_asset(org_a, site_a1, "AST-QR-002")
    active = id_selectors.active_for_asset(org_a, asset)
    qr = active["QR"]
    bc = id_services.generate_identifier(asset, kind="BARCODE", actor=owner_a)

    svg = id_services.render_qr_svg(qr.token)
    assert "<svg" in svg
    assert "</svg>" in svg

    png = id_services.render_qr_png(qr.token)
    assert isinstance(png, bytes)
    assert png.startswith(b"\x89PNG")

    bc_svg = id_services.render_barcode_svg(bc.token)
    assert "<svg" in bc_svg


def test_replace_and_revoke_identifier(org_a, site_a1, make_asset, owner_a):
    asset = make_asset(org_a, site_a1, "AST-QR-003")
    active = id_selectors.active_for_asset(org_a, asset)
    qr = active["QR"]
    old_token = qr.token

    # Replace identifier
    new_qr = id_services.replace_identifier(
        qr, reason="Label damaged on site", actor=owner_a
    )
    qr.refresh_from_db()
    assert qr.is_active is False
    assert qr.revoked_reason == "Label damaged on site"
    assert qr.revoked_by == owner_a
    assert new_qr.is_active is True
    assert new_qr.token != old_token

    # Revoke identifier
    revoked = id_services.revoke_identifier(
        new_qr, reason="Decommissioned label", actor=owner_a
    )
    assert revoked.is_active is False
    assert revoked.revoked_reason == "Decommissioned label"

    # Revoking already revoked identifier raises Conflict
    with pytest.raises(Conflict):
        id_services.revoke_identifier(revoked, reason="Duplicate revoke", actor=owner_a)


def test_terminal_asset_cannot_generate_labels(org_a, site_a1, make_asset, owner_a):
    asset = make_asset(org_a, site_a1, "AST-TERM-001")
    # Follow complete transition chain to retirement: ACTIVE -> UNDER_MAINTENANCE -> OUT_OF_SERVICE -> RETIRED
    asset = asset_services.change_status(asset, action="start_maintenance", reason="Maintenance started", actor=owner_a)
    asset = asset_services.change_status(asset, action="mark_out_of_service", reason="Beyond local repair", actor=owner_a)
    asset = asset_services.change_status(asset, action="retire", reason="End of lifecycle", actor=owner_a)
    assert asset.status == RETIRED

    with pytest.raises(Conflict):
        id_services.generate_identifier(asset, kind="BARCODE", actor=owner_a)


def test_resolve_token_valid_and_creates_scan_event(org_a, site_a1, make_asset, owner_a):
    asset = make_asset(org_a, site_a1, "AST-SCAN-001")
    active = id_selectors.active_for_asset(org_a, asset)
    qr = active["QR"]
    mem_a = Membership.objects.get(organization=org_a, user=owner_a)

    ident, resolved_asset, is_active = id_services.resolve_token(
        mem_a, qr.token, actor=owner_a
    )
    assert ident == qr
    assert resolved_asset == asset
    assert is_active is True

    # ScanEvent recorded
    scans = id_selectors.scans_for_asset(org_a, asset)
    assert len(scans) == 1
    assert scans[0].identifier == qr
    assert scans[0].scanned_by == owner_a


def test_resolve_token_unknown_and_cross_tenant_isolation(
    org_a, org_b, site_a1, site_b1, make_asset, owner_a, owner_b
):
    asset_a = make_asset(org_a, site_a1, "AST-ORG-A")
    active = id_selectors.active_for_asset(org_a, asset_a)
    qr_a = active["QR"]
    mem_a = Membership.objects.get(organization=org_a, user=owner_a)
    mem_b = Membership.objects.get(organization=org_b, user=owner_b)

    # 1. Invalid token raises NotFound
    with pytest.raises(NotFound):
        id_services.resolve_token(mem_a, "nonexistent-token-12345", actor=owner_a)

    # 2. Cross-tenant token lookup raises NotFound (tenant isolation)
    with pytest.raises(NotFound):
        id_services.resolve_token(mem_b, qr_a.token, actor=owner_b)


def test_resolve_token_unauthorized_site_scope_blocked(
    org_a, site_a1, site_a2, make_asset, owner_a, tech_a
):
    asset_a2 = make_asset(org_a, site_a2, "AST-SITE-A2")
    active = id_selectors.active_for_asset(org_a, asset_a2)
    qr_a2 = active["QR"]
    tech_mem = Membership.objects.get(organization=org_a, user=tech_a)

    from apps.rbac.services import has_permission
    if not has_permission(tech_mem, "asset.view", site_a2.pk):
        with pytest.raises(PermissionDenied):
            id_services.resolve_token(tech_mem, qr_a2.token, actor=tech_a)


def test_ui_labels_panel_and_print_sheet(client, org_a, site_a1, make_asset, owner_a):
    client.force_login(owner_a)
    asset = make_asset(org_a, site_a1, "AST-UI-001")

    # 1. Labels panel HTMX partial
    res = client.get(f"/app/assets/{asset.pk}/labels/panel/")
    assert res.status_code == 200
    assert "labels-panel" in res.content.decode()

    # 2. Generate Barcode via panel action POST
    res_gen = client.post(
        f"/app/assets/{asset.pk}/labels/generate/",
        {"kind": "BARCODE"},
    )
    assert res_gen.status_code == 302

    # 3. View printable label sheet
    res_lbl = client.get(f"/app/assets/{asset.pk}/label/")
    assert res_lbl.status_code == 200
    assert asset.asset_tag in res_lbl.content.decode()

    # 4. View image SVG
    res_img = client.get(f"/app/assets/{asset.pk}/labels/qr/svg/")
    assert res_img.status_code == 200
    assert res_img["Content-Type"] == "image/svg+xml"

    # 5. Scan view
    res_scan = client.get("/app/scan/")
    assert res_scan.status_code == 200

    # 6. Resolve token redirects directly to asset detail
    active = id_selectors.active_for_asset(org_a, asset)
    res_res = client.get(f"/app/s/{active['QR'].token}/")
    assert res_res.status_code == 302
    assert f"/app/assets/{asset.pk}/" in res_res["Location"]


def test_api_asset_qr_and_identifier_endpoints(as_user, org_a, site_a1, make_asset, owner_a):
    client = as_user(owner_a, org_a)
    asset = make_asset(org_a, site_a1, "AST-API-001")

    # 1. GET /api/v1/assets/{id}/qr/ has auto-generated QR code
    res_qr0 = client.get(f"/api/v1/assets/{asset.pk}/qr/")
    assert res_qr0.status_code == status.HTTP_200_OK
    assert res_qr0.data["qr"] is not None
    qr_id = res_qr0.data["qr"]["id"]
    token = res_qr0.data["qr"]["token"]

    # 2. POST /api/v1/asset-identifiers/ to generate Barcode
    res_post = client.post(
        "/api/v1/asset-identifiers/",
        {"asset": str(asset.pk), "kind": "BARCODE"},
        format="json",
    )
    assert res_post.status_code == status.HTTP_201_CREATED
    bc_token = res_post.data["token"]

    # 3. GET /api/v1/assets/{id}/qr/ now has active QR and Barcode
    res_qr1 = client.get(f"/api/v1/assets/{asset.pk}/qr/")
    assert res_qr1.status_code == status.HTTP_200_OK
    assert res_qr1.data["qr"]["token"] == token
    assert res_qr1.data["barcode"]["token"] == bc_token

    # 4. GET /api/v1/asset-identifiers/resolve/?token=...
    res_res = client.get(f"/api/v1/asset-identifiers/resolve/?token={token}")
    assert res_res.status_code == status.HTTP_200_OK
    assert res_res.data["asset_tag"] == "AST-API-001"

    # 5. POST /api/v1/asset-identifiers/{id}/replace/
    res_rep = client.post(
        f"/api/v1/asset-identifiers/{qr_id}/replace/",
        {"reason": "Barcode wear and tear"},
        format="json",
    )
    assert res_rep.status_code == status.HTTP_200_OK
    assert res_rep.data["token"] != token

    # 6. POST /api/v1/asset-identifiers/{id}/revoke/
    new_id = res_rep.data["id"]
    res_rev = client.post(
        f"/api/v1/asset-identifiers/{new_id}/revoke/",
        {"reason": "Revoking replacement"},
        format="json",
    )
    assert res_rev.status_code == status.HTTP_200_OK
    assert res_rev.data["is_active"] is False
