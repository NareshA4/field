"""
Seed script for FieldOps Nexus - Full Master ERP Domain with Role-Based Accounts and Asset Hierarchy.
"""
import os
import sys
from decimal import Decimal
from datetime import date, timedelta
from pathlib import Path

# Setup Django environment
BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
import django
django.setup()

from apps.tenancy.models import Organization, Membership
from apps.accounts.models import User
from apps.rbac import services as rbac
from apps.sites.models import Site, Zone
from apps.assets import hierarchy, services as asset_services
from apps.assets.models import AssetCategory, Asset, AssetStatus


def seed():
    print("[*] Seeding FieldOps Nexus System Data & Demo Environment...")

    # 1. Organization
    org, _ = Organization.objects.get_or_create(
        code="APEX-IND",
        defaults={
            "name": "Apex Industrial Technologies Ltd",
            "slug": "apex-industrial",
            "contact_email": "operations@apexindustrial.com",
            "contact_phone": "+94 11 289 9000",
        },
    )
    print(f"[OK] Organization: {org.name} ({org.code})")

    # 2. System Roles
    roles = rbac.seed_system_roles(org)
    print(f"[OK] Seeded {len(roles)} system roles.")

    # 3. Users and Memberships
    users_data = [
        ("admin@apexindustrial.com", "Admin User", "owner", "General Manager"),
        ("asset.manager@apexindustrial.com", "Saman Perera", "asset_manager", "Senior Asset Manager"),
        ("planner@apexindustrial.com", "Nimal Fernando", "maintenance_planner", "Maintenance Planner"),
        ("supervisor@apexindustrial.com", "Kamal Silva", "supervisor", "Plant Operations Supervisor"),
        ("lead.technician@apexindustrial.com", "Kasun Perera", "technician", "Lead Field Technician"),
    ]

    for email, full_name, role_key, job_title in users_data:
        user = User.objects.filter(email=email).first()
        if not user:
            user = User.objects.create_user(
                email=email,
                password="Password123!",
                full_name=full_name,
            )
        else:
            user.set_password("Password123!")
            user.full_name = full_name
            user.save()

        membership = Membership.objects.unscoped().filter(organization=org, user=user).first()
        if not membership:
            membership = Membership.objects.create(
                organization=org,
                user=user,
                job_title=job_title,
                status=Membership.Status.ACTIVE,
            )
        else:
            membership.job_title = job_title
            membership.status = Membership.Status.ACTIVE
            membership.save()

        # Assign Role
        target_role = roles.get(role_key)
        if target_role:
            rbac.set_membership_roles(membership, [target_role], actor=user, system=True)
        print(f"[OK] User created: {email} (Password: Password123!) | Role: {role_key}")

    # 4. Sites & Zones
    site_cmb, _ = Site.objects.get_or_create(
        organization=org,
        code="SITE-CMB-HQ",
        defaults={
            "name": "Colombo Central Processing Plant",
            "address": "45 Industrial Parkway, Peliyagoda",
            "city": "Colombo",
            "country": "LK",
            "timezone": "Asia/Colombo",
            "contact_name": "Saman Perera",
            "contact_email": "asset.manager@apexindustrial.com",
            "contact_phone": "+94 11 289 9001",
        },
    )

    zone_hvac, _ = Zone.objects.get_or_create(
        organization=org,
        site=site_cmb,
        code="ZN-HVAC-01",
        defaults={
            "name": "Central Utility Plant & Chillers",
            "floor_or_level": "Roof Deck / Level 4",
            "description": "Industrial chilling plants and AHU distribution",
        },
    )

    zone_elec, _ = Zone.objects.get_or_create(
        organization=org,
        site=site_cmb,
        code="ZN-ELEC-01",
        defaults={
            "name": "Main Electrical Switchgear Room",
            "floor_or_level": "Ground Floor",
            "description": "33kV / 415V Substation & Power Distribution",
        },
    )
    print(f"[OK] Sites & Zones created for {site_cmb.name}")

    # 5. Asset Categories
    cat_hvac, _ = AssetCategory.objects.get_or_create(
        organization=org,
        name="HVAC & Chillers",
        defaults={
            "description": "Large scale industrial chillers and cooling towers",
            "attribute_definitions": [
                {"key": "cooling_tons", "label": "Cooling Capacity (Tons)", "type": "number", "required": True},
                {"key": "refrigerant", "label": "Refrigerant Type", "type": "choice", "required": True, "choices": ["R-134a", "R-410A", "R-1233zd"]},
                {"key": "voltage", "label": "Operating Voltage", "type": "text", "required": False},
            ],
        },
    )

    cat_pumps, _ = AssetCategory.objects.get_or_create(
        organization=org,
        name="Pumps & Compressors",
        defaults={
            "description": "Centrifugal, screw compressors and booster pumps",
            "attribute_definitions": [
                {"key": "flow_rate_gpm", "label": "Flow Rate (GPM)", "type": "number", "required": False},
                {"key": "rpm", "label": "Operating RPM", "type": "number", "required": False},
            ],
        },
    )

    cat_elec, _ = AssetCategory.objects.get_or_create(
        organization=org,
        name="Electrical & Power",
        defaults={
            "description": "Transformers, VFD drives and circuit breakers",
        },
    )

    admin_user = User.objects.get(email="admin@apexindustrial.com")

    # 6. Multi-Tier Asset Hierarchy
    # Root Chiller
    root_chiller = Asset.objects.filter(organization=org, asset_tag="AST-CHL-001").first()
    if not root_chiller:
        root_chiller = asset_services.create_asset(
            org,
            asset_tag="AST-CHL-001",
            name="Trane Centrifugal Chiller Unit #1",
            category=cat_hvac,
            site=site_cmb,
            zone=zone_hvac,
            manufacturer="Trane Technologies",
            model="CVHE-500TR",
            serial_number="TRN-2026-90812",
            purchase_date=date(2024, 3, 15),
            commission_date=date(2024, 4, 1),
            warranty_ref="WR-TRN-2024-5Y",
            description="500-ton water-cooled centrifugal chiller serving plant main loop.",
            attributes={"cooling_tons": "500", "refrigerant": "R-134a", "voltage": "415V 3-Phase"},
            actor=admin_user,
        )

    # Sub-Assembly 1: Compressor
    comp_unit = Asset.objects.filter(organization=org, asset_tag="AST-CMP-001").first()
    if not comp_unit:
        comp_unit = asset_services.create_asset(
            org,
            asset_tag="AST-CMP-001",
            name="Direct-Drive Centrifugal Compressor",
            category=cat_pumps,
            site=site_cmb,
            zone=zone_hvac,
            manufacturer="Trane Technologies",
            model="DD-COMP-500",
            serial_number="CMP-9901-A",
            purchase_date=date(2024, 3, 15),
            commission_date=date(2024, 4, 1),
            actor=admin_user,
        )
        hierarchy.add_component(
            parent=root_chiller,
            child=comp_unit,
            relationship_type="ASSEMBLY",
            quantity=1,
            actor=admin_user,
        )

    # Replaceable Part 1.1: Titanium Impeller
    impeller = Asset.objects.filter(organization=org, asset_tag="AST-IMP-001").first()
    if not impeller:
        impeller = asset_services.create_asset(
            org,
            asset_tag="AST-IMP-001",
            name="Precision Titanium Impeller Stage 1",
            category=cat_pumps,
            site=site_cmb,
            zone=zone_hvac,
            manufacturer="Elliott Turbomachinery",
            model="IMP-TI-101",
            serial_number="SN-IMP-7721",
            actor=admin_user,
        )
        hierarchy.add_component(
            parent=comp_unit,
            child=impeller,
            relationship_type="REPLACEABLE_PART",
            quantity=1,
            part_number="PART-IMP-TI-500",
            notes="Requires dynamic balance check on install",
            actor=admin_user,
        )

    # Replaceable Part 1.2: Magnetic Bearings
    bearing = Asset.objects.filter(organization=org, asset_tag="AST-BRG-001").first()
    if not bearing:
        bearing = asset_services.create_asset(
            org,
            asset_tag="AST-BRG-001",
            name="Active Magnetic Bearing Set",
            category=cat_pumps,
            site=site_cmb,
            zone=zone_hvac,
            manufacturer="SKF Magnetic Bearings",
            model="MB-6000",
            serial_number="SN-SKF-8812",
            actor=admin_user,
        )
        hierarchy.add_component(
            parent=comp_unit,
            child=bearing,
            relationship_type="REPLACEABLE_PART",
            quantity=2,
            part_number="SKF-MB-6000",
            notes="Ceramic backup bearings included",
            actor=admin_user,
        )

    # Sub-Assembly 2: Variable Frequency Drive
    vfd_unit = Asset.objects.filter(organization=org, asset_tag="AST-VFD-001").first()
    if not vfd_unit:
        vfd_unit = asset_services.create_asset(
            org,
            asset_tag="AST-VFD-001",
            name="Danfoss VLT 500kW Inverter Drive",
            category=cat_elec,
            site=site_cmb,
            zone=zone_elec,
            manufacturer="Danfoss",
            model="FC-102-500KW",
            serial_number="DF-VLT-2024-001",
            actor=admin_user,
        )
        hierarchy.add_component(
            parent=root_chiller,
            child=vfd_unit,
            relationship_type="COMPONENT",
            quantity=1,
            part_number="DF-FC102-500",
            actor=admin_user,
        )

    # Standalone Warehouse Spares (Available for Component Replacement / Swapping)
    spare_brg = Asset.objects.filter(organization=org, asset_tag="AST-BRG-SPARE").first()
    if not spare_brg:
        asset_services.create_asset(
            org,
            asset_tag="AST-BRG-SPARE",
            name="SKF Active Magnetic Bearing Set (Stock Spare)",
            category=cat_pumps,
            site=site_cmb,
            zone=zone_hvac,
            manufacturer="SKF Magnetic Bearings",
            model="MB-6000-GEN2",
            serial_number="SN-SKF-9905",
            actor=admin_user,
        )

    spare_imp = Asset.objects.filter(organization=org, asset_tag="AST-IMP-SPARE").first()
    if not spare_imp:
        asset_services.create_asset(
            org,
            asset_tag="AST-IMP-SPARE",
            name="Titanium Impeller Stage 1 (Stock Spare)",
            category=cat_pumps,
            site=site_cmb,
            zone=zone_hvac,
            manufacturer="Elliott Turbomachinery",
            model="IMP-TI-101-REV2",
            serial_number="SN-IMP-8801",
            actor=admin_user,
        )

    # 7. Telemetry Meters
    if not root_chiller.meters.exists():
        m1 = asset_services.create_meter(root_chiller, name="Operating Run Hours", unit="h", actor=admin_user)
        asset_services.record_reading(m1, value=Decimal("1480.5"), notes="Routine maintenance log", actor=admin_user)
        
        m2 = asset_services.create_meter(root_chiller, name="Energy Consumption", unit="kWh", actor=admin_user)
        asset_services.record_reading(m2, value=Decimal("245100.0"), notes="End of month telemetry", actor=admin_user)

    print(f"[OK] Full Asset Hierarchy Tree created under {root_chiller.asset_tag}")
    print("\n=======================================================")
    print("DEMO CREDENTIALS READY FOR LOGIN:")
    print("URL: http://localhost:8080/accounts/login/")
    print("-------------------------------------------------------")
    print("1. Asset Manager:        asset.manager@apexindustrial.com")
    print("2. Organization Admin:   admin@apexindustrial.com")
    print("3. Lead Technician:      lead.technician@apexindustrial.com")
    print("4. Maintenance Planner:  planner@apexindustrial.com")
    print("5. Supervisor:           supervisor@apexindustrial.com")
    print("Password (All Accounts): Password123!")
    print("=======================================================\n")


if __name__ == "__main__":
    seed()
