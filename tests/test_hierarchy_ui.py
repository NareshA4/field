"""
UI Test Suite for FieldOps Nexus Asset Hierarchy Hub, Tree Rendering, and Design Layer.
"""
import pytest
from django.template import Context, Template
from apps.assets import hierarchy, services
from apps.assets.models import Asset, AssetComponent
from apps.ui.templatetags.ui_tags import icon, _ICONS

pytestmark = pytest.mark.django_db


# =========================================================================
# 1. UI Icon Helper & SVG Rendering Tests
# =========================================================================

def test_svg_icon_tag_renders_valid_vector_paths():
    """Verify all critical UI icons render vector SVG paths rather than fallback placeholders."""
    critical_icons = [
        "package", "list", "hard-drive", "refresh-cw", "alert-circle",
        "layers", "external-link", "align-justify", "maximize-2", "minimize-2",
        "info", "copy", "git-branch", "trash-2", "eye", "arrow-up-right", "tag"
    ]
    for icon_name in critical_icons:
        rendered = icon(icon_name, size=16)
        assert 'width="16"' in rendered
        assert 'height="16"' in rendered
        assert 'class="fx-icon"' in rendered
        assert '<svg' in rendered and '</svg>' in rendered
        # Ensure icon is properly mapped in _ICONS dictionary
        assert icon_name in _ICONS
        assert _ICONS[icon_name] in rendered


def test_svg_icon_fallback_for_unknown_name():
    """Unknown icon names should gracefully fallback to the grid icon without throwing exceptions."""
    rendered = icon("non_existent_icon_xyz", size=20)
    assert 'class="fx-icon"' in rendered
    assert _ICONS["grid"] in rendered


# =========================================================================
# 2. Asset Hierarchy Hub Page Rendering Tests
# =========================================================================

@pytest.fixture
def hierarchy_fixture(org_a, site_a1, make_category, owner_a):
    """Fixture with a complete 3-level asset hierarchy tree."""
    cat = make_category(org_a, "HVAC Systems")
    root = services.create_asset(
        org_a, site=site_a1, category=cat, actor=owner_a,
        asset_tag="AST-CHL-001", name="Trane Centrifugal Chiller Unit #1",
        manufacturer="Trane", model="CVHE-500TR", serial_number="TRN-2026-90812"
    )
    compressor = services.create_asset(
        org_a, site=site_a1, category=cat, actor=owner_a,
        asset_tag="AST-CMP-001", name="Direct-Drive Centrifugal Compressor",
        manufacturer="Trane", model="DD-COMP-500", serial_number="CMP-9901-A"
    )
    bearing = services.create_asset(
        org_a, site=site_a1, category=cat, actor=owner_a,
        asset_tag="AST-BRG-001", name="Active Magnetic Bearing Set",
        manufacturer="SKF", model="MB-6000", serial_number="SN-SKF-8812"
    )
    
    # Level 1: Root -> Compressor (Assembly)
    link1 = hierarchy.add_component(
        parent=root, child=compressor, relationship_type="ASSEMBLY",
        quantity=1, part_number="PART-CMP-900", actor=owner_a
    )
    # Level 2: Compressor -> Bearing (Replaceable Part)
    link2 = hierarchy.add_component(
        parent=compressor, child=bearing, relationship_type="REPLACEABLE_PART",
        quantity=2, part_number="SKF-MB-6000", actor=owner_a
    )
    return {"root": root, "compressor": compressor, "bearing": bearing, "link1": link1, "link2": link2}


def test_hierarchy_hub_page_renders_with_kpis_and_toolbar(client, owner_a, hierarchy_fixture):
    """Verify Hierarchy Hub UI renders KPIs, navigation, search, and filter toolbar."""
    client.force_login(owner_a)
    root = hierarchy_fixture["root"]
    
    response = client.get(f"/app/assets/hierarchy/?root={root.pk}")
    assert response.status_code == 200
    html = response.content.decode()

    # KPI summary cards
    assert "Root Assemblies" in html
    assert "Total Sub-Items" in html
    assert "Replaceable Parts" in html
    assert "Parts Missing #" in html

    # Page Header and Breadcrumb
    assert "Asset Hierarchy Hub" in html
    assert "Equipment assembly breakdown, sub-component trees, and replaceable spare parts planning." in html
    assert "Component Catalog" in html
    assert "Asset Registry" in html
    assert "Register Root Asset" in html

    # Interactive Toolbar elements
    assert 'class="fx-hierarchy-toolbar"' in html
    assert 'data-tree-search' in html
    assert 'placeholder="Search tag, part #, serial…"' in html
    assert 'class="fx-filter-pill-group"' in html
    assert 'data-filter-role="all"' in html
    assert 'data-filter-role="assembly"' in html
    assert 'data-filter-role="component"' in html
    assert 'data-filter-role="replaceable"' in html
    assert 'data-action="toggle-density"' in html
    assert 'data-action="expand-all"' in html
    assert 'data-action="collapse-all"' in html


def test_hierarchy_tree_nodes_render_role_badges_and_actions(client, owner_a, hierarchy_fixture):
    """Verify tree nodes render correct role styling, copyable part pills, and action menus."""
    client.force_login(owner_a)
    root = hierarchy_fixture["root"]
    
    response = client.get(f"/app/assets/hierarchy/?root={root.pk}")
    assert response.status_code == 200
    html = response.content.decode()

    # Root Node Rendering
    assert "AST-CHL-001" in html
    assert "Trane Centrifugal Chiller Unit #1" in html
    assert "Root Assembly" in html
    assert "role-root" in html
    assert "is-active" in html
    assert "Active View" in html

    # Child Assembly Node Rendering
    assert "AST-CMP-001" in html
    assert "Direct-Drive Centrifugal Compressor" in html
    assert "Assembly" in html
    assert "role-assembly" in html
    assert "PART-CMP-900" in html

    # Deep Child Replaceable Part Rendering
    assert "AST-BRG-001" in html
    assert "Active Magnetic Bearing Set" in html
    assert "Replaceable Part" in html
    assert "role-replaceable_part" in html
    assert "SKF-MB-6000" in html
    assert 'data-copy="SKF-MB-6000"' in html

    # Quick Action Dropdown items
    assert "Move / Re-parent" in html
    assert "Replace Part / Swap" in html
    assert "Quick Inspection" in html
    assert "Detach from Parent" in html


def test_hierarchy_hub_empty_state_when_no_asset_selected(client, owner_a):
    """Verify clean empty state card when navigating to hub without selecting an asset."""
    client.force_login(owner_a)
    response = client.get("/app/assets/hierarchy/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "No Assembly Selected" in html or "Root Assemblies" in html


# =========================================================================
# 3. Authentication UI Tests (Login Screen & Shell)
# =========================================================================

def test_login_screen_ui_structure(client):
    """Verify login page renders modern centered auth wrap and card container."""
    response = client.get("/accounts/login/")
    assert response.status_code == 200
    html = response.content.decode()
    
    assert "fx-auth-wrap" in html
    assert "fx-auth-card" in html
    assert "FieldOps Nexus" in html
    assert 'name="username"' in html
    assert 'name="password"' in html
    assert "btn-primary" in html


def test_asset_detail_hierarchy_tab_anchors(client, owner_a, hierarchy_fixture):
    """Verify asset detail page hierarchy tab contains anchors for move and replace sections."""
    client.force_login(owner_a)
    compressor = hierarchy_fixture["compressor"]
    
    response = client.get(f"/app/assets/{compressor.pk}/?tab=hierarchy")
    assert response.status_code == 200
    html = response.content.decode()

    # Re-parenting form and section anchor
    assert 'id="parent-section"' in html
    assert 'Move to new parent' in html or 'Parent Asset' in html
