import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
django.setup()

from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta, date

from apps.tenancy.models import Organization, Membership
from apps.sites.models import Site, Zone
from apps.sites import services as site_services
from apps.assets.models import AssetCategory, Asset, AssetMeter, AssetMeterReading
from apps.assets import services as asset_services
from apps.identification.models import AssetIdentifier
from apps.identification import services as id_services
from apps.core.tenant import tenant_context

User = get_user_model()
org = Organization.objects.first()
if not org:
    print("No organization found!")
    exit(1)

admin_user = User.objects.filter(is_platform_admin=True).first() or User.objects.first()
membership = Membership.objects.filter(organization=org).first()

with tenant_context(org):
    # 1. Create or get Site
    site = Site.objects.filter(organization=org, code="BLR-DC-01").first()
    if not site:
        site = site_services.create_site(
            org,
            code="BLR-DC-01",
            name="Bengaluru Main Data Center",
            description="Tier-4 Enterprise Data Center & Server Facilities",
            address="Plot 42, Electronic City Phase 1",
            city="Bengaluru",
            state_region="Karnataka",
            postal_code="560100",
            country="IN",
            timezone="Asia/Kolkata",
            contact_name="Rajesh Kumar",
            contact_email="facilities.blr@example.com",
            contact_phone="+91 98765 43210",
            actor=admin_user,
        )
        print(f"Created Site: {site.code} - {site.name}")
    else:
        print(f"Site exists: {site.code} - {site.name}")

    # 2. Create or get Zone
    zone = Zone.objects.filter(site=site, code="HALL-A").first()
    if not zone:
        zone = site_services.create_zone(
            site,
            name="Server Hall A - Ground Floor",
            code="HALL-A",
            zone_type="ZONE",
            description="Primary high-density server rack hall",
            actor=admin_user,
        )
        print(f"Created Zone: {zone.name}")
    else:
        print(f"Zone exists: {zone.name}")

    # 3. Create or get Asset Category
    category = AssetCategory.objects.filter(organization=org, name="Precision HVAC & Chillers").first()
    if not category:
        category = asset_services.create_category(
            org,
            name="Precision HVAC & Chillers",
            description="Data center climate control and precision cooling chillers",
            attribute_definitions=[
                {"name": "refrigerant", "label": "Refrigerant Type", "type": "TEXT", "required": False},
                {"name": "cooling_capacity", "label": "Cooling Capacity", "type": "TEXT", "required": False},
                {"name": "power_rating", "label": "Power Rating", "type": "TEXT", "required": False},
            ],
            actor=admin_user,
        )
        print(f"Created Category: {category.name}")
    else:
        print(f"Category exists: {category.name}")

    # 4. Create or get Asset
    asset_tag = "CHILLER-DEMO-01"
    asset = Asset.objects.filter(organization=org, asset_tag=asset_tag).first()
    if not asset:
        asset = asset_services.create_asset(
            org,
            site=site,
            category=category,
            zone=zone,
            asset_tag=asset_tag,
            name="Liebert High-Density Precision Chiller Unit",
            manufacturer="Vertiv Liebert",
            model="PCW-500-HD",
            serial_number="SN-VERTIV-2026-9871A",
            description="Primary dual-circuit precision cooling unit servicing Server Hall A rows 1-8.",
            purchase_date=date(2026, 1, 15),
            commission_date=date(2026, 2, 1),
            warranty_ref="VERTIV-CARE-GOLD-2029",
            attributes={
                "refrigerant_type": "R-410A EcoSafe",
                "cooling_capacity": "50 TR (175 kW)",
                "power_rating": "415V 3-Phase 50Hz",
            },
            actor=admin_user,
        )
        print(f"Created Asset: {asset.asset_tag} - {asset.name}")

        # Transition status: COMMISSIONED -> OPERATIONAL
        asset_services.change_status(
            asset,
            action="OPERATE",
            reason="Commissioning sign-off completed by certified engineer.",
            actor=admin_user,
        )
        print(f"Asset status updated to: {asset.status}")

        # Ensure active QR and Barcode identifiers exist
        qr_ident = AssetIdentifier.objects.filter(asset=asset, kind="QR", is_active=True).first()
        if not qr_ident:
            qr_ident = id_services.generate_identifier(asset, kind="QR", actor=admin_user)
        bc_ident = AssetIdentifier.objects.filter(asset=asset, kind="BARCODE", is_active=True).first()
        if not bc_ident:
            bc_ident = id_services.generate_identifier(asset, kind="BARCODE", actor=admin_user)
        print(f"Generated QR Code: {qr_ident.token}")
        print(f"Generated Barcode: {bc_ident.token}")

        # Create Meters
        meter_hours = AssetMeter.objects.create(
            organization=org,
            asset=asset,
            name="Run Hours",
            unit="hours",
            last_value=1420.5,
            last_read_at=timezone.now(),
        )
        AssetMeterReading.objects.create(
            organization=org,
            meter=meter_hours,
            value=1420.5,
            read_at=timezone.now(),
            notes="Routine 1000-hour inspection reading.",
            recorded_by=admin_user,
        )

        meter_kwh = AssetMeter.objects.create(
            organization=org,
            asset=asset,
            name="Power Consumption",
            unit="kWh",
            last_value=38940.0,
            last_read_at=timezone.now(),
        )
        AssetMeterReading.objects.create(
            organization=org,
            meter=meter_kwh,
            value=38940.0,
            read_at=timezone.now(),
            notes="Monthly energy meter log.",
            recorded_by=admin_user,
        )
        print("Created Meters and initial readings.")
    else:
        print(f"Asset already exists: {asset.asset_tag}")

    # Fetch active QR token
    active_qr = AssetIdentifier.objects.filter(asset=asset, kind="QR", is_active=True).first()
    qr_token = active_qr.token if active_qr else "N/A"

    print("\n" + "="*70)
    print("DEMO ASSET SETUP COMPLETE")
    print("="*70)
    print(f"Asset Tag:        {asset.asset_tag}")
    print(f"Asset Name:       {asset.name}")
    print(f"Site:             {site.code} ({site.name})")
    print(f"Location / Zone:  {zone.name}")
    print(f"Status:           {asset.status}")
    print(f"QR Token:         {qr_token}")
    print(f"Asset URL:        http://localhost:8080/app/assets/{asset.pk}/")
    print(f"Labels URL:       http://localhost:8080/app/assets/{asset.pk}/?tab=labels")
    print(f"Printable Sheet:  http://localhost:8080/app/assets/{asset.pk}/label/")
    print(f"QR Scan Link:     http://localhost:8080/app/s/{qr_token}/")
    print("="*70)
