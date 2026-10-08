"""
FieldOps Nexus - Phase-by-Phase Verification Script for Component Move & Cycle Prevention
"""
import os
import sys
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

from apps.tenancy.models import Organization
from apps.accounts.models import User
from apps.sites.models import Site
from apps.assets.models import AssetCategory, Asset, AssetComponent
from apps.assets import services, hierarchy
from apps.audit.models import AuditLog
from apps.core.exceptions import ValidationFailed, Conflict


def run_verification():
    print("=" * 70)
    print("FIELDOPS NEXUS: COMPONENT MOVE & CYCLE PREVENTION VERIFICATION")
    print("=" * 70)

    org = Organization.objects.filter(name__iexact="Apex Industrial Technologies Ltd").first()
    if not org:
        org = Organization.objects.first()
    assert org, "Organization must exist"
    
    actor = User.objects.filter(email="asset.manager@apexindustrial.com").first() or User.objects.first()
    site = Site.objects.filter(organization=org).first()
    cat = AssetCategory.objects.filter(organization=org).first()
    
    print(f"[*] Organization: {org.name} | Actor: {actor.email} | Site: {site.name}")
    print("-" * 70)

    import uuid
    run_id = uuid.uuid4().hex[:6].upper()
    tag_r1 = f"ROOT1-{run_id}"
    tag_r2 = f"ROOT2-{run_id}"
    tag_c1 = f"COMP1-{run_id}"
    tag_c2 = f"COMP2-{run_id}"

    # -------------------------------------------------------------
    # PHASE 1: Successful Component Re-parenting
    # -------------------------------------------------------------
    print("[PHASE 1] Testing Component Move & Re-Parenting...")
    root1 = services.create_asset(org, site=site, category=cat, actor=actor, asset_tag=tag_r1, name="Phase 1 Root 1")
    root2 = services.create_asset(org, site=site, category=cat, actor=actor, asset_tag=tag_r2, name="Phase 1 Root 2")
    child = services.create_asset(org, site=site, category=cat, actor=actor, asset_tag=tag_c1, name="Phase 1 Child")
    
    # 1. Attach child under root1
    link = hierarchy.add_component(parent=root1, child=child, relationship_type="COMPONENT", quantity=1, actor=actor)
    assert link.parent_id == root1.pk
    assert hierarchy.ancestors(child)[0].pk == root1.pk
    print("  [OK] Child successfully attached under Root 1")

    # 2. Move child to root2
    moved = hierarchy.move_component(link, root2, actor=actor)
    assert moved.parent_id == root2.pk
    assert hierarchy.ancestors(child)[0].pk == root2.pk
    assert not AssetComponent.objects.filter(parent=root1, child=child).exists()
    assert AssetComponent.objects.filter(parent=root2, child=child).exists()
    print("  [OK] Child successfully moved to Root 2 (Verified in DB)")
    print("  [RESULT] PHASE 1: PASS\n")

    # -------------------------------------------------------------
    # PHASE 2: Direct Circular Dependency Prevention
    # -------------------------------------------------------------
    print("[PHASE 2] Testing Direct Circular Dependency Prevention...")
    # Attempting to attach root2 under child (which is currently under root2)
    cycle_detected = False
    try:
        hierarchy.add_component(parent=child, child=root2, actor=actor)
    except ValidationFailed as exc:
        if exc.code == "hierarchy_cycle":
            cycle_detected = True
            print(f"  [OK] Direct cycle correctly blocked with code: '{exc.code}' - {exc.message}")
    assert cycle_detected, "Direct cycle was not prevented!"
    print("  [RESULT] PHASE 2: PASS\n")

    # -------------------------------------------------------------
    # PHASE 3: Deep / Multi-Level Circular Dependency Prevention
    # -------------------------------------------------------------
    print("[PHASE 3] Testing Multi-Level / Deep Cycle Prevention...")
    sub_child = services.create_asset(org, site=site, category=cat, actor=actor, asset_tag=tag_c2, name="Phase 3 Sub-Child")
    link2 = hierarchy.add_component(parent=child, child=sub_child, relationship_type="REPLACEABLE_PART", quantity=1, actor=actor)
    assert hierarchy.ancestors(sub_child) == [child, root2]
    
    deep_cycle_detected = False
    try:
        # Attempt to make root2 a child of sub_child (Root2 -> Child -> SubChild -> Root2)
        hierarchy.add_component(parent=sub_child, child=root2, actor=actor)
    except ValidationFailed as exc:
        if exc.code == "hierarchy_cycle":
            deep_cycle_detected = True
            print(f"  [OK] Deep multi-level cycle correctly blocked: '{exc.code}' - {exc.message}")
    assert deep_cycle_detected, "Deep cycle was not prevented!"
    print("  [RESULT] PHASE 3: PASS\n")

    # -------------------------------------------------------------
    # PHASE 4: Self-Parenting Prevention
    # -------------------------------------------------------------
    print("[PHASE 4] Testing Self-Parenting Prevention...")
    self_parent_detected = False
    try:
        hierarchy.add_component(parent=root1, child=root1, actor=actor)
    except ValidationFailed as exc:
        if exc.code == "self_parent":
            self_parent_detected = True
            print(f"  [OK] Self-parenting correctly blocked: '{exc.code}' - {exc.message}")
    assert self_parent_detected, "Self-parenting was not prevented!"
    print("  [RESULT] PHASE 4: PASS\n")

    # -------------------------------------------------------------
    # PHASE 5: Immutable Audit Trail Verification
    # -------------------------------------------------------------
    print("[PHASE 5] Testing Immutable Audit Logging...")
    move_log = AuditLog.objects.filter(organization=org, action="asset.component_moved", target_id=str(child.pk)).order_by("-created_at").first()
    assert move_log is not None, "Audit log record not found!"
    assert move_log.actor_id == actor.pk
    assert move_log.before.get("parent_id") == str(root1.pk)
    assert move_log.after.get("parent_id") == str(root2.pk)
    print(f"  [OK] Audit record verified: action='{move_log.action}' | target='{child.asset_tag}'")
    print(f"  [OK] Snapshot delta: parent {move_log.before.get('parent_id')} -> {move_log.after.get('parent_id')}")
    print("  [RESULT] PHASE 5: PASS\n")

    # Clean up test component relationships
    print("[*] Cleaning up temporary test component relationships...")
    AssetComponent.objects.filter(pk__in=[link.pk, link2.pk]).delete()
    print("[OK] Test component relationships cleaned up.")
    print("=" * 70)
    print("ALL 5 PHASES COMPLETED AND VERIFIED SUCCESSFULLY: 100% PASS")
    print("=" * 70)


if __name__ == "__main__":
    run_verification()
