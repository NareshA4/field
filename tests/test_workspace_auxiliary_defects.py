from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assets.models import Asset, AssetCategory
from apps.core.exceptions import Conflict, PermissionDenied, ValidationFailed
from apps.incidents.models import ServiceRequest
from apps.rbac import services as rbac
from apps.sites.models import Site
from apps.tenancy import services as tenancy
from apps.tenancy.models import Membership
from apps.workorders.models import WorkOrder
from apps.workorders import services as wo_services
from apps.workspace import selectors, services as ws_services


class WorkspaceAuxiliaryDefectTests(TestCase):
    """
    Test suite for PRD Section 3.3 & Add-On 5:
    Technician "Report Auxiliary Defect / Breakdown" On-Site Shortcut.
    Validates on-site defect capture, asset selection, duplicate handling,
    work order cross-linking, UI modal rendering, and REST API.
    """

    def setUp(self):
        # 1. Organization & Admin
        self.admin = User.objects.create_user(
            email="admin@defects.test",
            password="TestPassword123!",
            full_name="Admin Defects",
            is_platform_admin=True,
        )
        self.org, self.owner_m = tenancy.create_organization(
            name="Apex Field Utilities",
            owner_email="ops@defects.test",
            owner_name="Operations Lead",
            actor=self.admin,
        )

        # Other organization for tenant isolation tests
        self.other_org, _ = tenancy.create_organization(
            name="Rival Utilities Corp",
            owner_email="ops@rival.test",
            owner_name="Rival Lead",
            actor=self.admin,
        )

        # 2. Site & Assets
        self.site = Site.objects.create(
            organization=self.org,
            code="FAC-DEF-01",
            name="Hydro Power Substation A",
        )
        self.category = AssetCategory.objects.create(
            organization=self.org,
            name="Compressors",
        )
        # Primary job asset
        self.asset_primary = Asset.objects.create(
            organization=self.org,
            site=self.site,
            category=self.category,
            asset_tag="AST-CMP-201",
            name="Main Air Compressor Unit",
        )
        # Adjacent auxiliary asset at the same site
        self.asset_auxiliary = Asset.objects.create(
            organization=self.org,
            site=self.site,
            category=self.category,
            asset_tag="AST-PMP-202",
            name="Auxiliary Oil Recirculation Pump",
        )
        # Asset belonging to another tenant
        self.other_site = Site.objects.create(
            organization=self.other_org,
            code="FAC-RIV-01",
            name="Rival Station",
        )
        self.other_category = AssetCategory.objects.create(
            organization=self.other_org,
            name="Rival Turbines",
        )
        self.other_asset = Asset.objects.create(
            organization=self.other_org,
            site=self.other_site,
            category=self.other_category,
            asset_tag="AST-RIV-999",
            name="Cross Tenant Generator",
        )

        # 3. Roles and Memberships
        planner_user = User.objects.create_user(
            email="planner@defects.test",
            password="TestPassword123!",
            full_name="Peter Planner",
        )
        self.planner = Membership.objects.create(
            organization=self.org,
            user=planner_user,
            status=Membership.Status.ACTIVE,
        )
        planner_role = rbac.system_role_by_key(self.org, "maintenance_planner")
        rbac.set_membership_roles(self.planner, [planner_role], actor=None, system=True)

        tech_user = User.objects.create_user(
            email="tech@defects.test",
            password="TestPassword123!",
            full_name="Tony Technician",
        )
        self.technician = Membership.objects.create(
            organization=self.org,
            user=tech_user,
            status=Membership.Status.ACTIVE,
        )
        tech_role = rbac.system_role_by_key(self.org, "technician")
        rbac.set_membership_roles(self.technician, [tech_role], actor=None, system=True)

        unassigned_user = User.objects.create_user(
            email="unassigned.tech@defects.test",
            password="TestPassword123!",
            full_name="Unassigned Tech",
        )
        self.unassigned_technician = Membership.objects.create(
            organization=self.org,
            user=unassigned_user,
            status=Membership.Status.ACTIVE,
        )
        rbac.set_membership_roles(self.unassigned_technician, [tech_role], actor=None, system=True)

        # 4. Work Order assigned to technician
        self.work_order = wo_services.create_work_order(
            self.org,
            site_id=self.site.pk,
            asset=self.asset_primary,
            actor=self.planner.user,
            title="Perform 500-Hour Service on Compressor 201",
            work_type="PREVENTIVE",
            priority=WorkOrder.Priority.HIGH,
            membership=self.planner,
        )
        t_start = timezone.now()
        self.work_order = wo_services.plan_work_order(
            self.work_order,
            membership=self.planner,
            actor=self.planner.user,
            planned_start=t_start,
            planned_end=t_start + timedelta(hours=3),
            estimated_hours=Decimal("3.00"),
            due_at=t_start + timedelta(days=1),
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
        ws_services.start_job(self.work_order, membership=self.technician, actor=self.technician.user)
        self.work_order.refresh_from_db()

        # 5. UI Client
        self.ui_client = Client(headers={"host": "localhost"})
        self.ui_client.force_login(self.technician.user)
        session = self.ui_client.session
        session["active_org_id"] = str(self.org.pk)
        session.save()

        # 6. API Client
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.technician.user)
        self.api_client.credentials(HTTP_X_ORGANIZATION_ID=str(self.org.pk))

    def test_report_auxiliary_defect_service_success(self):
        """Technician reports an auxiliary defect on the primary asset; creates ServiceRequest and WorkNote."""
        sr = ws_services.report_auxiliary_defect(
            self.work_order,
            title="Hydraulic leak at discharge valve flange",
            description="Active dripping observed during compressor run-up.",
            severity="HIGH",
            service_impact="DEGRADED",
            failure_category="Hydraulic",
            membership=self.technician,
            actor=self.technician.user,
        )

        self.assertIsNotNone(sr)
        self.assertTrue(sr.number.startswith("INC") or sr.number.startswith("SR"))
        self.assertEqual(sr.status, "NEW")
        self.assertEqual(sr.severity, "HIGH")
        self.assertEqual(sr.service_impact, "DEGRADED")
        self.assertEqual(sr.asset_id, self.asset_primary.pk)
        self.assertEqual(sr.site_id, self.site.pk)
        self.assertEqual(sr.reported_by_id, self.technician.pk)
        self.assertIn(f"[Discovered during on-site execution of {self.work_order.order_number}]", sr.description)

        # Cross-linked WorkNote verified
        notes = list(ws_services.selectors.work_notes_for(self.work_order))
        self.assertTrue(any(sr.number in n.body for n in notes))

    def test_report_auxiliary_defect_different_asset_at_same_site(self):
        """Technician reports a defect on an adjacent auxiliary asset installed at the same facility."""
        sr = ws_services.report_auxiliary_defect(
            self.work_order,
            asset=self.asset_auxiliary,
            title="Loud bearing screech on recirc pump",
            description="High mechanical noise detected near oil skid.",
            severity="CRITICAL",
            service_impact="PARTIAL_OUTAGE",
            failure_category="Mechanical",
            membership=self.technician,
            actor=self.technician.user,
        )

        self.assertEqual(sr.asset_id, self.asset_auxiliary.pk)
        self.assertEqual(sr.site_id, self.site.pk)
        self.assertEqual(sr.severity, "CRITICAL")

        # Note references auxiliary asset tag
        notes = list(ws_services.selectors.work_notes_for(self.work_order))
        self.assertTrue(any(self.asset_auxiliary.asset_tag in n.body for n in notes))

    def test_report_auxiliary_defect_cross_tenant_asset_rejected(self):
        """Attempting to report a defect for an asset belonging to another tenant raises ValidationFailed."""
        with self.assertRaises(ValidationFailed):
            ws_services.report_auxiliary_defect(
                self.work_order,
                asset=self.other_asset,
                title="Cross tenant defect attempt",
                membership=self.technician,
                actor=self.technician.user,
            )

    def test_report_auxiliary_defect_unauthorized_technician_rejected(self):
        """A technician not assigned to the job cannot report a defect from this work order context."""
        with self.assertRaises(PermissionDenied):
            ws_services.report_auxiliary_defect(
                self.work_order,
                title="Unauthorized report attempt",
                membership=self.unassigned_technician,
                actor=self.unassigned_technician.user,
            )

    def test_report_auxiliary_defect_duplicate_detection(self):
        """Submitting an identical defect on the same asset triggers DuplicateIncidentError."""
        desc = "Severe oil spray from secondary coupling seal."
        ws_services.report_auxiliary_defect(
            self.work_order,
            title="Oil spray from secondary coupling",
            description=desc,
            membership=self.technician,
            actor=self.technician.user,
        )

        with self.assertRaises(Conflict):
            ws_services.report_auxiliary_defect(
                self.work_order,
                title="Oil spray from secondary coupling",
                description=desc,
                membership=self.technician,
                actor=self.technician.user,
                override_duplicate=False,
            )

    def test_report_auxiliary_defect_duplicate_override(self):
        """Technician can override duplicate detection by supplying override_duplicate and a reason."""
        desc = "Severe oil spray from secondary coupling seal."
        ws_services.report_auxiliary_defect(
            self.work_order,
            title="Oil spray from secondary coupling",
            description=desc,
            membership=self.technician,
            actor=self.technician.user,
        )

        sr_second = ws_services.report_auxiliary_defect(
            self.work_order,
            title="Oil spray from secondary coupling",
            description=desc,
            override_duplicate=True,
            override_reason="Different seal on opposite side of shaft",
            membership=self.technician,
            actor=self.technician.user,
        )
        self.assertIsNotNone(sr_second)

    def test_ui_job_detail_renders_report_defect_button_and_modal(self):
        """GET workspace:job_detail renders the Report Defect button, modal, and site assets."""
        url = reverse("workspace:job_detail", kwargs={"pk": self.work_order.pk})
        response = self.ui_client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn("btnReportDefect", content)
        self.assertIn("Report Defect", content)
        self.assertIn("reportAuxiliaryDefectModal", content)
        self.assertIn("Report Auxiliary Defect / Breakdown", content)
        # Verifies site assets are present in select options
        self.assertIn(self.asset_primary.asset_tag, content)
        self.assertIn(self.asset_auxiliary.asset_tag, content)

    def test_ui_job_detail_renders_auxiliary_defects_list(self):
        """Once reported, the on-site defect appears in the job detail auxiliary defects card."""
        sr = ws_services.report_auxiliary_defect(
            self.work_order,
            title="Vibration on cooling fin mount",
            description="Fin mount loose.",
            membership=self.technician,
            actor=self.technician.user,
        )

        url = reverse("workspace:job_detail", kwargs={"pk": self.work_order.pk})
        response = self.ui_client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn("auxiliaryDefectsSection", content)
        self.assertIn(sr.number, content)
        self.assertIn("Vibration on cooling fin mount", content)

    def test_ui_post_report_defect_success(self):
        """POST to workspace:report_defect creates the incident and redirects with success message."""
        url = reverse("workspace:report_defect", kwargs={"pk": self.work_order.pk})
        post_data = {
            "title": "Loose electrical terminal on sensor junction box",
            "asset_id": str(self.asset_auxiliary.pk),
            "kind": "INCIDENT",
            "severity": "MEDIUM",
            "service_impact": "NONE",
            "description": "Terminal screws loosened due to vibration.",
            "failure_category": "Electrical",
        }
        response = self.ui_client.post(url, data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Auxiliary defect", content)
        self.assertIn("reported successfully", content)

        # Verified in DB
        sr = ServiceRequest.objects.filter(title="Loose electrical terminal on sensor junction box").first()
        self.assertIsNotNone(sr)
        self.assertEqual(sr.asset_id, self.asset_auxiliary.pk)

    def test_api_post_report_defect_success(self):
        """POST /api/v1/workspace/jobs/<pk>/defects/ creates defect and returns 201 Created JSON."""
        url = f"/api/v1/workspace/jobs/{self.work_order.pk}/defects/"
        payload = {
            "title": "Auxiliary cooling radiator pinhole corrosion leak",
            "asset_id": str(self.asset_auxiliary.pk),
            "kind": "INCIDENT",
            "severity": "HIGH",
            "service_impact": "DEGRADED",
            "description": "Trace glycol coolant leaking onto drip tray.",
            "failure_category": "Piping",
        }
        response = self.api_client.post(url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertIn("id", data)
        self.assertIn("number", data)
        self.assertEqual(data["severity"], "HIGH")
        self.assertEqual(data["asset_tag"], self.asset_auxiliary.asset_tag)
        self.assertEqual(data["work_order_order_number"], self.work_order.order_number)

    def test_api_post_report_defect_duplicate_conflict_409(self):
        """POST duplicate defect without override returns 409 Conflict with duplicates list."""
        url = f"/api/v1/workspace/jobs/{self.work_order.pk}/defects/"
        payload = {
            "title": "Persistent valve weep at packing gland",
            "asset_id": str(self.asset_primary.pk),
            "description": "Heavy packing weep detected on gland packing.",
            "severity": "MEDIUM",
        }
        # First submission
        r1 = self.api_client.post(url, data=payload, format="json")
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED)

        # Duplicate submission without override
        r2 = self.api_client.post(url, data=payload, format="json")
        self.assertEqual(r2.status_code, status.HTTP_409_CONFLICT)
        data = r2.json()
        self.assertEqual(data["error"]["code"], "duplicate_incident_detected")
        self.assertTrue(len(data["error"]["duplicates"]) > 0)

    def test_api_post_report_defect_validation_error(self):
        """POST defect with title shorter than 3 characters returns 400 Bad Request."""
        url = f"/api/v1/workspace/jobs/{self.work_order.pk}/defects/"
        payload = {
            "title": "AB",  # < 3 chars
            "asset_id": str(self.asset_primary.pk),
        }
        response = self.api_client.post(url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
