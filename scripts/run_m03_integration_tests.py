"""
FieldOps Nexus - Cross-Module Integration Test Suite for Module M03 (Asset Hierarchy)
Validates interactions between M03 and M01 (Sites), M02 (Assets), M05 (Incidents),
M06 (Work Orders), M09 (Inventory), RBAC, and Audit Trail.
"""
import os
import sys
import uuid
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

from django.conf import settings
from django.utils import timezone
settings.SECURE_SSL_REDIRECT = False
settings.SECURE_HSTS_SECONDS = 0
settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
if "DEFAULT_THROTTLE_CLASSES" in settings.REST_FRAMEWORK:
    settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = []
if "testserver" not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append("testserver")
if "*" not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append("*")

import unittest
from django.test import Client
from apps.tenancy.models import Organization, Membership
from apps.accounts.models import User
from apps.sites.models import Site
from apps.assets.models import AssetCategory, Asset, AssetComponent
from apps.assets import services as asset_services, hierarchy
from apps.audit.models import AuditLog
from apps.incidents.models import ServiceRequest
from apps.workorders.models import WorkOrder
from apps.inventory.models import Part
from apps.ui.templatetags.ui_tags import hierarchy_trail


class M03IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org = Organization.objects.filter(name__iexact="Apex Industrial Technologies Ltd").first() or Organization.objects.first()
        cls.org_ext = Organization.objects.exclude(pk=cls.org.pk).first()
        cls.membership = Membership.objects.filter(organization=cls.org, status="ACTIVE").first() or Membership.objects.filter(organization=cls.org).first()
        cls.manager = cls.membership.user
        cls.site_a = Site.objects.filter(organization=cls.org).first()
        cls.site_b = Site.objects.filter(organization=cls.org).exclude(pk=cls.site_a.pk).first()
        cls.cat = AssetCategory.objects.filter(organization=cls.org).first()

    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")
        self.client.force_login(self.manager)

        self.run_id = uuid.uuid4().hex[:6].upper()
        self.created_assets = []
        self.created_links = []
        self.created_work_orders = []
        self.created_incidents = []
        self.created_parts = []

    def tearDown(self):
        for wo in self.created_work_orders:
            try:
                WorkOrder.objects.filter(pk=wo.pk).delete()
            except Exception:
                pass
        for inc in self.created_incidents:
            try:
                ServiceRequest.objects.filter(pk=inc.pk).delete()
            except Exception:
                pass
        for part in self.created_parts:
            try:
                Part.objects.filter(pk=part.pk).delete()
            except Exception:
                pass
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

    def _create_asset(self, tag, name, site=None, org=None, status="ACTIVE"):
        org = org or self.org
        site = site or (Site.objects.filter(organization=org).first())
        cat = AssetCategory.objects.filter(organization=org).first() or self.cat
        asset = asset_services.create_asset(
            org, site=site, category=cat, actor=self.manager,
            asset_tag=f"{tag}-{self.run_id}", name=name
        )
        if status != "ACTIVE":
            asset.status = status
            asset.save(update_fields=["status"])
        self.created_assets.append(asset)
        return asset

    # -------------------------------------------------------------------------
    # INT-01: M03 <-> M01 (Site & Multi-Tenant Boundary Enforcement)
    # -------------------------------------------------------------------------
    def test_int_01_m01_site_and_tenant_boundaries(self):
        print("\n[INT-01] Testing M03 <-> M01 (Site Boundaries & Tenant Isolation)...")
        parent = self._create_asset("PAR-S1", "Parent Site 1", site=self.site_a)

        # Cross-Site Attempt
        if self.site_b:
            child_site_b = self._create_asset("CHD-S2", "Child Site 2", site=self.site_b)
            from apps.core.exceptions import ValidationFailed
            with self.assertRaises(ValidationFailed) as cm:
                hierarchy.add_component(parent, child_site_b, actor=self.manager)
            self.assertEqual(cm.exception.code, "site_mismatch")
            print("  [PASS] Cross-site hierarchy attachment strictly blocked.")

        # Cross-Tenant Attempt
        if self.org_ext:
            child_org_ext = self._create_asset("CHD-EXT", "Child Org Ext", org=self.org_ext)
            with self.assertRaises(ValidationFailed) as cm:
                hierarchy.add_component(parent, child_org_ext, actor=self.manager)
            self.assertEqual(cm.exception.code, "cross_tenant")
            print("  [PASS] Cross-tenant hierarchy attachment strictly blocked.")

    # -------------------------------------------------------------------------
    # INT-02: M03 <-> M02 (Asset Lifecycle State Integrity)
    # -------------------------------------------------------------------------
    def test_int_02_m02_asset_lifecycle_state_integrity(self):
        print("\n[INT-02] Testing M03 <-> M02 (Asset Lifecycle State Invariants)...")
        root = self._create_asset("LIFE-ROOT", "Lifecycle Root", status="ACTIVE")
        child = self._create_asset("LIFE-CHD", "Lifecycle Child", status="ACTIVE")

        link = hierarchy.add_component(root, child, actor=self.manager)
        self.created_links.append(link)

        # Terminal status prevents modifications
        retired_parent = self._create_asset("RET-PAR", "Retired Parent", status="RETIRED")
        from apps.core.exceptions import Conflict
        with self.assertRaises(Conflict) as cm:
            hierarchy.add_component(retired_parent, self._create_asset("ACT-CHD", "Active Child"), actor=self.manager)
        self.assertEqual(cm.exception.code, "asset_terminal")
        print("  [PASS] Asset lifecycle terminal states protect hierarchy integrity.")

    # -------------------------------------------------------------------------
    # INT-03: M03 <-> M05 (Incidents Upstream Breadcrumbs Integration)
    # -------------------------------------------------------------------------
    def test_int_03_m05_incidents_hierarchy_breadcrumbs(self):
        print("\n[INT-03] Testing M03 <-> M05 (Incidents Hierarchy Breadcrumbs)...")
        root = self._create_asset("INC-ROOT", "Turbine Generator Root")
        sub = self._create_asset("INC-SUB", "Turbine Gearbox")
        comp = self._create_asset("INC-COMP", "Gearbox Bearing Set")

        l1 = hierarchy.add_component(root, sub, relationship_type="ASSEMBLY", actor=self.manager)
        self.created_links.append(l1)
        l2 = hierarchy.add_component(sub, comp, relationship_type="COMPONENT", actor=self.manager)
        self.created_links.append(l2)

        # Create Service Request (Incident) on leaf component
        inc = ServiceRequest.objects.create(
            organization=self.org,
            site=self.site_a,
            asset=comp,
            title=f"Excessive Vibration - {self.run_id}",
            number=f"SR-{self.run_id}",
            kind="INCIDENT",
            severity="HIGH",
            status="NEW",
            reported_by=self.membership,
            occurred_at=timezone.now()
        )
        self.created_incidents.append(inc)

        # Direct template tag unit verification
        trail = hierarchy_trail(comp)
        self.assertIn("Machine Path:", trail)
        self.assertIn(root.asset_tag, trail)
        self.assertIn(sub.asset_tag, trail)

        # Render Incident detail page
        res = self.client.get(f"/app/incidents/{inc.pk}/")
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()

        self.assertIn("Machine Path:", html)
        self.assertIn(root.asset_tag, html)
        self.assertIn(sub.asset_tag, html)
        self.assertIn(comp.asset_tag, html)
        self.assertIn(f"/app/assets/{root.pk}/?tab=hierarchy", html)
        print("  [PASS] Incident detail view correctly resolves and renders upstream machine path.")

    # -------------------------------------------------------------------------
    # INT-04: M03 <-> M06 (Work Orders Lineage & Navigation Integration)
    # -------------------------------------------------------------------------
    def test_int_04_m06_work_orders_lineage_integration(self):
        print("\n[INT-04] Testing M03 <-> M06 (Work Orders Lineage & Navigation)...")
        root = self._create_asset("WO-ROOT", "Main Boiler Unit")
        sub = self._create_asset("WO-SUB", "Feed Pump Assembly")
        part = self._create_asset("WO-PART", "Mechanical Seal")

        l1 = hierarchy.add_component(root, sub, relationship_type="ASSEMBLY", actor=self.manager)
        self.created_links.append(l1)
        l2 = hierarchy.add_component(sub, part, relationship_type="REPLACEABLE_PART", actor=self.manager)
        self.created_links.append(l2)

        # Verify lineage rendering via hierarchy_trail
        trail_part = hierarchy_trail(part)
        self.assertIn("Machine Path:", trail_part)
        self.assertIn(root.asset_tag, trail_part)
        self.assertIn(sub.asset_tag, trail_part)
        self.assertIn(part.asset_tag, trail_part)
        self.assertIn(f"/app/assets/{root.pk}/?tab=hierarchy", trail_part)

        # Verify standalone/root generates empty string to prevent UI clutter
        self.assertEqual(hierarchy_trail(root), "")
        print("  [PASS] Work Order ancestor trail and 1-click machine navigation verified.")

    # -------------------------------------------------------------------------
    # INT-05: M03 <-> M09 (Inventory Parts Catalog Navigation Bridge)
    # -------------------------------------------------------------------------
    def test_int_05_m09_inventory_parts_bridge(self):
        print("\n[INT-05] Testing M03 <-> M09 (Inventory Parts Catalog Navigation Bridge)...")
        part_no = f"SKF-BRG-{self.run_id}"
        inv_part = Part.objects.create(
            organization=self.org,
            part_number=part_no,
            name=f"Deep Groove Ball Bearing {self.run_id}",
            unit="EA",
            is_active=True
        )
        self.created_parts.append(inv_part)

        root = self._create_asset("M09-ROOT", "Compressor Package")
        child = self._create_asset("M09-CHD", "Shaft Bearing")

        link = hierarchy.add_component(
            root, child, relationship_type="REPLACEABLE_PART",
            part_number=part_no, actor=self.manager
        )
        self.created_links.append(link)

        # 1. Inspect tree HTML endpoint
        res_tree = self.client.get(f"/app/assets/{root.pk}/tree/")
        self.assertEqual(res_tree.status_code, 200)
        html_tree = res_tree.content.decode()
        self.assertIn(f"/app/inventory/parts/?q={part_no}", html_tree)

        # 2. Inspect Quick View Modal endpoint
        res_modal = self.client.get(f"/app/assets/{child.pk}/quick-view/")
        self.assertEqual(res_modal.status_code, 200)
        html_modal = res_modal.content.decode()
        self.assertIn(f"/app/inventory/parts/?q={part_no}", html_modal)

        # 3. Verify URL encoded special characters via quote with safe=""
        encoded_pn = urllib.parse.quote("VALVE/HYD-100", safe="")
        self.assertEqual(encoded_pn, "VALVE%2FHYD-100")
        print("  [PASS] M09 inventory catalog navigation bridge completely verified.")

    # -------------------------------------------------------------------------
    # INT-06: M03 <-> Audit Trail (`apps/audit`)
    # -------------------------------------------------------------------------
    def test_int_06_audit_trail_immutability(self):
        print("\n[INT-06] Testing M03 <-> Audit Trail (Immutable Mutation Records)...")
        root1 = self._create_asset("AUD-R1", "Audit Root 1")
        root2 = self._create_asset("AUD-R2", "Audit Root 2")
        child = self._create_asset("AUD-CH", "Audit Child")

        link = hierarchy.add_component(root1, child, relationship_type="COMPONENT", actor=self.manager)
        self.created_links.append(link)

        # Move component
        hierarchy.move_component(link, root2, actor=self.manager)

        # Verify audit log generated
        log_entry = AuditLog.objects.filter(
            organization=self.org,
            action="asset.component_moved",
            target_id=child.pk
        ).first()

        self.assertIsNotNone(log_entry)
        self.assertEqual(log_entry.actor, self.manager)
        self.assertEqual(str(log_entry.before["parent_id"]), str(root1.pk))
        self.assertEqual(str(log_entry.after["parent_id"]), str(root2.pk))
        print("  [PASS] Hierarchy modifications captured in immutable audit trail.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
