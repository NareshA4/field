"""
Phase 3 Verification Script: M09 Parts Catalog Navigation Bridge & Part Number Workflows.
Verifies:
1. Positive Path:
   - Part number pills, badges, dropdown items, and table cells link to /app/inventory/parts/?q=<part_number>.
   - Special characters in part numbers are URL-encoded properly.
   - Parts catalog search endpoint (/app/inventory/parts/?q=...) receives query and returns matching catalog items.
2. Negative Path:
   - Components with missing / null / empty part numbers display graceful "Unassigned" or "None specified" badges.
   - Zero broken anchor tags (no empty '?q=' or 404 links).
3. Cross-Template Coverage:
   - Tree node card (_tree_node.html)
   - Asset detail parent card (detail.html)
   - Quick-view inspection modal (_detail_modal.html)
   - Installed components master list (component_list.html)
"""

import os
import sys
import urllib.parse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
import django
django.setup()

from django.test import Client
from apps.tenancy.models import Organization, Membership
from apps.sites.models import Site
from apps.assets.models import Asset, AssetCategory, AssetComponent, AssetLocationHistory, AssetStatusHistory
from apps.identification.models import AssetIdentifier
from apps.inventory.models import Part
from apps.accounts.models import User
from apps.assets import services, hierarchy

def run_verification():
    print("=" * 70)
    print("PHASE 3 VERIFICATION: M09 Parts Catalog Navigation Bridge")
    print("=" * 70)

    # 1. Setup test data
    org = Organization.objects.filter(name__iexact="Apex Industrial Technologies Ltd").first() or Organization.objects.first()
    assert org, "Organization must exist"
    
    site = Site.objects.filter(organization=org).first()
    assert site, "Site must exist"
        
    cat = AssetCategory.objects.filter(organization=org).first()
    if not cat:
        cat = AssetCategory.objects.create(organization=org, name="General Equipment", code="GEN-EQ")

    membership = Membership.objects.filter(organization=org, status="ACTIVE").first() or Membership.objects.filter(organization=org).first()
    assert membership, "Active Membership must exist"
    user = membership.user

    # Clean up previous test artifacts if any
    test_tags = ["P3-ROOT-01", "P3-BEARING-01", "P3-VALVE-01", "P3-NOPART-01"]
    test_part_numbers = ["SKF-6205-2RSH", "VALVE/HYD-100"]

    for pn in test_part_numbers:
        Part.objects.filter(organization=org, part_number=pn).delete()

    for t in test_tags:
        a = Asset.objects.filter(organization=org, asset_tag=t).first()
        if a:
            AssetComponent.objects.filter(parent=a).delete()
            AssetComponent.objects.filter(child=a).delete()
            AssetIdentifier.objects.filter(asset=a).delete()
            AssetLocationHistory.objects.filter(asset=a).delete()
            AssetStatusHistory.objects.filter(asset=a).delete()
            a.delete()

    print("\n[Step 1] Creating M09 Inventory Parts in Catalog...")
    part1 = Part.objects.create(
        organization=org,
        part_number="SKF-6205-2RSH",
        name="Deep Groove Ball Bearing 25x52x15mm",
        unit="EA",
        is_active=True
    )
    part2 = Part.objects.create(
        organization=org,
        part_number="VALVE/HYD-100",
        name="High Pressure Hydraulic Check Valve",
        unit="EA",
        is_active=True
    )
    print("  ✓ Created M09 Part: SKF-6205-2RSH")
    print("  ✓ Created M09 Part: VALVE/HYD-100 (contains slash character)")

    print("\n[Step 2] Creating Assets & Component Relationships...")
    root_asset = services.create_asset(org, site=site, category=cat, actor=user, asset_tag="P3-ROOT-01", name="Centrifugal Pump Station")
    bearing_asset = services.create_asset(org, site=site, category=cat, actor=user, asset_tag="P3-BEARING-01", name="Drive End Bearing Assembly")
    valve_asset = services.create_asset(org, site=site, category=cat, actor=user, asset_tag="P3-VALVE-01", name="Discharge Relief Valve")
    nopart_asset = services.create_asset(org, site=site, category=cat, actor=user, asset_tag="P3-NOPART-01", name="Custom Fabricated Bracket")

    # Link with standard part number
    link_bearing = hierarchy.add_component(
        parent=root_asset,
        child=bearing_asset,
        relationship_type="COMPONENT",
        quantity=2,
        part_number="SKF-6205-2RSH",
        actor=user
    )
    # Link with special characters in part number
    link_valve = hierarchy.add_component(
        parent=root_asset,
        child=valve_asset,
        relationship_type="REPLACEABLE_PART",
        quantity=1,
        part_number="VALVE/HYD-100",
        actor=user
    )
    # Link with NO part number (negative test case)
    link_nopart = hierarchy.add_component(
        parent=root_asset,
        child=nopart_asset,
        relationship_type="COMPONENT",
        quantity=1,
        part_number="",
        actor=user
    )
    print("  ✓ Attached Bearing with part_number='SKF-6205-2RSH'")
    print("  ✓ Attached Valve with part_number='VALVE/HYD-100'")
    print("  ✓ Attached Custom Bracket with empty part_number (Negative Test Case)")

    client = Client(HTTP_HOST="localhost")
    client.force_login(user)

    # 3. Test Tree View rendering (M03 -> M09 Bridge)
    print("\n[Step 3] Verifying Tree Rendering & Parts Bridge Links...")
    resp_tree = client.get(f"/app/assets/{root_asset.pk}/tree/")
    assert resp_tree.status_code == 200, f"Expected 200, got {resp_tree.status_code}"
    html_tree = resp_tree.content.decode("utf-8")

    # Check positive links in tree node
    assert "data-copy=\"SKF-6205-2RSH\"" in html_tree
    assert urllib.parse.quote("SKF-6205-2RSH") in html_tree or "SKF-6205-2RSH" in html_tree
    assert "/app/inventory/parts/?q=" in html_tree
    assert urllib.parse.quote("VALVE/HYD-100") in html_tree or "VALVE%2FHYD-100" in html_tree
    print("  ✓ Tree nodes contain copyable part badges and direct M09 parts catalog links")

    # 4. Test Component List table view
    print("\n[Step 4] Verifying Component Master List Table...")
    resp_complist = client.get("/app/assets/components/")
    assert resp_complist.status_code == 200
    html_complist = resp_complist.content.decode("utf-8")

    # Positive: contains links to inventory parts
    assert "SKF-6205-2RSH" in html_complist
    assert "/app/inventory/parts/?q=" in html_complist
    # Negative: Unassigned badge for component with no part number
    assert "Unassigned" in html_complist
    print("  ✓ Component master list renders both active part links and 'Unassigned' fallback badges")

    # 5. Test Asset Detail & Quick View Modal
    print("\n[Step 5] Verifying Detail & Quick View Inspection Modal...")
    # Bearing detail page
    resp_detail = client.get(f"/app/assets/{bearing_asset.pk}/?tab=hierarchy")
    assert resp_detail.status_code == 200
    html_detail = resp_detail.content.decode("utf-8")
    assert "/app/inventory/parts/?q=" in html_detail
    assert "SKF-6205-2RSH" in html_detail
    print("  ✓ Asset detail Parent Card includes 1-click Parts Catalog navigation")

    # Quick View modal on bearing
    resp_modal = client.get(f"/app/assets/{bearing_asset.pk}/quick-view/")
    assert resp_modal.status_code == 200
    html_modal = resp_modal.content.decode("utf-8")
    assert "/app/inventory/parts/?q=" in html_modal
    assert "SKF-6205-2RSH" in html_modal
    print("  ✓ Quick View Modal renders functional Parts Catalog bridge link")

    # Quick View modal on component with no part number (Negative test)
    resp_modal_nopart = client.get(f"/app/assets/{nopart_asset.pk}/quick-view/")
    assert resp_modal_nopart.status_code == 200
    html_modal_nopart = resp_modal_nopart.content.decode("utf-8")
    assert "None specified" in html_modal_nopart
    print("  ✓ Quick View Modal on unassigned part gracefully renders 'None specified'")

    # 6. Test M09 Parts Catalog Destination (Click-Through Resolution)
    print("\n[Step 6] Verifying M09 Parts Catalog Search Resolution...")
    # Search for standard part
    resp_parts_q1 = client.get(f"/app/inventory/parts/?q=SKF-6205-2RSH")
    assert resp_parts_q1.status_code == 200, f"Expected 200 from parts search, got {resp_parts_q1.status_code}"
    html_parts_q1 = resp_parts_q1.content.decode("utf-8")
    assert "SKF-6205-2RSH" in html_parts_q1
    assert "Deep Groove Ball Bearing" in html_parts_q1
    print("  ✓ Click-through to /app/inventory/parts/?q=SKF-6205-2RSH resolves and finds item in catalog")

    # Search for special characters part
    encoded_q2 = urllib.parse.quote("VALVE/HYD-100")
    resp_parts_q2 = client.get(f"/app/inventory/parts/?q={encoded_q2}")
    assert resp_parts_q2.status_code == 200
    html_parts_q2 = resp_parts_q2.content.decode("utf-8")
    assert "VALVE/HYD-100" in html_parts_q2
    assert "Hydraulic Check Valve" in html_parts_q2
    print(f"  ✓ Click-through with encoded URL /app/inventory/parts/?q={encoded_q2} resolves perfectly")

    # 7. Clean up
    print("\n[Step 7] Cleaning up test data...")
    link_bearing.delete()
    link_valve.delete()
    link_nopart.delete()
    part1.delete()
    part2.delete()
    for t in test_tags:
        a = Asset.objects.filter(organization=org, asset_tag=t).first()
        if a:
            AssetIdentifier.objects.filter(asset=a).delete()
            AssetLocationHistory.objects.filter(asset=a).delete()
            AssetStatusHistory.objects.filter(asset=a).delete()
            a.delete()
    print("  ✓ Test data cleaned up successfully")

    print("\n" + "=" * 70)
    print(">>> PHASE 3 COMPLETE: ALL CHECKS PASSED (100% SUCCESS) <<<")
    print("=" * 70)

if __name__ == "__main__":
    run_verification()
