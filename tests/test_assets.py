"""M02 Asset Registry: models, services, lifecycle transitions, RBAC, tenant isolation, APIs, audit."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework import status

from apps.assets import selectors, services
from apps.assets.models import (
    Asset,
    AssetStatusHistory,
)
from apps.assets.workflow import (
    ACTIVE,
    DISPOSED,
    OUT_OF_SERVICE,
    RETIRED,
    UNDER_MAINTENANCE,
)
from apps.audit.models import AuditLog
from apps.core.exceptions import Conflict, InvalidTransition, NotFound, ValidationFailed

pytestmark = pytest.mark.django_db


def audit_actions(org):
    return list(AuditLog.objects.filter(organization=org).values_list("action", flat=True))


# ===================================================================================================
# 1. MODELS & CONSTRAINTS
# ===================================================================================================


def test_asset_creation_and_fields(org_a, site_a1, make_category, owner_a):
    cat = make_category(org_a, "HVAC")
    asset = services.create_asset(
        org_a,
        site=site_a1,
        category=cat,
        actor=owner_a,
        asset_tag="AC-001",
        name="Main Chiller",
        manufacturer="Carrier",
        model="AquaSnap 30MP",
        serial_number="SN-CAR-9988",
        purchase_date=date(2025, 1, 10),
        commission_date=date(2025, 1, 15),
        warranty_ref="WR-2025-CAR",
        description="Roof chiller unit",
    )
    assert asset.pk is not None
    assert asset.organization_id == org_a.pk
    assert asset.site_id == site_a1.pk
    assert asset.category_id == cat.pk
    assert asset.status == ACTIVE
    assert asset.asset_tag == "AC-001"
    assert asset.serial_number == "SN-CAR-9988"
    assert str(asset) == "AC-001 · Main Chiller"


def test_asset_tag_unique_per_organization_case_insensitive(org_a, org_b, site_a1, make_site, make_asset):
    make_asset(org_a, site_a1, "PUMP-101")
    with pytest.raises(Conflict) as exc:
        make_asset(org_a, site_a1, "pump-101")
    assert exc.value.code == "duplicate_asset_tag"

    # Same tag allowed in another tenant
    site_b = make_site(org_b, "SITE-B")
    asset_b = make_asset(org_b, site_b, "PUMP-101")
    assert asset_b.organization_id == org_b.pk


def test_asset_serial_unique_per_manufacturer_and_org(org_a, org_b, site_a1, make_site, make_category, owner_a):
    cat = make_category(org_a, "Electrical")
    services.create_asset(
        org_a,
        site=site_a1,
        category=cat,
        actor=owner_a,
        asset_tag="GEN-1",
        name="Generator 1",
        manufacturer="Cummins",
        serial_number="SN-7788",
    )
    # Duplicate manufacturer + serial in same org fails
    with pytest.raises(Conflict) as exc:
        services.create_asset(
            org_a,
            site=site_a1,
            category=cat,
            actor=owner_a,
            asset_tag="GEN-2",
            name="Generator 2",
            manufacturer="cummins",
            serial_number="sn-7788",
        )
    assert exc.value.code == "duplicate_serial_number"

    # Same serial under different organization is permitted
    cat_b = make_category(org_b, "Electrical")
    site_b = make_site(org_b, "SITE-B")
    asset_b = services.create_asset(
        org_b,
        site=site_b,
        category=cat_b,
        actor=None,
        asset_tag="GEN-1",
        name="Generator 1",
        manufacturer="Cummins",
        serial_number="SN-7788",
    )
    assert asset_b.organization_id == org_b.pk


def test_asset_commission_date_before_purchase_date_rejected(org_a, site_a1, make_category, owner_a):
    cat = make_category(org_a, "Pumps")
    with pytest.raises(ValidationFailed) as exc:
        services.create_asset(
            org_a,
            site=site_a1,
            category=cat,
            actor=owner_a,
            asset_tag="P-01",
            name="Water Pump",
            purchase_date=date(2025, 5, 20),
            commission_date=date(2025, 5, 10),
        )
    assert exc.value.code == "invalid_dates"


def test_asset_status_history_is_append_only(org_a, site_a1, make_asset):
    asset = make_asset(org_a, site_a1, "HIST-1")
    history = asset.status_history.first()
    assert history is not None

    with pytest.raises(ValueError, match="AssetStatusHistory is append-only"):
        history.reason = "Tampered reason"
        history.save()

    with pytest.raises(ValueError, match="AssetStatusHistory is append-only"):
        history.delete()


def test_asset_location_history_is_append_only(org_a, site_a1, make_site, make_asset, owner_a):
    site_a2 = make_site(org_a, "A2")
    asset = make_asset(org_a, site_a1, "MOVE-1")
    services.update_asset(asset, site=site_a2, reason="Relocated to plant 2", actor=owner_a)

    loc_hist = asset.location_history.first()
    assert loc_hist is not None
    assert loc_hist.from_site_id == site_a1.pk
    assert loc_hist.to_site_id == site_a2.pk

    with pytest.raises(ValueError, match="AssetLocationHistory is append-only"):
        loc_hist.reason = "Modified"
        loc_hist.save()

    with pytest.raises(ValueError, match="AssetLocationHistory is append-only"):
        loc_hist.delete()


# ===================================================================================================
# 2. SERVICES & CROSS-ENTITY VALIDATIONS
# ===================================================================================================


def test_create_asset_cross_tenant_site_rejected(org_a, org_b, make_site, make_category, owner_a):
    site_b = make_site(org_b, "B-SITE")
    cat_a = make_category(org_a, "Pumps")
    with pytest.raises(ValidationFailed) as exc:
        services.create_asset(org_a, site=site_b, category=cat_a, actor=owner_a, asset_tag="TAG-CROSS", name="Pump")
    assert exc.value.code == "cross_tenant_site"


def test_create_asset_inactive_site_rejected(org_a, site_a1, make_category, owner_a):
    from apps.sites import services as site_services

    site_services.deactivate_site(site_a1, reason="Renovation", actor=owner_a)
    cat = make_category(org_a, "Pumps")
    with pytest.raises(Conflict) as exc:
        services.create_asset(org_a, site=site_a1, category=cat, actor=owner_a, asset_tag="TAG-INACT", name="Pump")
    assert exc.value.code == "site_inactive"


def test_create_asset_zone_from_another_site_rejected(org_a, site_a1, make_site, make_zone, make_category, owner_a):
    site_a2 = make_site(org_a, "A2")
    zone_a2 = make_zone(site_a2, "Basement A2")
    cat = make_category(org_a, "Pumps")
    with pytest.raises(ValidationFailed) as exc:
        services.create_asset(org_a, site=site_a1, zone=zone_a2, category=cat, actor=owner_a, asset_tag="TAG-ZONE", name="Pump")
    assert exc.value.code == "zone_site_mismatch"


def test_create_asset_inactive_category_rejected(org_a, site_a1, make_category, owner_a):
    cat = make_category(org_a, "Deprecated Tech")
    services.update_category(cat, is_active=False, actor=owner_a)
    with pytest.raises(Conflict) as exc:
        services.create_asset(org_a, site=site_a1, category=cat, actor=owner_a, asset_tag="TAG-CAT", name="Pump")
    assert exc.value.code == "category_inactive"


def test_create_asset_emits_audit_and_initial_history(org_a, site_a1, make_category, owner_a):
    cat = make_category(org_a, "Meters")
    asset = services.create_asset(org_a, site=site_a1, category=cat, asset_tag="AUD-01", name="Audited Asset", actor=owner_a)
    assert "asset.created" in audit_actions(org_a)
    assert asset.status_history.count() == 1
    assert asset.status_history.first().to_status == ACTIVE


# ===================================================================================================
# 3. ASSET UPDATES & LIFECYCLE STATE MACHINE
# ===================================================================================================


def test_update_asset_fields_and_audit(org_a, site_a1, make_asset, owner_a):
    asset = make_asset(org_a, site_a1, "UPD-1", name="Original Name")
    updated = services.update_asset(asset, name="Renamed Asset", description="Updated description", actor=owner_a)
    assert updated.name == "Renamed Asset"
    assert updated.description == "Updated description"
    assert "asset.updated" in audit_actions(org_a)


def test_update_asset_cannot_modify_status_directly(org_a, site_a1, make_asset, owner_a):
    asset = make_asset(org_a, site_a1, "STATUS-PROT")
    # update_asset does not accept status parameter
    services.update_asset(asset, name="New Name", actor=owner_a)
    asset.refresh_from_db()
    assert asset.status == ACTIVE


def test_lifecycle_transitions_and_history(org_a, site_a1, make_asset, owner_a):
    asset = make_asset(org_a, site_a1, "LIFE-1")
    assert asset.status == ACTIVE

    # ACTIVE -> UNDER_MAINTENANCE
    services.change_status(asset, action="start_maintenance", reason="Scheduled overhaul", actor=owner_a)
    asset.refresh_from_db()
    assert asset.status == UNDER_MAINTENANCE
    assert "asset.status_changed" in audit_actions(org_a)

    # UNDER_MAINTENANCE -> OUT_OF_SERVICE
    services.change_status(asset, action="mark_out_of_service", reason="Critical part broken", actor=owner_a)
    asset.refresh_from_db()
    assert asset.status == OUT_OF_SERVICE

    # OUT_OF_SERVICE -> ACTIVE
    services.change_status(asset, action="return_to_service", reason="Repaired and tested", actor=owner_a)
    asset.refresh_from_db()
    assert asset.status == ACTIVE

    # ACTIVE -> UNDER_MAINTENANCE -> OUT_OF_SERVICE -> RETIRED
    services.change_status(asset, action="start_maintenance", reason="Pre-retirement check", actor=owner_a)
    services.change_status(asset, action="mark_out_of_service", reason="Beyond repair", actor=owner_a)
    services.change_status(asset, action="retire", reason="End of economic lifecycle", actor=owner_a)
    asset.refresh_from_db()
    assert asset.status == RETIRED

    # Terminal state prevents any further transitions
    with pytest.raises(InvalidTransition):
        services.change_status(asset, action="return_to_service", reason="Attempted return", actor=owner_a)

    # Terminal state prevents normal updates
    with pytest.raises(Conflict) as exc:
        services.update_asset(asset, name="Attempted edit", actor=owner_a)
    assert exc.value.code == "asset_terminal"


def test_out_of_service_to_disposed_transition(org_a, site_a1, make_asset, owner_a):
    asset = make_asset(org_a, site_a1, "DISP-1")
    services.change_status(asset, action="start_maintenance", reason="Initial inspect", actor=owner_a)
    services.change_status(asset, action="mark_out_of_service", reason="Damaged in fire", actor=owner_a)
    services.change_status(asset, action="dispose", reason="Scrapped for recycling", actor=owner_a)
    asset.refresh_from_db()
    assert asset.status == DISPOSED


def test_invalid_lifecycle_transition_rejected(org_a, site_a1, make_asset, owner_a):
    asset = make_asset(org_a, site_a1, "INVALID-TR")
    # Cannot dispose an ACTIVE asset directly (must be OUT_OF_SERVICE first)
    with pytest.raises(InvalidTransition):
        services.change_status(asset, action="dispose", reason="Direct dispose attempt", actor=owner_a)


# ===================================================================================================
# 4. CATEGORIES & CUSTOM ATTRIBUTES
# ===================================================================================================


def test_category_custom_attributes_validation(org_a, site_a1, owner_a):
    schema = [
        {"label": "Voltage", "type": "number", "required": True},
        {"label": "Cooling Type", "type": "choice", "required": False, "choices": ["Air", "Water", "Oil"]},
    ]
    cat = services.create_category(
        org_a,
        name="Transformers",
        attribute_definitions=schema,
        actor=owner_a,
    )
    assert len(cat.attribute_definitions) == 2
    assert cat.attribute_definitions[0]["label"] == "Voltage"
    assert cat.attribute_definitions[0]["key"] == "voltage"

    # Missing required attribute fails
    with pytest.raises(ValidationFailed) as exc:
        services.create_asset(org_a, site=site_a1, category=cat, actor=owner_a, asset_tag="TR-1", name="T1", attributes={})
    assert "Voltage" in str(exc.value)

    # Invalid choice value fails
    with pytest.raises(ValidationFailed) as exc:
        services.create_asset(
            org_a,
            site=site_a1,
            category=cat,
            actor=owner_a,
            asset_tag="TR-1",
            name="T1",
            attributes={"voltage": "440", "cooling_type": "Liquid Nitrogen"},
        )
    assert "Cooling Type" in str(exc.value)

    # Valid custom attributes succeed
    asset = services.create_asset(
        org_a,
        site=site_a1,
        category=cat,
        actor=owner_a,
        asset_tag="TR-1",
        name="T1",
        attributes={"voltage": "440", "cooling_type": "Oil"},
    )
    assert asset.attributes["voltage"] == "440"
    assert asset.attributes["cooling_type"] == "Oil"


# ===================================================================================================
# 5. METERS & READINGS
# ===================================================================================================


def test_asset_meters_and_monotonic_readings(org_a, site_a1, make_asset, owner_a):
    asset = make_asset(org_a, site_a1, "METERED-1")
    meter = services.create_meter(asset, name="Runtime Hours", unit="hrs", actor=owner_a)
    assert meter.asset_id == asset.pk
    assert meter.is_active is True
    assert "asset.meter_created" in audit_actions(org_a)

    # First reading
    t1 = timezone.now() - timedelta(hours=5)
    r1 = services.record_reading(meter, value=Decimal("100.5"), read_at=t1, actor=owner_a)
    assert r1.value == Decimal("100.5")

    # Second higher reading succeeds
    t2 = timezone.now() - timedelta(hours=2)
    r2 = services.record_reading(meter, value=Decimal("105.0"), read_at=t2, actor=owner_a)
    assert r2.value == Decimal("105.0")

    # Lower reading (backward counter) is rejected (monotonic rule)
    t3 = timezone.now()
    with pytest.raises(ValidationFailed) as exc:
        services.record_reading(meter, value=Decimal("95.0"), read_at=t3, actor=owner_a)
    assert exc.value.code == "meter_not_monotonic"

    # Inactive meter cannot accept readings
    services.set_meter_active(meter, active=False, actor=owner_a)
    with pytest.raises(Conflict) as exc:
        services.record_reading(meter, value=Decimal("110.0"), read_at=timezone.now(), actor=owner_a)
    assert exc.value.code == "meter_inactive"


# ===================================================================================================
# 6. RBAC & SITE-SCOPED PERMISSIONS
# ===================================================================================================


def test_rbac_asset_site_scoped_permissions(org_a, make_site, make_asset, make_scoped_member):
    site_1 = make_site(org_a, "SITE-1")
    site_2 = make_site(org_a, "SITE-2")

    asset_1 = make_asset(org_a, site_1, "AST-SITE1")
    asset_2 = make_asset(org_a, site_2, "AST-SITE2")

    # Member granted supervisor (read) on site 1 only
    member_s1 = make_scoped_member(org_a, "sup1@demo.local", "supervisor", [site_1])

    # Visible assets for member_s1 only includes site 1
    visible = list(selectors.assets_for(member_s1, org_a))
    assert asset_1 in visible
    assert asset_2 not in visible

    # get_asset on site 2 raises NotFound (scoped 404 security rule)
    with pytest.raises(NotFound):
        selectors.get_asset(member_s1, org_a, asset_2.pk)


# ===================================================================================================
# 7. TENANT ISOLATION
# ===================================================================================================


def test_tenant_isolation_asset_access(org_a, org_b, site_a1, make_site, make_asset, make_member):
    site_b = make_site(org_b, "SITE-B")
    asset_a = make_asset(org_a, site_a1, "ORG-A-ASSET")
    asset_b = make_asset(org_b, site_b, "ORG-B-ASSET")

    member_a = make_member(org_a, "user_a@demo.local", "admin")
    member_b = make_member(org_b, "user_b@demo.local", "admin")

    # Member A cannot see Org B asset
    assert asset_b not in selectors.assets_for(member_a, org_a)
    with pytest.raises(NotFound):
        selectors.get_asset(member_a, org_a, asset_b.pk)

    # Member B cannot see Org A asset
    assert asset_a not in selectors.assets_for(member_b, org_b)
    with pytest.raises(NotFound):
        selectors.get_asset(member_b, org_b, asset_a.pk)


# ===================================================================================================
# 8. REST API INTEGRATION
# ===================================================================================================


def test_api_asset_list_and_filters(as_user, org_a, site_a1, make_site, make_asset, make_category, owner_a):
    client = as_user(owner_a, org_a)
    cat = make_category(org_a, "Pumps")
    site_a2 = make_site(org_a, "A2")

    make_asset(org_a, site_a1, "API-PUMP-1", category=cat)
    make_asset(org_a, site_a2, "API-FAN-2")

    # List all
    res = client.get("/api/v1/assets/")
    assert res.status_code == status.HTTP_200_OK
    results = res.data["results"] if "results" in res.data else res.data
    tags = [r["asset_tag"] for r in results]
    assert "API-PUMP-1" in tags
    assert "API-FAN-2" in tags

    # Filter by site
    res_site = client.get(f"/api/v1/assets/?site={site_a1.pk}")
    results_site = res_site.data["results"] if "results" in res_site.data else res_site.data
    assert len(results_site) == 1
    assert results_site[0]["asset_tag"] == "API-PUMP-1"


def test_api_asset_create(as_user, org_a, site_a1, make_category, owner_a):
    client = as_user(owner_a, org_a)
    cat = make_category(org_a, "Motors")

    payload = {
        "asset_tag": "API-MOT-1",
        "name": "Electric Motor 50kW",
        "category": str(cat.pk),
        "site": str(site_a1.pk),
        "manufacturer": "Siemens",
        "model": "1LE1",
        "serial_number": "SN-SIE-100",
    }
    res = client.post("/api/v1/assets/", payload, format="json")
    assert res.status_code == status.HTTP_201_CREATED
    assert res.data["asset_tag"] == "API-MOT-1"
    assert res.data["status"] == ACTIVE


def test_api_asset_patch_cannot_bypass_status(as_user, org_a, site_a1, make_asset, owner_a):
    client = as_user(owner_a, org_a)
    asset = make_asset(org_a, site_a1, "API-PATCH-1")

    # Attempting to supply status in PATCH is blocked with 400 validation error
    res_bad = client.patch(f"/api/v1/assets/{asset.pk}/", {"status": "DISPOSED"}, format="json")
    assert res_bad.status_code == status.HTTP_400_BAD_REQUEST
    assert "status" in str(res_bad.data)

    # Normal PATCH without status updates editable fields
    res = client.patch(f"/api/v1/assets/{asset.pk}/", {"name": "New Name"}, format="json")
    assert res.status_code == status.HTTP_200_OK
    asset.refresh_from_db()
    assert asset.name == "New Name"
    assert asset.status == ACTIVE


def test_api_asset_transition_endpoint(as_user, org_a, site_a1, make_asset, owner_a):
    client = as_user(owner_a, org_a)
    asset = make_asset(org_a, site_a1, "API-TRANS-1")

    # Valid transition
    res = client.post(
        f"/api/v1/assets/{asset.pk}/transition/",
        {"action": "start_maintenance", "reason": "Routine calibration"},
        format="json",
    )
    assert res.status_code == status.HTTP_200_OK
    assert res.data["status"] == UNDER_MAINTENANCE

    # History endpoint returns the transition record
    res_hist = client.get(f"/api/v1/assets/{asset.pk}/history/")
    assert res_hist.status_code == status.HTTP_200_OK
    hist_results = res_hist.data["results"] if "results" in res_hist.data else res_hist.data
    assert len(hist_results) >= 2  # initial + maintenance


def test_api_meter_creation_and_reading(as_user, org_a, site_a1, make_asset, owner_a):
    client = as_user(owner_a, org_a)
    asset = make_asset(org_a, site_a1, "API-MTR-1")

    # Create meter
    res_m = client.post(
        "/api/v1/asset-meters/",
        {"asset": str(asset.pk), "name": "Power Consumption", "unit": "kWh"},
        format="json",
    )
    assert res_m.status_code == status.HTTP_201_CREATED
    meter_id = res_m.data["id"]

    # Post reading
    res_r = client.post(
        f"/api/v1/asset-meters/{meter_id}/readings/",
        {"value": "5000.25", "notes": "End of month meter reading"},
        format="json",
    )
    assert res_r.status_code == status.HTTP_201_CREATED
    assert Decimal(str(res_r.data["value"])) == Decimal("5000.25")


# ===================================================================================================
# 9. TRANSACTION ATOMICITY & ROLLBACK
# ===================================================================================================


def test_transaction_rollback_on_failed_asset_creation(org_a, site_a1, make_category, owner_a):
    cat = make_category(org_a, "Electrical")
    initial_count = Asset.objects.filter(organization=org_a).count()
    initial_history = AssetStatusHistory.objects.filter(organization=org_a).count()

    # Attempt to create asset with commission date earlier than purchase date
    with pytest.raises(ValidationFailed):
        services.create_asset(
            org_a,
            site=site_a1,
            category=cat,
            actor=owner_a,
            asset_tag="FAIL-1",
            name="Fail Asset",
            purchase_date=date(2025, 6, 1),
            commission_date=date(2025, 5, 1),
        )

    # Asset count and status history count remain unchanged (clean rollback)
    assert Asset.objects.filter(organization=org_a).count() == initial_count
    assert AssetStatusHistory.objects.filter(organization=org_a).count() == initial_history

