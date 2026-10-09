"""
FieldOps Nexus - Unit Test Suite for Module M03 (Asset Hierarchy)
Exhaustive unit testing for domain functions, validators, tree algorithms, and UI tags.
"""
import os
import sys
import uuid
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

import unittest
from apps.tenancy.models import Organization
from apps.accounts.models import User
from apps.sites.models import Site
from apps.assets.models import AssetCategory, Asset, AssetComponent
from apps.assets import services, hierarchy
from apps.core.exceptions import ValidationFailed, Conflict
from apps.ui.templatetags.ui_tags import icon, _ICONS, hierarchy_trail


class M03UnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org = Organization.objects.filter(name__iexact="Apex Industrial Technologies Ltd").first() or Organization.objects.first()
        cls.org_ext = Organization.objects.exclude(pk=cls.org.pk).first()
        cls.actor = User.objects.filter(email="asset.manager@apexindustrial.com").first() or User.objects.first()
        cls.site_a = Site.objects.filter(organization=cls.org).first()
        cls.site_b = Site.objects.filter(organization=cls.org).exclude(pk=cls.site_a.pk).first()
        cls.cat = AssetCategory.objects.filter(organization=cls.org).first()

    def setUp(self):
        self.run_id = uuid.uuid4().hex[:6].upper()
        self.created_assets = []
        self.created_links = []

    def tearDown(self):
        for link in self.created_links:
            try:
                AssetComponent.objects.filter(pk=link.pk).delete()
            except Exception:
                pass
        for asset in self.created_assets:
            try:
                AssetComponent.objects.filter(parent=asset).delete()
                AssetComponent.objects.filter(child=asset).delete()
                Asset.objects.filter(pk=asset.pk).delete()
            except Exception:
                pass

    def _create_test_asset(self, tag, name, site=None, org=None, status="ACTIVE"):
        org = org or self.org
        site = site or (Site.objects.filter(organization=org).first())
        cat = AssetCategory.objects.filter(organization=org).first() or self.cat
        asset = services.create_asset(
            org, site=site, category=cat, actor=self.actor,
            asset_tag=f"{tag}-{self.run_id}", name=name
        )
        if status != "ACTIVE":
            asset.status = status
            asset.save(update_fields=["status"])
        self.created_assets.append(asset)
        return asset

    # -------------------------------------------------------------------------
    # 1. UNIT TEST: Quantity Validator
    # -------------------------------------------------------------------------
    def test_unit_quantity_validation(self):
        """Unit test hierarchy._check_quantity() validator function."""
        print("\n[UT-01] Testing quantity validator _check_quantity()...")
        hierarchy._check_quantity(1)
        hierarchy._check_quantity(50)
        hierarchy._check_quantity(10000)

        with self.assertRaises(ValidationFailed) as cm:
            hierarchy._check_quantity(0)
        self.assertEqual(cm.exception.code, "invalid_quantity")

        with self.assertRaises(ValidationFailed) as cm:
            hierarchy._check_quantity(-5)
        self.assertEqual(cm.exception.code, "invalid_quantity")

        with self.assertRaises(ValidationFailed) as cm:
            hierarchy._check_quantity(10001)
        self.assertEqual(cm.exception.code, "invalid_quantity")

        with self.assertRaises(ValidationFailed) as cm:
            hierarchy._check_quantity("5")
        self.assertEqual(cm.exception.code, "invalid_quantity")
        print("  [PASS] _check_quantity correctly validates boundary values.")

    # -------------------------------------------------------------------------
    # 2. UNIT TEST: Self-Parenting Guardrail
    # -------------------------------------------------------------------------
    def test_unit_check_link_self_parent(self):
        """Unit test check_link() prevents self-parenting."""
        print("\n[UT-02] Testing check_link() self-parenting prevention...")
        asset = self._create_test_asset("SELF", "Self Node")
        with self.assertRaises(ValidationFailed) as cm:
            hierarchy.check_link(asset, asset)
        self.assertEqual(cm.exception.code, "self_parent")
        self.assertIn("cannot be its own parent", str(cm.exception))
        print("  [PASS] check_link rejected self-parenting.")

    # -------------------------------------------------------------------------
    # 3. UNIT TEST: Cross-Tenant Isolation
    # -------------------------------------------------------------------------
    def test_unit_check_link_cross_tenant(self):
        """Unit test check_link() prevents cross-tenant linking."""
        print("\n[UT-03] Testing check_link() cross-tenant isolation...")
        if not self.org_ext:
            print("  - Skipping cross-tenant test: single tenant environment.")
            return
        parent = self._create_test_asset("TEN1", "Tenant 1 Node", org=self.org)
        child = self._create_test_asset("TEN2", "Tenant 2 Node", org=self.org_ext)
        with self.assertRaises(ValidationFailed) as cm:
            hierarchy.check_link(parent, child)
        self.assertEqual(cm.exception.code, "cross_tenant")
        print("  [PASS] check_link rejected cross-tenant association.")

    # -------------------------------------------------------------------------
    # 4. UNIT TEST: Cross-Site Boundary
    # -------------------------------------------------------------------------
    def test_unit_check_link_cross_site(self):
        """Unit test check_link() prevents cross-site linking."""
        print("\n[UT-04] Testing check_link() cross-site boundary...")
        if not self.site_b:
            print("  - Skipping cross-site test: single site environment.")
            return
        parent = self._create_test_asset("SITEA", "Site A Node", site=self.site_a)
        child = self._create_test_asset("SITEB", "Site B Node", site=self.site_b)
        with self.assertRaises(ValidationFailed) as cm:
            hierarchy.check_link(parent, child)
        self.assertEqual(cm.exception.code, "site_mismatch")
        print("  [PASS] check_link rejected cross-site association.")

    # -------------------------------------------------------------------------
    # 5. UNIT TEST: Terminal Status Protection
    # -------------------------------------------------------------------------
    def test_unit_check_link_terminal_status(self):
        """Unit test check_link() blocks retired/disposed assets."""
        print("\n[UT-05] Testing check_link() terminal status protection...")
        parent_ret = self._create_test_asset("PRET", "Parent Retired", status="RETIRED")
        child_act = self._create_test_asset("CACT", "Child Active", status="ACTIVE")
        with self.assertRaises(Conflict) as cm:
            hierarchy.check_link(parent_ret, child_act)
        self.assertEqual(cm.exception.code, "asset_terminal")

        parent_act = self._create_test_asset("PACT", "Parent Active", status="ACTIVE")
        child_disp = self._create_test_asset("CDISP", "Child Disposed", status="DISPOSED")
        with self.assertRaises(Conflict) as cm:
            hierarchy.check_link(parent_act, child_disp)
        self.assertEqual(cm.exception.code, "asset_terminal")
        print("  [PASS] check_link blocked associations with terminal assets.")

    # -------------------------------------------------------------------------
    # 6. UNIT TEST: Direct & Deep Cycle Detection
    # -------------------------------------------------------------------------
    def test_unit_cycle_detection(self):
        """Unit test ancestors() and check_link() cycle prevention."""
        print("\n[UT-06] Testing cycle detection logic in ancestors() and check_link()...")
        node_a = self._create_test_asset("CYC-A", "Node A")
        node_b = self._create_test_asset("CYC-B", "Node B")
        node_c = self._create_test_asset("CYC-C", "Node C")

        l1 = hierarchy.add_component(node_a, node_b, actor=self.actor)
        self.created_links.append(l1)

        l2 = hierarchy.add_component(node_b, node_c, actor=self.actor)
        self.created_links.append(l2)

        anc_c = hierarchy.ancestors(node_c)
        self.assertEqual([a.pk for a in anc_c], [node_b.pk, node_a.pk])

        with self.assertRaises(ValidationFailed) as cm:
            hierarchy.check_link(node_c, node_a)
        self.assertEqual(cm.exception.code, "hierarchy_cycle")

        with self.assertRaises(ValidationFailed) as cm:
            hierarchy.check_link(node_b, node_a)
        self.assertEqual(cm.exception.code, "hierarchy_cycle")
        print("  [PASS] Cycle detection correctly traversed ancestors and blocked cycles.")

    # -------------------------------------------------------------------------
    # 7. UNIT TEST: Subtree Height Calculation
    # -------------------------------------------------------------------------
    def test_unit_subtree_height(self):
        """Unit test subtree_height() calculation."""
        print("\n[UT-07] Testing subtree_height() calculation...")
        root = self._create_test_asset("H-ROOT", "Height Root")
        sub = self._create_test_asset("H-SUB", "Height Sub")
        leaf = self._create_test_asset("H-LEAF", "Height Leaf")

        self.assertEqual(hierarchy.subtree_height(root), 0)

        l1 = hierarchy.add_component(root, sub, actor=self.actor)
        self.created_links.append(l1)
        self.assertEqual(hierarchy.subtree_height(root), 1)
        self.assertEqual(hierarchy.subtree_height(sub), 0)

        l2 = hierarchy.add_component(sub, leaf, actor=self.actor)
        self.created_links.append(l2)
        self.assertEqual(hierarchy.subtree_height(root), 2)
        self.assertEqual(hierarchy.subtree_height(sub), 1)
        self.assertEqual(hierarchy.subtree_height(leaf), 0)
        print("  [PASS] subtree_height accurately reflects hierarchy depth.")

    # -------------------------------------------------------------------------
    # 8. UNIT TEST: Component Replace & Remove Services
    # -------------------------------------------------------------------------
    def test_unit_replace_and_remove_component(self):
        """Unit test hierarchy.replace_component() and hierarchy.remove_component()."""
        print("\n[UT-08] Testing hierarchy.replace_component() and remove_component()...")
        parent = self._create_test_asset("REP-PAR", "Replace Parent")
        old_part = self._create_test_asset("REP-OLD", "Old Part")
        new_part = self._create_test_asset("REP-NEW", "New Part")

        link = hierarchy.add_component(parent, old_part, relationship_type="REPLACEABLE_PART", part_number="PART-001", actor=self.actor)
        self.created_links.append(link)

        # Execute replace
        new_link = hierarchy.replace_component(link, new_part, reason="Worn bearing", actor=self.actor)
        self.created_links.append(new_link)

        self.assertFalse(AssetComponent.objects.filter(parent=parent, child=old_part).exists())
        self.assertTrue(AssetComponent.objects.filter(parent=parent, child=new_part).exists())
        self.assertEqual(new_link.relationship_type, "REPLACEABLE_PART")
        self.assertIn("Replaced", new_link.notes)

        # Execute remove
        hierarchy.remove_component(new_link, actor=self.actor)
        self.assertFalse(AssetComponent.objects.filter(parent=parent, child=new_part).exists())
        print("  [PASS] replace_component and remove_component executed successfully.")

    # -------------------------------------------------------------------------
    # 9. UNIT TEST: Tree Building & Traversal Helpers (root_of, build_tree, flatten, validate_tree)
    # -------------------------------------------------------------------------
    def test_unit_tree_traversal_and_validation(self):
        """Unit test root_of(), build_tree(), flatten(), and validate_tree()."""
        print("\n[UT-09] Testing root_of(), build_tree(), flatten(), and validate_tree()...")
        root = self._create_test_asset("T-ROOT", "Tree Root")
        child1 = self._create_test_asset("T-CH1", "Child 1")
        child2 = self._create_test_asset("T-CH2", "Child 2")

        l1 = hierarchy.add_component(root, child1, actor=self.actor)
        self.created_links.append(l1)
        l2 = hierarchy.add_component(child1, child2, actor=self.actor)
        self.created_links.append(l2)

        # Test root_of
        self.assertEqual(hierarchy.root_of(child2).pk, root.pk)
        self.assertEqual(hierarchy.root_of(root).pk, root.pk)

        # Test build_tree & flatten
        tree = hierarchy.build_tree(root)
        self.assertEqual(tree["asset"].pk, root.pk)
        self.assertEqual(len(tree["children"]), 1)
        nodes = hierarchy.flatten(tree)
        self.assertEqual(len(nodes), 3)

        # Test validate_tree
        report = hierarchy.validate_tree(root)
        self.assertTrue(report["valid"])
        self.assertEqual(report["nodes"], 3)
        self.assertEqual(len(report["issues"]), 0)
        print("  [PASS] root_of, build_tree, flatten, and validate_tree functioning correctly.")

    # -------------------------------------------------------------------------
    # 10. UNIT TEST: SVG Icon Template Tag
    # -------------------------------------------------------------------------
    def test_unit_icon_template_tag(self):
        """Unit test ui_tags.icon() rendering and fallback."""
        print("\n[UT-10] Testing ui_tags.icon() vector rendering and fallback...")
        self.assertIn("package", _ICONS)
        self.assertIn("trash-2", _ICONS)
        self.assertIn("refresh-cw", _ICONS)

        out = icon("package", size=24)
        self.assertIn('width="24"', out)
        self.assertIn('height="24"', out)
        self.assertIn('class="fx-icon"', out)
        self.assertIn("<svg", out)

        fallback = icon("non_existent_icon_123")
        self.assertIn(_ICONS["grid"], fallback)
        print("  [PASS] icon() template tag rendered correct SVG tags and fallback.")

    # -------------------------------------------------------------------------
    # 11. UNIT TEST: Hierarchy Trail UI Tag
    # -------------------------------------------------------------------------
    def test_unit_hierarchy_trail_tag(self):
        """Unit test ui_tags.hierarchy_trail() breadcrumb output."""
        print("\n[UT-11] Testing ui_tags.hierarchy_trail() breadcrumbs...")
        root = self._create_test_asset("TR-ROOT", "Trail Root")
        sub = self._create_test_asset("TR-SUB", "Trail Sub")
        leaf = self._create_test_asset("TR-LEAF", "Trail Leaf")

        self.assertEqual(hierarchy_trail(root), "")

        l1 = hierarchy.add_component(root, sub, actor=self.actor)
        self.created_links.append(l1)
        l2 = hierarchy.add_component(sub, leaf, actor=self.actor)
        self.created_links.append(l2)

        self.assertEqual(hierarchy_trail(root), "")

        trail = hierarchy_trail(leaf)
        self.assertIn("Machine Path:", trail)
        self.assertIn(root.asset_tag, trail)
        self.assertIn(sub.asset_tag, trail)
        self.assertIn(leaf.asset_tag, trail)
        print("  [PASS] hierarchy_trail outputs clean, accurate machine paths.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
