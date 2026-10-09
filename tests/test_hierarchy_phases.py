"""
Automated Verification Suite for Component Move and Circular Dependency Prevention (Phases 1 - 5).
"""
import pytest
from apps.assets import hierarchy, services
from apps.assets.models import Asset, AssetComponent
from apps.audit.models import AuditLog
from apps.core.exceptions import ValidationFailed, Conflict

pytestmark = pytest.mark.django_db


def test_phase_1_component_move_and_reparenting(org_a, site_a1, make_category, owner_a):
    """Phase 1: Successfully move a component from parent 1 to parent 2."""
    cat = make_category(org_a, "HVAC")
    
    # Create Root 1, Root 2, and Child Component
    root1 = services.create_asset(org_a, site=site_a1, category=cat, actor=owner_a, asset_tag="ROOT-01", name="Root 1")
    root2 = services.create_asset(org_a, site=site_a1, category=cat, actor=owner_a, asset_tag="ROOT-02", name="Root 2")
    child = services.create_asset(org_a, site=site_a1, category=cat, actor=owner_a, asset_tag="COMP-01", name="Compressor")
    
    # Initially link under Root 1
    link = hierarchy.add_component(parent=root1, child=child, relationship_type="COMPONENT", quantity=1, actor=owner_a)
    assert link.parent == root1
    assert hierarchy.ancestors(child)[0] == root1
    
    # Move child from Root 1 to Root 2
    moved_link = hierarchy.move_component(link, root2, actor=owner_a)
    assert moved_link.parent == root2
    assert hierarchy.ancestors(child)[0] == root2
    assert not AssetComponent.objects.filter(parent=root1, child=child).exists()
    assert AssetComponent.objects.filter(parent=root2, child=child).exists()


def test_phase_2_direct_circular_dependency_prevention(org_a, site_a1, make_category, owner_a):
    """Phase 2: Direct circular link (A -> B, then moving A under B) must be blocked."""
    cat = make_category(org_a, "HVAC")
    asset_a = services.create_asset(org_a, site=site_a1, category=cat, actor=owner_a, asset_tag="CHILLER-A", name="Chiller A")
    asset_b = services.create_asset(org_a, site=site_a1, category=cat, actor=owner_a, asset_tag="PUMP-B", name="Pump B")
    
    # Link A -> B
    hierarchy.add_component(parent=asset_a, child=asset_b, actor=owner_a)
    
    # Try adding B -> A (Must raise hierarchy_cycle)
    with pytest.raises(ValidationFailed) as exc:
        hierarchy.add_component(parent=asset_b, child=asset_a, actor=owner_a)
    assert exc.value.code == "hierarchy_cycle"


def test_phase_3_multilevel_deep_circular_dependency_prevention(org_a, site_a1, make_category, owner_a):
    """Phase 3: Multi-level cycle (A -> B -> C, then moving A under C) must be blocked."""
    cat = make_category(org_a, "HVAC")
    node_a = services.create_asset(org_a, site=site_a1, category=cat, actor=owner_a, asset_tag="NODE-A", name="Node A")
    node_b = services.create_asset(org_a, site=site_a1, category=cat, actor=owner_a, asset_tag="NODE-B", name="Node B")
    node_c = services.create_asset(org_a, site=site_a1, category=cat, actor=owner_a, asset_tag="NODE-C", name="Node C")
    
    # Build chain: A -> B -> C
    link_ab = hierarchy.add_component(parent=node_a, child=node_b, actor=owner_a)
    link_bc = hierarchy.add_component(parent=node_b, child=node_c, actor=owner_a)
    
    assert hierarchy.ancestors(node_c) == [node_b, node_a]
    
    # Try creating cycle: C -> A
    with pytest.raises(ValidationFailed) as exc:
        hierarchy.add_component(parent=node_c, child=node_a, actor=owner_a)
    assert exc.value.code == "hierarchy_cycle"


def test_phase_4_self_parenting_prevention(org_a, site_a1, make_category, owner_a):
    """Phase 4: Self-parenting (A -> A) must be blocked."""
    cat = make_category(org_a, "HVAC")
    asset = services.create_asset(org_a, site=site_a1, category=cat, actor=owner_a, asset_tag="SELF-01", name="Self Test")
    
    with pytest.raises(ValidationFailed) as exc:
        hierarchy.add_component(parent=asset, child=asset, actor=owner_a)
    assert exc.value.code == "self_parent"


def test_phase_5_immutable_audit_logging(org_a, site_a1, make_category, owner_a):
    """Phase 5: Move operations must generate immutable audit records with before/after snapshots."""
    cat = make_category(org_a, "HVAC")
    root1 = services.create_asset(org_a, site=site_a1, category=cat, actor=owner_a, asset_tag="AUD-ROOT-1", name="Aud Root 1")
    root2 = services.create_asset(org_a, site=site_a1, category=cat, actor=owner_a, asset_tag="AUD-ROOT-2", name="Aud Root 2")
    child = services.create_asset(org_a, site=site_a1, category=cat, actor=owner_a, asset_tag="AUD-COMP-1", name="Aud Comp 1")
    
    link = hierarchy.add_component(parent=root1, child=child, actor=owner_a)
    hierarchy.move_component(link, root2, actor=owner_a)
    
    # Verify audit trail
    move_log = AuditLog.objects.filter(organization=org_a, action="asset.component_moved").first()
    assert move_log is not None
    assert move_log.actor == owner_a
    assert move_log.before["parent_id"] == str(root1.pk)
    assert move_log.after["parent_id"] == str(root2.pk)
