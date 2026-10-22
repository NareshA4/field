"""
Phase 2 Verification Script: Upstream Ancestor Breadcrumbs in Maintenance & Work Orders.
Verifies:
1. Root assets with no ancestors render clean (no trail / no clutter).
2. Nested component work orders display full upstream ancestor breadcrumbs.
3. Incident/Service request details display full upstream ancestor breadcrumbs.
4. Correct links with /?tab=hierarchy parameter for easy machine navigation.
"""

import os
import sys
from pathlib import Path
from django.utils import timezone

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
import django
django.setup()

from django.conf import settings
settings.SECURE_SSL_REDIRECT = False

from django.test import Client
from apps.rbac import catalog
if not catalog.is_registered("maintenance.view"):
    catalog.register("maintenance", "maintenance.view", "View maintenance plans", site_scoped=True)
from apps.tenancy.models import Organization, Membership
from apps.sites.models import Site
from apps.assets.models import Asset, AssetCategory, AssetComponent, AssetLocationHistory, AssetStatusHistory
from apps.identification.models import AssetIdentifier
from apps.workorders.models import WorkOrder
from apps.incidents.models import ServiceRequest
from apps.accounts.models import User
from apps.assets import services, hierarchy
from apps.ui.templatetags.ui_tags import hierarchy_trail

def run_verification():
    print("=" * 70)
    print("PHASE 2 VERIFICATION: Upstream Ancestor Breadcrumbs in Work Orders & Incidents")
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
    test_tags = ["P2-ROOT-01", "P2-SUB-01", "P2-COMP-01", "P2-STANDALONE-01"]
    for t in test_tags:
        a = Asset.objects.filter(organization=org, asset_tag=t).first()
        if a:
            WorkOrder.objects.filter(asset=a).delete()
            ServiceRequest.objects.filter(asset=a).delete()
            AssetComponent.objects.filter(parent=a).delete()
            AssetComponent.objects.filter(child=a).delete()
            AssetIdentifier.objects.filter(asset=a).delete()
            AssetLocationHistory.objects.filter(asset=a).delete()
            AssetStatusHistory.objects.filter(asset=a).delete()
            a.delete()

    print("\n[Step 1] Creating Asset Hierarchy Tree...")
    root_asset = services.create_asset(org, site=site, category=cat, actor=user, asset_tag="P2-ROOT-01", name="Main Conveyor Line Alpha")
    sub_asset = services.create_asset(org, site=site, category=cat, actor=user, asset_tag="P2-SUB-01", name="Drive Motor Assembly")
    comp_asset = services.create_asset(org, site=site, category=cat, actor=user, asset_tag="P2-COMP-01", name="Vibration Sensor & Bearings")
    standalone_asset = services.create_asset(org, site=site, category=cat, actor=user, asset_tag="P2-STANDALONE-01", name="Standalone Generator")

    # Link hierarchy: Root -> Sub -> Comp
    link1 = hierarchy.add_component(
        parent=root_asset,
        child=sub_asset,
        relationship_type="ASSEMBLY",
        quantity=1,
        actor=user
    )
    link2 = hierarchy.add_component(
        parent=sub_asset,
        child=comp_asset,
        relationship_type="COMPONENT",
        quantity=1,
        actor=user
    )
    print("  ✓ Created hierarchy: P2-ROOT-01 > P2-SUB-01 > P2-COMP-01")

    # 2. Test template tag directly
    print("\n[Step 2] Testing hierarchy_trail Template Tag...")
    
    # Standalone root asset -> should be empty
    trail_standalone = hierarchy_trail(standalone_asset)
    assert trail_standalone == "", f"Expected empty trail for standalone asset, got: {trail_standalone}"
    print("  ✓ Standalone asset returns clean empty trail (no clutter)")

    # Root asset with children -> should be empty (it has no ancestors)
    trail_root = hierarchy_trail(root_asset)
    assert trail_root == "", f"Expected empty trail for root asset, got: {trail_root}"
    print("  ✓ Root asset with children returns clean empty trail")

    # Sub-assembly -> should show root
    trail_sub = hierarchy_trail(sub_asset)
    assert "P2-ROOT-01" in str(trail_sub), f"Expected P2-ROOT-01 in trail, got: {trail_sub}"
    assert f"/app/assets/{root_asset.pk}/?tab=hierarchy" in str(trail_sub)
    print(f"  ✓ 1-Level Sub-Assembly trail correctly references Root Machine: {trail_sub}")

    # Leaf component -> should show root > sub > comp
    trail_comp = hierarchy_trail(comp_asset)
    assert "P2-ROOT-01" in str(trail_comp)
    assert "P2-SUB-01" in str(trail_comp)
    assert "P2-COMP-01" in str(trail_comp)
    assert f"/app/assets/{root_asset.pk}/?tab=hierarchy" in str(trail_comp)
    assert f"/app/assets/{sub_asset.pk}/?tab=hierarchy" in str(trail_comp)
    print(f"  ✓ 2-Level Leaf Component trail: {trail_comp}")

    # 3. Create Work Orders and Incidents
    print("\n[Step 3] Testing Work Orders & Incidents Detail Rendering...")
    wo_comp = WorkOrder.objects.create(
        organization=org, site=site, asset=comp_asset,
        title="Bearing Lubrication and Calibration",
        order_number="WO-P2-001", work_type="PREVENTIVE", priority="MEDIUM", status="PLANNED",
        created_by=user
    )
    wo_root = WorkOrder.objects.create(
        organization=org, site=site, asset=root_asset,
        title="Annual Line Overhaul",
        order_number="WO-P2-002", work_type="CORRECTIVE", priority="HIGH", status="IN_PROGRESS",
        created_by=user
    )

    now = timezone.now()
    sr_comp = ServiceRequest.objects.create(
        organization=org, site=site, asset=comp_asset,
        title="Abnormal Sensor Noise Detected",
        number="SR-P2-001", kind="INCIDENT", severity="HIGH", status="NEW",
        reported_by=membership, occurred_at=now
    )
    sr_root = ServiceRequest.objects.create(
        organization=org, site=site, asset=root_asset,
        title="Main Conveyor Belt Stoppage",
        number="SR-P2-002", kind="INCIDENT", severity="CRITICAL", status="NEW",
        reported_by=membership, occurred_at=now
    )

    client = Client(HTTP_HOST="localhost")
    client.force_login(user)

    # Test Work Order Detail - Component (Should have Machine Path)
    resp_wo_comp = client.get(f"/app/work-orders/{wo_comp.pk}/")
    assert resp_wo_comp.status_code == 200, f"Expected 200, got {resp_wo_comp.status_code}"
    html_wo_comp = resp_wo_comp.content.decode("utf-8")
    assert "Machine Path:" in html_wo_comp, "Expected 'Machine Path:' in component work order detail"
    assert "P2-ROOT-01" in html_wo_comp
    assert "P2-SUB-01" in html_wo_comp
    print("  ✓ Work Order on Component correctly renders upstream Machine Path breadcrumb")

    # Test Work Order Detail - Root Asset (Should NOT have Machine Path)
    resp_wo_root = client.get(f"/app/work-orders/{wo_root.pk}/")
    assert resp_wo_root.status_code == 200, f"Expected 200, got {resp_wo_root.status_code}"
    html_wo_root = resp_wo_root.content.decode("utf-8")
    assert "Machine Path:" not in html_wo_root, "Root asset work order should not have 'Machine Path:'"
    print("  ✓ Work Order on Root Asset cleanly avoids showing redundant Machine Path")

    # Test Incident Detail - Component (Should have Machine Path)
    resp_sr_comp = client.get(f"/app/incidents/{sr_comp.pk}/")
    assert resp_sr_comp.status_code == 200, f"Expected 200, got {resp_sr_comp.status_code}"
    html_sr_comp = resp_sr_comp.content.decode("utf-8")
    assert "Machine Path:" in html_sr_comp, "Expected 'Machine Path:' in component incident detail"
    assert "P2-ROOT-01" in html_sr_comp
    print("  ✓ Incident on Component correctly renders upstream Machine Path breadcrumb")

    # Test Incident Detail - Root Asset (Should NOT have Machine Path)
    resp_sr_root = client.get(f"/app/incidents/{sr_root.pk}/")
    assert resp_sr_root.status_code == 200, f"Expected 200, got {resp_sr_root.status_code}"
    html_sr_root = resp_sr_root.content.decode("utf-8")
    assert "Machine Path:" not in html_sr_root, "Root asset incident should not have 'Machine Path:'"
    print("  ✓ Incident on Root Asset cleanly avoids showing redundant Machine Path")

    # 4. Clean up
    print("\n[Step 4] Cleaning up test data...")
    wo_comp.delete()
    wo_root.delete()
    sr_comp.delete()
    sr_root.delete()
    link1.delete()
    link2.delete()
    for t in test_tags:
        a = Asset.objects.filter(organization=org, asset_tag=t).first()
        if a:
            AssetIdentifier.objects.filter(asset=a).delete()
            AssetLocationHistory.objects.filter(asset=a).delete()
            AssetStatusHistory.objects.filter(asset=a).delete()
            a.delete()
    print("  ✓ Test data cleaned up successfully")

    print("\n" + "=" * 70)
    print(">>> PHASE 2 COMPLETE: ALL CHECKS PASSED (100% SUCCESS) <<<")
    print("=" * 70)

if __name__ == "__main__":
    run_verification()
