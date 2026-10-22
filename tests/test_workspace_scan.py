from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, Client
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assets.models import Asset, AssetCategory
from apps.identification import services as id_services
from apps.rbac import services as rbac
from apps.sites.models import Site
from apps.tenancy import services as tenancy
from apps.tenancy.models import Membership
from apps.workorders.models import WorkOrder
from apps.workorders import services as wo_services
from apps.workspace import services as ws_services


class WorkspaceScanAndAssetVerificationTests(TestCase):
    """
    Test suite for PRD Section 3.3 & Step 6: Quick Scan QR/Barcode Launcher and Machine Verification.
    Validates mobile scanner access from cockpit, in-job machine tag verification,
    token resolver auto-job discovery, and the REST API verification endpoint.
    """

    def setUp(self):
        # 1. Tenant & Admin
        self.admin = User.objects.create_user(
            email="admin@scan.test",
            password="TestPassword123!",
            full_name="Admin Scan",
            is_platform_admin=True,
        )
        self.org, self.owner_m = tenancy.create_organization(
            name="Apex Mobile Fleet",
            owner_email="ops@scan.test",
            owner_name="Fleet Operations",
            actor=self.admin,
        )

        # 2. Site & Assets
        self.site = Site.objects.create(
            organization=self.org,
            code="FAC-SCAN-01",
            name="Main Utility Facility",
        )
        self.category = AssetCategory.objects.create(
            organization=self.org,
            name="Pumping Units",
        )
        self.asset_target = Asset.objects.create(
            organization=self.org,
            site=self.site,
            category=self.category,
            asset_tag="AST-PMP-101",
            name="Primary Industrial Feed Pump 101",
        )
        self.asset_other = Asset.objects.create(
            organization=self.org,
            site=self.site,
            category=self.category,
            asset_tag="AST-PMP-999",
            name="Secondary Auxiliary Pump 999",
        )

        # 3. QR & Barcode Identifiers
        self.qr_target = id_services.generate_identifier(self.asset_target, kind="QR", actor=self.admin)
        self.qr_other = id_services.generate_identifier(self.asset_other, kind="QR", actor=self.admin)

        # 4. Users & Roles
        planner_user = User.objects.create_user(
            email="planner@scan.test",
            password="TestPassword123!",
            full_name="Paul Planner",
        )
        self.planner = Membership.objects.create(
            organization=self.org,
            user=planner_user,
            status=Membership.Status.ACTIVE,
        )
        planner_role = rbac.system_role_by_key(self.org, "maintenance_planner")
        rbac.set_membership_roles(self.planner, [planner_role], actor=None, system=True)

        tech_user = User.objects.create_user(
            email="tech@scan.test",
            password="TestPassword123!",
            full_name="Tara Tech",
        )
        self.technician = Membership.objects.create(
            organization=self.org,
            user=tech_user,
            status=Membership.Status.ACTIVE,
        )
        tech_role = rbac.system_role_by_key(self.org, "technician")
        rbac.set_membership_roles(self.technician, [tech_role], actor=None, system=True)

        other_user = User.objects.create_user(
            email="other.tech@scan.test",
            password="TestPassword123!",
            full_name="Other Field Worker",
        )
        self.other_technician = Membership.objects.create(
            organization=self.org,
            user=other_user,
            status=Membership.Status.ACTIVE,
        )
        rbac.set_membership_roles(self.other_technician, [tech_role], actor=None, system=True)

        # 5. Work Order assigned to technician
        self.work_order = wo_services.create_work_order(
            self.org,
            site_id=self.site.pk,
            asset=self.asset_target,
            actor=self.planner.user,
            title="Inspect Impeller on Feed Pump 101",
            work_type="PREVENTIVE",
            priority=WorkOrder.Priority.HIGH,
            membership=self.planner,
        )
        t_start = timezone.now() + timedelta(hours=1)
        self.work_order = wo_services.plan_work_order(
            self.work_order,
            membership=self.planner,
            actor=self.planner.user,
            planned_start=t_start,
            planned_end=t_start + timedelta(hours=3),
            estimated_hours=Decimal("2.00"),
            due_at=t_start + timedelta(days=2),
        )
        self.work_order = wo_services.assign_work_order(
            self.work_order,
            membership=self.planner,
            actor=self.planner.user,
            assignee_id=self.technician.pk,
            scheduled_start=t_start,
            scheduled_end=t_start + timedelta(hours=3),
        )
        self.work_order = wo_services.dispatch_work_order(
            self.work_order,
            membership=self.planner,
            actor=self.planner.user,
        )

        # Clients
        self.ui_client = Client(headers={"host": "localhost"})
        self.ui_client.force_login(self.technician.user)
        session = self.ui_client.session
        session["active_org_id"] = str(self.org.pk)
        session.save()

        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.technician.user)
        self.api_client.credentials(HTTP_X_ORGANIZATION=str(self.org.pk))

    def test_cockpit_my_jobs_contains_scan_qr_button(self):
        """Technician cockpit header exposes the prominent Scan Asset QR launcher button."""
        resp = self.ui_client.get("/app/workspace/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="btnScanQR"')
        self.assertContains(resp, 'href="/app/scan/"')
        self.assertContains(resp, "Scan Asset QR")

    def test_job_detail_contains_verify_machine_scan_button(self):
        """Job detail screen provides direct verification button linking to scanner with work_order ID."""
        resp = self.ui_client.get(f"/app/workspace/jobs/{self.work_order.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="btnVerifyAsset"')
        self.assertContains(resp, f'href="/app/scan/?work_order={self.work_order.pk}"')
        self.assertContains(resp, "Verify Machine (Scan)")

    def test_scan_page_shows_work_order_verification_banner(self):
        """Scanner UI shows machine verification context when work_order query parameter is present."""
        resp = self.ui_client.get(f"/app/scan/?work_order={self.work_order.pk}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Machine Verification Active")
        self.assertContains(resp, f'value="{self.work_order.pk}"')
        self.assertContains(resp, f'href="/app/workspace/jobs/{self.work_order.pk}/"')

    def test_scan_page_manual_post_preserves_work_order(self):
        """Submitting code manually redirects to resolver with work_order query parameter."""
        resp = self.ui_client.post(
            "/app/scan/",
            {"token": self.qr_target.token, "work_order": str(self.work_order.pk)},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], f"/app/s/{self.qr_target.token}/?work_order={self.work_order.pk}")

    def test_resolve_view_verifies_matching_asset(self):
        """Resolver validates scanned asset matches work order and redirects to job detail with success message."""
        url = f"/app/s/{self.qr_target.token}/?work_order={self.work_order.pk}"
        resp = self.ui_client.get(url, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertRedirects(resp, f"/app/workspace/jobs/{self.work_order.pk}/")
        self.assertContains(resp, "Asset Verified: Machine Primary Industrial Feed Pump 101 (AST-PMP-101) matches this work order.")

    def test_resolve_view_flags_mismatched_asset(self):
        """Resolver warns technician if scanned asset does not match the target work order asset."""
        url = f"/app/s/{self.qr_other.token}/?work_order={self.work_order.pk}"
        resp = self.ui_client.get(url, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertRedirects(resp, f"/app/workspace/jobs/{self.work_order.pk}/")
        self.assertContains(resp, "Mismatch Warning! Scanned asset AST-PMP-999")

    def test_resolve_view_auto_detects_technician_active_job(self):
        """General scan without work_order parameter detects technician's active job and opens it."""
        url = f"/app/s/{self.qr_target.token}/"
        resp = self.ui_client.get(url, follow=True)
        self.assertEqual(resp.status_code, 200)
        # Should redirect directly to the technician's assigned work order
        self.assertRedirects(resp, f"/app/workspace/jobs/{self.work_order.pk}/")
        self.assertContains(resp, f"Found assigned work order {self.work_order.order_number}")

    def test_api_verify_asset_matching_token(self):
        """REST API verify-asset endpoint returns verified=True and matched=True for matching token."""
        url = f"/api/v1/workspace/jobs/{self.work_order.pk}/verify-asset/"
        resp = self.api_client.post(url, {"token": self.qr_target.token}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["verified"])
        self.assertTrue(resp.data["matched"])
        self.assertEqual(resp.data["expected_asset_tag"], "AST-PMP-101")
        self.assertEqual(resp.data["scanned_asset_tag"], "AST-PMP-101")
        self.assertIn("matches", resp.data["message"])

    def test_api_verify_asset_mismatched_token(self):
        """REST API verify-asset endpoint returns verified=True and matched=False for different machine."""
        url = f"/api/v1/workspace/jobs/{self.work_order.pk}/verify-asset/"
        resp = self.api_client.post(url, {"token": self.qr_other.token}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["verified"])
        self.assertFalse(resp.data["matched"])
        self.assertEqual(resp.data["expected_asset_tag"], "AST-PMP-101")
        self.assertEqual(resp.data["scanned_asset_tag"], "AST-PMP-999")
        self.assertIn("does NOT match", resp.data["message"])

    def test_api_verify_asset_by_tag(self):
        """REST API verify-asset endpoint supports direct asset_tag verification."""
        url = f"/api/v1/workspace/jobs/{self.work_order.pk}/verify-asset/"
        resp = self.api_client.post(url, {"asset_tag": "AST-PMP-101"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["matched"])

    def test_api_verify_asset_invalid_token(self):
        """REST API returns 400 when an invalid/unresolvable token is provided."""
        url = f"/api/v1/workspace/jobs/{self.work_order.pk}/verify-asset/"
        resp = self.api_client.post(url, {"token": "INVALID_TOKEN_999999"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(resp.data["verified"])

    def test_api_verify_asset_rbac_unassigned_technician(self):
        """Unassigned technician receives 403 Forbidden when attempting to verify asset for job."""
        client = APIClient()
        client.force_authenticate(user=self.other_technician.user)
        client.credentials(HTTP_X_ORGANIZATION=str(self.org.pk))

        url = f"/api/v1/workspace/jobs/{self.work_order.pk}/verify-asset/"
        resp = client.post(url, {"token": self.qr_target.token}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
