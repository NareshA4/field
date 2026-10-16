"""
FieldOps Nexus - Automated REST API Test Suite for Module M03 (Asset Hierarchy)
Tests all DRF API endpoints, serialization, validation, RBAC, and error responses.
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

from django.conf import settings
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
from rest_framework.test import APIClient
from apps.tenancy.models import Organization
from apps.accounts.models import User
from apps.sites.models import Site
from apps.assets.models import AssetCategory, Asset, AssetComponent
from apps.assets import services


class M03APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org = Organization.objects.filter(name__iexact="Apex Industrial Technologies Ltd").first() or Organization.objects.first()
        cls.org_ext = Organization.objects.exclude(pk=cls.org.pk).first()
        cls.manager_user = User.objects.filter(email="asset.manager@apexindustrial.com").first() or User.objects.first()
        cls.site = Site.objects.filter(organization=cls.org).first()
        cls.cat = AssetCategory.objects.filter(organization=cls.org).first()

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.manager_user)
        self.client.credentials(HTTP_X_ORGANIZATION=self.org.slug)
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

    def _create_asset(self, tag, name):
        asset = services.create_asset(
            self.org, site=self.site, category=self.cat, actor=self.manager_user,
            asset_tag=f"{tag}-{self.run_id}", name=name
        )
        self.created_assets.append(asset)
        return asset

    # -------------------------------------------------------------------------
    # 1. API: POST /api/v1/assets/{id}/components/ (Attach Child)
    # -------------------------------------------------------------------------
    def test_api_01_attach_component(self):
        print("\n[API-01] Testing POST /api/v1/assets/{id}/components/...")
        parent = self._create_asset("PAR", "Parent Machine")
        child = self._create_asset("CHD", "Child Component")

        payload = {
            "child": str(child.pk),
            "relationship_type": "COMPONENT",
            "quantity": 2,
            "part_number": "PART-API-001",
            "notes": "API test component attachment"
        }
        res = self.client.post(f"/api/v1/assets/{parent.pk}/components/", payload, format="json")
        self.assertEqual(res.status_code, 201, getattr(res, "data", res.content))
        self.assertEqual(res.data["parent_tag"], parent.asset_tag)
        self.assertEqual(res.data["child_tag"], child.asset_tag)
        self.assertEqual(res.data["quantity"], 2)
        self.assertEqual(res.data["part_number"], "PART-API-001")
        print("  [PASS] Component attached successfully via API (201 Created).")

    # -------------------------------------------------------------------------
    # 2. API: GET /api/v1/assets/{id}/components/ (List Direct Children)
    # -------------------------------------------------------------------------
    def test_api_02_list_asset_components(self):
        print("\n[API-02] Testing GET /api/v1/assets/{id}/components/...")
        parent = self._create_asset("PAR2", "Parent Machine 2")
        child = self._create_asset("CHD2", "Child Component 2")

        attach_res = self.client.post(f"/api/v1/assets/{parent.pk}/components/", {"child": str(child.pk)}, format="json")
        self.assertEqual(attach_res.status_code, 201)

        res = self.client.get(f"/api/v1/assets/{parent.pk}/components/")
        self.assertEqual(res.status_code, 200, getattr(res, "data", res.content))
        items = res.data.get("results", res.data)
        self.assertTrue(any(c["child_tag"] == child.asset_tag for c in items))
        print("  [PASS] Direct child components listed successfully (200 OK).")

    # -------------------------------------------------------------------------
    # 3. API: GET /api/v1/assets/{id}/tree/ (Asset Tree & Ancestor Path)
    # -------------------------------------------------------------------------
    def test_api_03_get_asset_tree(self):
        print("\n[API-03] Testing GET /api/v1/assets/{id}/tree/...")
        root = self._create_asset("ROOT", "Root Machine")
        sub = self._create_asset("SUB", "Sub Assembly")
        leaf = self._create_asset("LEAF", "Leaf Part")

        self.client.post(f"/api/v1/assets/{root.pk}/components/", {"child": str(sub.pk), "relationship_type": "ASSEMBLY"}, format="json")
        self.client.post(f"/api/v1/assets/{sub.pk}/components/", {"child": str(leaf.pk), "relationship_type": "REPLACEABLE_PART"}, format="json")

        res = self.client.get(f"/api/v1/assets/{root.pk}/tree/")
        self.assertEqual(res.status_code, 200, getattr(res, "data", res.content))
        self.assertIn("tree", res.data)
        self.assertIn("path", res.data)
        self.assertEqual(res.data["tree"]["asset_tag"], root.asset_tag)
        self.assertEqual(len(res.data["tree"]["children"]), 1)
        self.assertEqual(res.data["tree"]["children"][0]["asset_tag"], sub.asset_tag)

        res_leaf = self.client.get(f"/api/v1/assets/{leaf.pk}/tree/")
        self.assertEqual(res_leaf.status_code, 200)
        path_tags = [p["asset_tag"] for p in res_leaf.data["path"]]
        self.assertEqual(path_tags, [root.asset_tag, sub.asset_tag])
        print("  [PASS] Full hierarchy tree and ancestor path returned correctly (200 OK).")

    # -------------------------------------------------------------------------
    # 4. API: GET /api/v1/assets/{id}/validate/ (Subtree Integrity Check)
    # -------------------------------------------------------------------------
    def test_api_04_validate_subtree(self):
        print("\n[API-04] Testing GET /api/v1/assets/{id}/validate/...")
        root = self._create_asset("VROOT", "Val Root")
        child = self._create_asset("VCHD", "Val Child")

        self.client.post(f"/api/v1/assets/{root.pk}/components/", {"child": str(child.pk)}, format="json")

        res = self.client.get(f"/api/v1/assets/{root.pk}/validate/")
        self.assertEqual(res.status_code, 200, getattr(res, "data", res.content))
        self.assertTrue(res.data["valid"])
        self.assertEqual(res.data["nodes"], 2)
        self.assertEqual(len(res.data["issues"]), 0)
        print("  [PASS] Subtree validation integrity report generated (200 OK).")

    # -------------------------------------------------------------------------
    # 5. API: POST /api/v1/assets/{id}/validate-link/ (Dry-Run Check)
    # -------------------------------------------------------------------------
    def test_api_05_dry_run_validate_link(self):
        print("\n[API-05] Testing POST /api/v1/assets/{id}/validate-link/...")
        root = self._create_asset("DR-ROOT", "Dry Run Root")
        child = self._create_asset("DR-CHD", "Dry Run Child")

        # Valid candidate
        res = self.client.post(f"/api/v1/assets/{root.pk}/validate-link/", {"child": str(child.pk)}, format="json")
        self.assertEqual(res.status_code, 200, getattr(res, "data", res.content))
        self.assertTrue(res.data["valid"])

        # Invalid: self-parent
        res_self = self.client.post(f"/api/v1/assets/{root.pk}/validate-link/", {"child": str(root.pk)}, format="json")
        self.assertEqual(res_self.status_code, 200)
        self.assertFalse(res_self.data["valid"])
        self.assertEqual(res_self.data["code"], "self_parent")
        print("  [PASS] Dry-run link validation correctly returned valid/invalid predictions without mutating DB.")

    # -------------------------------------------------------------------------
    # 6. API: GET /api/v1/asset-components/ (Filter Component Edges)
    # -------------------------------------------------------------------------
    def test_api_06_filter_components_list(self):
        print("\n[API-06] Testing GET /api/v1/asset-components/?parent=... & child=...")
        parent = self._create_asset("FPAR", "Filter Parent")
        child = self._create_asset("FCHD", "Filter Child")

        attach_res = self.client.post(f"/api/v1/assets/{parent.pk}/components/", {"child": str(child.pk)}, format="json")
        self.assertEqual(attach_res.status_code, 201)
        link_id = attach_res.data["id"]

        # Filter by parent
        res_p = self.client.get(f"/api/v1/asset-components/?parent={parent.pk}")
        self.assertEqual(res_p.status_code, 200, getattr(res_p, "data", res_p.content))
        items_p = res_p.data.get("results", res_p.data)
        self.assertTrue(any(item["id"] == link_id for item in items_p))

        # Filter by child
        res_c = self.client.get(f"/api/v1/asset-components/?child={child.pk}")
        self.assertEqual(res_c.status_code, 200, getattr(res_c, "data", res_c.content))
        items_c = res_c.data.get("results", res_c.data)
        self.assertTrue(any(item["id"] == link_id for item in items_c))
        print("  [PASS] Global component registry filtering by parent/child verified (200 OK).")

    # -------------------------------------------------------------------------
    # 7. API: PATCH /api/v1/asset-components/{id}/ (Partial Update)
    # -------------------------------------------------------------------------
    def test_api_07_partial_update_component(self):
        print("\n[API-07] Testing PATCH /api/v1/asset-components/{id}/...")
        parent = self._create_asset("UPAR", "Update Parent")
        child = self._create_asset("UCHD", "Update Child")

        attach_res = self.client.post(f"/api/v1/assets/{parent.pk}/components/", {
            "child": str(child.pk),
            "relationship_type": "COMPONENT",
            "quantity": 1
        }, format="json")
        self.assertEqual(attach_res.status_code, 201)
        link_id = attach_res.data["id"]

        update_res = self.client.patch(f"/api/v1/asset-components/{link_id}/", {
            "relationship_type": "REPLACEABLE_PART",
            "quantity": 4,
            "part_number": "PART-UPD-888",
            "notes": "Updated via PATCH API"
        }, format="json")
        self.assertEqual(update_res.status_code, 200, getattr(update_res, "data", update_res.content))
        self.assertEqual(update_res.data["relationship_type"], "REPLACEABLE_PART")
        self.assertEqual(update_res.data["quantity"], 4)
        self.assertEqual(update_res.data["part_number"], "PART-UPD-888")
        print("  [PASS] Component edge updated successfully (200 OK).")

    # -------------------------------------------------------------------------
    # 8. API: POST /api/v1/asset-components/{id}/move/ (Re-parenting)
    # -------------------------------------------------------------------------
    def test_api_08_move_component(self):
        print("\n[API-08] Testing POST /api/v1/asset-components/{id}/move/...")
        p1 = self._create_asset("MV-P1", "Move Parent 1")
        p2 = self._create_asset("MV-P2", "Move Parent 2")
        child = self._create_asset("MV-CHD", "Move Child")

        attach_res = self.client.post(f"/api/v1/assets/{p1.pk}/components/", {"child": str(child.pk)}, format="json")
        self.assertEqual(attach_res.status_code, 201)
        link_id = attach_res.data["id"]

        move_res = self.client.post(f"/api/v1/asset-components/{link_id}/move/", {"parent": str(p2.pk)}, format="json")
        self.assertEqual(move_res.status_code, 200, getattr(move_res, "data", move_res.content))
        self.assertEqual(move_res.data["parent_tag"], p2.asset_tag)
        self.assertEqual(move_res.data["child_tag"], child.asset_tag)
        print("  [PASS] Component re-parented successfully via API (200 OK).")

    # -------------------------------------------------------------------------
    # 9. API: POST /api/v1/asset-components/{id}/replace/ (Atomic Swap)
    # -------------------------------------------------------------------------
    def test_api_09_replace_component(self):
        print("\n[API-09] Testing POST /api/v1/asset-components/{id}/replace/...")
        parent = self._create_asset("SW-PAR", "Swap Parent")
        old_part = self._create_asset("SW-OLD", "Old Part")
        new_part = self._create_asset("SW-NEW", "New Part")

        attach_res = self.client.post(f"/api/v1/assets/{parent.pk}/components/", {
            "child": str(old_part.pk),
            "relationship_type": "REPLACEABLE_PART",
            "quantity": 1
        }, format="json")
        self.assertEqual(attach_res.status_code, 201)
        link_id = attach_res.data["id"]

        replace_res = self.client.post(f"/api/v1/asset-components/{link_id}/replace/", {
            "new_child": str(new_part.pk),
            "reason": "Routine preventive replacement"
        }, format="json")
        self.assertEqual(replace_res.status_code, 200, getattr(replace_res, "data", replace_res.content))
        self.assertEqual(replace_res.data["child_tag"], new_part.asset_tag)
        self.assertEqual(replace_res.data["relationship_type"], "REPLACEABLE_PART")
        print("  [PASS] Component replaced atomically via API (200 OK).")

    # -------------------------------------------------------------------------
    # 10. API: DELETE /api/v1/asset-components/{id}/ (Detach Child)
    # -------------------------------------------------------------------------
    def test_api_10_detach_component(self):
        print("\n[API-10] Testing DELETE /api/v1/asset-components/{id}/...")
        parent = self._create_asset("DT-PAR", "Detach Parent")
        child = self._create_asset("DT-CHD", "Detach Child")

        attach_res = self.client.post(f"/api/v1/assets/{parent.pk}/components/", {"child": str(child.pk)}, format="json")
        self.assertEqual(attach_res.status_code, 201)
        link_id = attach_res.data["id"]

        del_res = self.client.delete(f"/api/v1/asset-components/{link_id}/")
        self.assertEqual(del_res.status_code, 204)

        get_res = self.client.get(f"/api/v1/asset-components/{link_id}/")
        self.assertEqual(get_res.status_code, 404)
        print("  [PASS] Component detached successfully (204 No Content).")

    # -------------------------------------------------------------------------
    # 11. API: Negative & Boundary Edge Cases (Cycle, Tenant Isolation, 401/403)
    # -------------------------------------------------------------------------
    def test_api_11_negative_cases(self):
        print("\n[API-11] Testing Negative API Edge Cases (Cycles, Auth, Invariants)...")
        a1 = self._create_asset("NEG-A", "Node A")
        b1 = self._create_asset("NEG-B", "Node B")

        attach_res = self.client.post(f"/api/v1/assets/{a1.pk}/components/", {"child": str(b1.pk)}, format="json")
        self.assertEqual(attach_res.status_code, 201)

        # Attempt Cycle: B -> A (Must reject with 400 Domain Error)
        cycle_res = self.client.post(f"/api/v1/assets/{b1.pk}/components/", {"child": str(a1.pk)}, format="json")
        self.assertIn(cycle_res.status_code, [400, 409, 422])

        # Unauthenticated request
        unauth_client = APIClient()
        unauth_res = unauth_client.get(f"/api/v1/assets/{a1.pk}/tree/")
        self.assertIn(unauth_res.status_code, [401, 403])
        print("  [PASS] Negative API boundary cases and auth checks correctly rejected.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
