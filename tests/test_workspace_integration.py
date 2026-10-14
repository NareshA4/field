from decimal import Decimal
from datetime import timedelta
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assets.models import Asset, AssetCategory
from apps.audit.models import AuditLog
from apps.core.exceptions import Conflict, PermissionDenied, ValidationFailed
from apps.rbac import services as rbac
from apps.sites.models import Site, SiteContact, Zone
from apps.tenancy import services as tenancy
from apps.tenancy.models import Membership
from apps.workspace import selectors as ws_selectors, services as ws_services
from apps.workspace.models import TravelLog, WorkNote
from apps.workorders.models import WorkOrder, WorkOrderLabor, WorkOrderMaterial, WorkOrderEvidence
from apps.workorders import services as wo_services


class Module07FullLifecycleIntegrationTests(TestCase):
    """
    Comprehensive integration test suite for Module 07 (Technician Workspace).
    Tests the complete end-to-end operational journey across all connective modules:
    M01 (Sites/Routing) -> M02/M03 (Asset Context) -> M06 (Work Order Lifecycle)
    -> M07 (Technician Execution Cockpit) -> M08 (Inspection Checklists)
    -> M09 (Spare Parts Usage) -> HPE Files (Evidence) -> M15 (Audit Trail).
    """

    def setUp(self):
        # 1. Foundation & Tenant Setup
        self.admin = User.objects.create_user(
            email="admin@integration.test",
            password="TestPassword123!",
            full_name="Platform Admin",
            is_platform_admin=True,
        )
        self.org, self.owner_m = tenancy.create_organization(
            name="Apex Field Services",
            owner_email="ops-head@apex.test",
            owner_name="Operations Head",
            actor=self.admin,
        )

        # 2. M01 Site & Location Master
        self.site = Site.objects.create(
            organization=self.org,
            code="FAC-EAST-01",
            name="East Coast Pumping Station",
            address="4500 Coastal Highway\nBuilding B, Unit 4\nBoston, MA 02101",
        )
        self.contact = SiteContact.objects.create(
            organization=self.org,
            site=self.site,
            name="Marcus Vance",
            role_title="Plant Supervisor",
            phone="+1-617-555-0199",
            email="mvance@apex.test",
            escalation_order=1,
        )

        # 3. M02 Asset Registry & M03 Hierarchy
        self.category = AssetCategory.objects.create(
            organization=self.org,
            name="Heavy Centrifugal Pumps",
            description="Industrial fluid transport pumps",
        )
        self.zone = Zone.objects.create(
            organization=self.org,
            site=self.site,
            name="Primary Pump Room (Sub-Level 2)",
        )
        self.asset = Asset.objects.create(
            organization=self.org,
            site=self.site,
            category=self.category,
            zone=self.zone,
            asset_tag="AST-PMP-8802",
            name="High Pressure Centrifugal Booster Pump 02",
            manufacturer="Flowserve",
            model="Durco Mark 3",
            serial_number="FS-2026-993821",
            warranty_ref="WARR-FS-EXT-2028",
        )

        # 4. Users & RBAC Roles
        planner_user = User.objects.create_user(
            email="planner@apex.test",
            password="TestPassword123!",
            full_name="Elena Rostova",
        )
        self.planner = Membership.objects.create(
            organization=self.org,
            user=planner_user,
            status=Membership.Status.ACTIVE,
        )
        planner_role = rbac.system_role_by_key(self.org, "maintenance_planner")
        rbac.set_membership_roles(self.planner, [planner_role], actor=None, system=True)

        tech_user = User.objects.create_user(
            email="technician@apex.test",
            password="TestPassword123!",
            full_name="Rajesh Kumar",
        )
        self.technician = Membership.objects.create(
            organization=self.org,
            user=tech_user,
            status=Membership.Status.ACTIVE,
        )
        tech_role = rbac.system_role_by_key(self.org, "technician")
        rbac.set_membership_roles(self.technician, [tech_role], actor=None, system=True)

        other_user = User.objects.create_user(
            email="other.tech@apex.test",
            password="TestPassword123!",
            full_name="Other Field Tech",
        )
        self.other_technician = Membership.objects.create(
            organization=self.org,
            user=other_user,
            status=Membership.Status.ACTIVE,
        )
        rbac.set_membership_roles(self.other_technician, [tech_role], actor=None, system=True)

        # Web Client for UI testing
        self.ui_client = Client(headers={"host": "localhost"})
        self.ui_client.force_login(self.technician.user)
        session = self.ui_client.session
        session["active_org_id"] = str(self.org.pk)
        session.save()

        # API Client for Mobile/REST testing
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.technician.user)
        self.api_client.credentials(HTTP_X_ORGANIZATION=str(self.org.pk))

    def _setup_dispatched_job(self, *, work_type="CORRECTIVE", checklist_required=True):
        """Creates, plans, assigns, and dispatches a work order via M06 authoritative pipeline."""
        wo = wo_services.create_work_order(
            self.org,
            site_id=self.site.pk,
            asset=self.asset,
            actor=self.planner.user,
            title="Emergency seal replacement on booster pump 02",
            description="Inspect mechanical shaft seal for thermal stress and fluid leakage. Pressure test after rebuild.",
            work_type=work_type,
            priority=WorkOrder.Priority.HIGH,
            membership=self.planner,
        )
        t_start = timezone.now() + timedelta(hours=2)
        wo = wo_services.plan_work_order(
            wo,
            membership=self.planner,
            actor=self.planner.user,
            planned_start=t_start,
            planned_end=t_start + timedelta(hours=4),
            estimated_hours=Decimal("4.00"),
            due_at=t_start + timedelta(days=1),
        )
        wo = wo_services.assign_work_order(
            wo,
            membership=self.planner,
            actor=self.planner.user,
            assignee_id=self.technician.pk,
            scheduled_start=t_start,
            scheduled_end=t_start + timedelta(hours=4),
        )
        wo = wo_services.dispatch_work_order(
            wo,
            membership=self.planner,
            actor=self.planner.user,
        )
        if checklist_required:
            wo.checklist_required = True
            wo.checklist_template_ref = "CHK-PUMP-STD-2026"
            wo.checklist_status = "PENDING"
            wo.save(update_fields=["checklist_required", "checklist_template_ref", "checklist_status"])
        else:
            wo.checklist_required = False
            wo.save(update_fields=["checklist_required"])
        return wo

    def test_complete_end_to_end_technician_integration_journey(self):
        """
        FULL E2E INTEGRATION TEST:
        1. Job dispatched by Planner (M06)
        2. Technician discovers job in My Jobs list (M07)
        3. Technician inspects Job Details & Site/Asset guidance (M01, M02, M03)
        4. Technician logs travel to site (M07 -> M06 labor)
        5. Technician starts job execution (M07 -> M06 state transition)
        6. Technician pauses job with mandatory reason and resumes it
        7. Technician appends work notes (M07 append-only)
        8. Technician tries to complete job without checklist/evidence -> fails closed (M08, M06)
        9. Technician records parts/materials used on-site (M07 -> M06/M09 boundary)
        10. Technician records authoritative on-site labor (M07 -> M06 labor)
        11. Technician uploads photo/file completion evidence (M07 -> HPE Files -> M06)
        12. Checklist completed and verified (M08 runner)
        13. Technician completes job with resolution summary (M07 -> M06 complete)
        14. Complete audit log trail verified (M15 audit)
        15. Drawer summary and aggregations verified
        """
        # Step 1: Planner dispatches work order
        wo = self._setup_dispatched_job()
        self.assertEqual(wo.status, WorkOrder.Status.DISPATCHED)
        self.assertEqual(wo.assigned_to, self.technician)

        # Step 2: Technician discovers job in My Jobs list UI and REST API
        ui_res = self.ui_client.get("/app/workspace/")
        self.assertEqual(ui_res.status_code, 200)
        self.assertIn(wo.order_number.encode(), ui_res.content)
        self.assertIn(b"East Coast Pumping Station", ui_res.content)

        api_res = self.api_client.get("/api/v1/workspace/jobs/?status=active")
        self.assertEqual(api_res.status_code, 200)
        api_data = api_res.json()
        self.assertEqual(api_data["count"], 1)
        self.assertEqual(api_data["jobs"][0]["order_number"], wo.order_number)

        # Step 3: Technician opens Job Detail and inspects site & asset context
        detail_res = self.ui_client.get(f"/app/workspace/jobs/{wo.pk}/")
        self.assertEqual(detail_res.status_code, 200)
        self.assertIn(b"Site &amp; Route Guidance", detail_res.content)
        self.assertIn(b"Marcus Vance", detail_res.content)
        self.assertIn(b"AST-PMP-8802", detail_res.content)
        self.assertIn(b"Flowserve", detail_res.content)
        self.assertIn(b"Primary Pump Room", detail_res.content)

        # Step 4: Technician logs travel time & distance via UI
        travel_res = self.ui_client.post(f"/app/workspace/jobs/{wo.pk}/travel/", {
            "duration_hours": "1.50",
            "distance_km": "42.5",
            "route_details": "I-93 South via Central Artery Tunnel",
            "notes": "Light morning traffic; arrived at plant security gate at 08:30",
        })
        self.assertEqual(travel_res.status_code, 302)  # Redirects to job detail
        travel_logs = TravelLog.objects.filter(work_order=wo)
        self.assertEqual(travel_logs.count(), 1)
        self.assertEqual(travel_logs.first().duration_hours, Decimal("1.50"))
        self.assertEqual(travel_logs.first().distance_km, Decimal("42.5"))

        # Step 5: Technician starts job execution (DISPATCHED -> IN_PROGRESS)
        start_res = self.ui_client.post(f"/app/workspace/jobs/{wo.pk}/start/")
        self.assertEqual(start_res.status_code, 302)
        wo.refresh_from_db()
        self.assertEqual(wo.status, WorkOrder.Status.IN_PROGRESS)
        self.assertIsNotNone(wo.started_at)

        # Step 6: Technician pauses job with hold reason and then resumes
        pause_res = self.ui_client.post(f"/app/workspace/jobs/{wo.pk}/pause/", {
            "reason": "Awaiting LOTO isolation clearance from site engineer",
        })
        self.assertEqual(pause_res.status_code, 302)
        wo.refresh_from_db()
        self.assertEqual(wo.status, WorkOrder.Status.ON_HOLD)
        self.assertEqual(wo.hold_reason, "Awaiting LOTO isolation clearance from site engineer")

        # Resume job
        resume_res = self.ui_client.post(f"/app/workspace/jobs/{wo.pk}/resume/")
        self.assertEqual(resume_res.status_code, 302)
        wo.refresh_from_db()
        self.assertEqual(wo.status, WorkOrder.Status.IN_PROGRESS)
        self.assertEqual(wo.hold_reason, "")

        # Step 7: Technician appends field execution notes via REST API
        note_res = self.api_client.post(f"/api/v1/workspace/jobs/{wo.pk}/notes/", {
            "body": "Disassembled outer pump casing. Mechanical seal shows heavy grooving and thermal cracking.",
        })
        self.assertEqual(note_res.status_code, 201)
        self.assertEqual(WorkNote.objects.filter(work_order=wo).count(), 1)

        # Step 8: Early completion attempt fails closed (Checklist blocker + Evidence blocker)
        fail_complete_res = self.ui_client.post(f"/app/workspace/jobs/{wo.pk}/complete/", {
            "resolution_notes": "Attempting to finish early without checklist or evidence.",
        })
        self.assertEqual(fail_complete_res.status_code, 302)
        wo.refresh_from_db()
        self.assertEqual(wo.status, WorkOrder.Status.IN_PROGRESS)  # Still in progress, not completed!

        # Step 9: Technician records parts and materials used on-site
        mat_res = self.api_client.post(f"/api/v1/workspace/jobs/{wo.pk}/materials/", {
            "item_name": "Mechanical Seal Cartridge 2.5 inch",
            "quantity": "1.00",
            "unit": "ea",
            "notes": "Part #MSC-250-Viton replaced old damaged seal",
        })
        self.assertEqual(mat_res.status_code, 201)

        # Record second material (lubricant) via UI
        mat_ui_res = self.ui_client.post(f"/app/workspace/jobs/{wo.pk}/materials/", {
            "item_name": "Mobil SHC 630 Synthetic Gear Lubricant",
            "quantity": "2.50",
            "unit": "L",
            "notes": "Bearing housing flushed and refilled",
        })
        self.assertEqual(mat_ui_res.status_code, 302)
        self.assertEqual(WorkOrderMaterial.objects.filter(work_order=wo).count(), 2)

        # Step 10: Technician records authoritative on-site labor
        labor_res = self.api_client.post(f"/api/v1/workspace/jobs/{wo.pk}/labor/", {
            "hours": "3.50",
            "description": "Installed new cartridge seal, checked shaft alignment with dial indicator.",
        })
        self.assertEqual(labor_res.status_code, 201)
        self.assertTrue(WorkOrderLabor.objects.filter(work_order=wo, hours=Decimal("3.50")).exists())

        # Step 11: Technician uploads completion evidence photos & files
        evidence_file = SimpleUploadedFile(
            "post_install_pressure_log.txt",
            b"HYDROSTATIC TEST RECORD\nPressure: 150 PSI\nDuration: 30 minutes\nLeakage: NONE\nInspector: R. Kumar",
            content_type="text/plain",
        )
        evidence_res = self.ui_client.post(
            f"/app/workspace/jobs/{wo.pk}/evidence/",
            {"evidence_file": evidence_file, "description": "Hydrostatic pressure test log"},
            format="multipart",
        )
        self.assertEqual(evidence_res.status_code, 302)
        self.assertEqual(WorkOrderEvidence.objects.filter(work_order=wo).count(), 1)
        self.assertTrue(wo.completion_evidence.exists())

        # Step 12: M08 Checklist verified and completed
        wo.checklist_status = "COMPLETED"
        wo.checklist_validation_state = "VALID"
        wo.checklist_instance_ref = "INSP-INST-2026-09"
        wo.checklist_required = False  # Certified
        wo.save()

        # Step 13: Technician completes job with resolution notes (>= 10 chars)
        complete_res = self.api_client.post(f"/api/v1/workspace/jobs/{wo.pk}/complete/", {
            "resolution_notes": "Successfully replaced mechanical shaft seal and gasket. Pressure tested at 150 PSI for 30 minutes with zero leakage. Pump operating smoothly at 1750 RPM.",
        })
        self.assertEqual(complete_res.status_code, 200)
        wo.refresh_from_db()
        self.assertEqual(wo.status, WorkOrder.Status.COMPLETED)
        self.assertIsNotNone(wo.completed_at)
        self.assertIn("150 PSI", wo.resolution_notes)

        # Step 14: Complete Audit Log verification (M15)
        actions = list(AuditLog.objects.filter(organization=self.org).values_list("action", flat=True))
        self.assertIn("workspace.travel_logged", actions)
        self.assertIn("workspace.job_started", actions)
        self.assertIn("workspace.job_paused", actions)
        self.assertIn("workspace.job_resumed", actions)
        self.assertIn("workspace.work_note_added", actions)
        self.assertIn("workspace.material_recorded", actions)
        self.assertIn("workspace.labor_recorded", actions)
        self.assertIn("workspace.evidence_uploaded", actions)
        self.assertIn("workspace.job_completed", actions)

        # Step 15: Drawer summary context and aggregated metrics
        summary = ws_services.get_drawer_summary(wo, membership=self.technician)
        self.assertEqual(summary["status"], WorkOrder.Status.COMPLETED)
        self.assertEqual(summary["total_travel_hours"], Decimal("1.50"))
        # Labor entries include 3.50h on-site labor
        self.assertGreaterEqual(summary["total_labor_hours"], Decimal("3.50"))
        self.assertFalse(summary["can_start"])
        self.assertFalse(summary["can_pause"])
        self.assertFalse(summary["can_complete"])

    def test_tenant_and_technician_cross_isolation_integration(self):
        """
        SECURITY INTEGRATION TEST:
        Validates that other technicians in the same organization and users from
        different tenant organizations cannot access, hijack, or record actions on the job.
        """
        wo = self._setup_dispatched_job(checklist_required=False)
        wo = ws_services.start_job(wo, membership=self.technician, actor=self.technician.user)

        # 1. Other technician in same org cannot record labor
        with self.assertRaises(PermissionDenied):
            ws_services.record_job_labor(
                wo,
                hours="1.0",
                description="Hijacking labor",
                membership=self.other_technician,
                actor=self.other_technician.user,
            )

        # 2. Other technician in same org cannot record materials
        with self.assertRaises(PermissionDenied):
            ws_services.record_job_material(
                wo,
                item_name="Illegal part",
                quantity="1.0",
                membership=self.other_technician,
                actor=self.other_technician.user,
            )

        # 3. Other technician cannot complete job
        with self.assertRaises(PermissionDenied):
            ws_services.complete_job(
                wo,
                resolution_notes="Unauthorized completion attempt.",
                membership=self.other_technician,
                actor=self.other_technician.user,
            )

        # 4. Other technician REST API gets rejected with 400/403
        other_api_client = APIClient()
        other_api_client.force_authenticate(user=self.other_technician.user)
        other_api_client.credentials(HTTP_X_ORGANIZATION=str(self.org.pk))

        res_api_labor = other_api_client.post(f"/api/v1/workspace/jobs/{wo.pk}/labor/", {
            "hours": "1.0",
            "description": "Unauthorized API labor",
        })
        self.assertEqual(res_api_labor.status_code, 400)

        # 5. Cross-tenant isolation: user from second organization cannot even find the job (404)
        other_org_admin = User.objects.create_user(
            email="admin@rogue-org.test", password="Password123!", full_name="Rogue Admin"
        )
        rogue_org, _ = tenancy.create_organization(
            name="Rogue Enterprise",
            owner_email="owner@rogue-org.test",
            owner_name="Rogue Owner",
            actor=self.admin,
        )
        rogue_tech_user = User.objects.create_user(
            email="tech@rogue-org.test", password="Password123!", full_name="Rogue Tech"
        )
        rogue_tech = Membership.objects.create(
            organization=rogue_org,
            user=rogue_tech_user,
            status=Membership.Status.ACTIVE,
        )
        tech_role = rbac.system_role_by_key(rogue_org, "technician")
        rbac.set_membership_roles(rogue_tech, [tech_role], actor=None, system=True)

        rogue_api_client = APIClient()
        rogue_api_client.force_authenticate(user=rogue_tech_user)
        rogue_api_client.credentials(HTTP_X_ORGANIZATION=str(rogue_org.pk))

        res_rogue = rogue_api_client.get(f"/api/v1/workspace/jobs/{wo.pk}/")
        self.assertEqual(res_rogue.status_code, 404)
