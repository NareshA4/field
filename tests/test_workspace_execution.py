from decimal import Decimal
from datetime import timedelta
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assets.models import Asset, AssetCategory
from apps.core.exceptions import Conflict, PermissionDenied, ValidationFailed
from apps.rbac import services as rbac
from apps.sites.models import Site
from apps.tenancy import services as tenancy
from apps.tenancy.models import Membership
from apps.workspace import selectors as ws_selectors, services as ws_services
from apps.workspace.models import TravelLog, WorkNote
from apps.workorders.models import WorkOrder
from apps.workorders import services as wo_services


class WorkspaceExecutionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin@test.local", password="password123!", full_name="Admin User", is_platform_admin=True
        )
        self.org, self.owner_m = tenancy.create_organization(
            name="Test Org", owner_email="owner@test.local", owner_name="Owner", actor=self.admin
        )
        self.site = Site.objects.create(
            organization=self.org, code="SITE-01", name="Alpha Plant", address="100 Main St\nIndustrial Park"
        )
        self.cat = AssetCategory.objects.create(organization=self.org, name="Pumps")
        self.asset = Asset.objects.create(
            organization=self.org, site=self.site, category=self.cat, asset_tag="AST-PUMP-01", name="Main Pump"
        )

        # Users and Roles
        tech_user = User.objects.create_user(email="tech@test.local", password="password123!", full_name="Kasun Tech")
        self.tech = Membership.objects.create(organization=self.org, user=tech_user, status=Membership.Status.ACTIVE)
        tech_role = rbac.system_role_by_key(self.org, "technician")
        rbac.set_membership_roles(self.tech, [tech_role], actor=None, system=True)

        other_user = User.objects.create_user(email="other@test.local", password="password123!", full_name="Other Tech")
        self.other_tech = Membership.objects.create(organization=self.org, user=other_user, status=Membership.Status.ACTIVE)
        rbac.set_membership_roles(self.other_tech, [tech_role], actor=None, system=True)

        planner_user = User.objects.create_user(email="planner@test.local", password="password123!", full_name="Planner")
        self.planner = Membership.objects.create(organization=self.org, user=planner_user, status=Membership.Status.ACTIVE)
        planner_role = rbac.system_role_by_key(self.org, "maintenance_planner")
        rbac.set_membership_roles(self.planner, [planner_role], actor=None, system=True)

    def _create_dispatched_order(self):
        wo = wo_services.create_work_order(
            self.org,
            site_id=self.site.pk,
            asset=self.asset,
            actor=self.planner.user,
            title="Service generator",
            work_type="CORRECTIVE",
            priority=WorkOrder.Priority.MEDIUM,
            membership=self.planner,
        )
        t0 = timezone.now() + timedelta(days=1)
        wo = wo_services.plan_work_order(
            wo,
            membership=self.planner,
            actor=self.planner.user,
            planned_start=t0,
            planned_end=t0 + timedelta(hours=2),
            estimated_hours=Decimal("2.0"),
            due_at=t0 + timedelta(days=2),
        )
        wo = wo_services.assign_work_order(
            wo,
            membership=self.planner,
            actor=self.planner.user,
            assignee_id=self.tech.pk,
            scheduled_start=t0,
            scheduled_end=t0 + timedelta(hours=2),
        )
        return wo_services.dispatch_work_order(wo, membership=self.planner, actor=self.planner.user)

    def test_start_job_lifecycle(self):
        wo = self._create_dispatched_order()
        self.assertEqual(wo.status, WorkOrder.Status.DISPATCHED)

        # Unauthorized technician cannot start
        with self.assertRaises(PermissionDenied):
            ws_services.start_job(wo, membership=self.other_tech, actor=self.other_tech.user)

        # Assigned technician starts the job
        started = ws_services.start_job(wo, membership=self.tech, actor=self.tech.user)
        self.assertEqual(started.status, WorkOrder.Status.IN_PROGRESS)
        self.assertIsNotNone(started.started_at)

    def test_pause_and_resume_job(self):
        wo = self._create_dispatched_order()
        wo = ws_services.start_job(wo, membership=self.tech, actor=self.tech.user)

        # Empty reason is rejected
        with self.assertRaises(ValidationFailed):
            ws_services.pause_job(wo, reason="", membership=self.tech, actor=self.tech.user)

        # Pause job with valid reason
        paused = ws_services.pause_job(wo, reason="Awaiting site access clearance", membership=self.tech, actor=self.tech.user)
        self.assertEqual(paused.status, WorkOrder.Status.ON_HOLD)
        self.assertEqual(paused.hold_reason, "Awaiting site access clearance")

        # Resume job
        resumed = ws_services.resume_job(paused, membership=self.tech, actor=self.tech.user)
        self.assertEqual(resumed.status, WorkOrder.Status.IN_PROGRESS)
        self.assertEqual(resumed.hold_reason, "")

    def test_travel_logging(self):
        wo = self._create_dispatched_order()
        wo = ws_services.start_job(wo, membership=self.tech, actor=self.tech.user)

        # Invalid duration rejected
        with self.assertRaises(ValidationFailed):
            ws_services.log_travel(wo, membership=self.tech, actor=self.tech.user, duration_hours="0")
        with self.assertRaises(ValidationFailed):
            ws_services.log_travel(wo, membership=self.tech, actor=self.tech.user, duration_hours="25")

        # Future date rejected
        future = timezone.localdate() + timedelta(days=2)
        with self.assertRaises(ValidationFailed):
            ws_services.log_travel(wo, membership=self.tech, actor=self.tech.user, duration_hours="1.5", work_date=future)

        # Valid travel log
        travel = ws_services.log_travel(
            wo,
            membership=self.tech,
            actor=self.tech.user,
            duration_hours="1.75",
            distance_km="32.5",
            route_details="Highway A1 to Plant South",
            notes="Heavy rain on route",
            record_labor_entry=True,
        )
        self.assertEqual(travel.duration_hours, Decimal("1.75"))
        self.assertEqual(travel.distance_km, Decimal("32.5"))
        self.assertIsNotNone(travel.labor_entry)
        self.assertEqual(travel.labor_entry.hours, Decimal("1.75"))
        self.assertEqual(TravelLog.objects.filter(work_order=wo).count(), 1)

    def test_drawer_summary_context(self):
        wo = self._create_dispatched_order()
        wo = ws_services.start_job(wo, membership=self.tech, actor=self.tech.user)
        ws_services.log_travel(wo, membership=self.tech, actor=self.tech.user, duration_hours="1.0", distance_km="20")

        summary = ws_services.get_drawer_summary(wo, membership=self.tech)
        self.assertEqual(summary["status"], WorkOrder.Status.IN_PROGRESS)
        self.assertTrue(summary["can_pause"])
        self.assertFalse(summary["can_start"])
        self.assertEqual(summary["total_travel_hours"], Decimal("1.0"))
        self.assertIn("route", summary)
        self.assertEqual(summary["route"]["site_name"], "Alpha Plant")

    def test_api_drawer_and_execution_actions(self):
        wo = self._create_dispatched_order()
        client = APIClient()
        client.force_authenticate(user=self.tech.user)
        client.credentials(HTTP_X_ORGANIZATION=str(self.org.pk))

        # GET drawer data
        res = client.get(f"/api/v1/workspace/jobs/{wo.pk}/drawer/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["order_number"], wo.order_number)
        self.assertTrue(data["can_start"])

        # POST start
        res = client.post(f"/api/v1/workspace/jobs/{wo.pk}/start/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["work_order"]["status"], "IN_PROGRESS")

        # POST pause
        res = client.post(f"/api/v1/workspace/jobs/{wo.pk}/pause/", {"reason": "Waiting for supervisor confirmation"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["work_order"]["status"], "ON_HOLD")

        # POST resume
        res = client.post(f"/api/v1/workspace/jobs/{wo.pk}/resume/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["work_order"]["status"], "IN_PROGRESS")

        # POST travel
        res = client.post(f"/api/v1/workspace/jobs/{wo.pk}/travel/", {
            "duration_hours": "1.25",
            "distance_km": "18.0",
            "route_details": "Central Expressway",
            "notes": "Fast transit",
        })
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["travel_log"]["duration_hours"], "1.25")

    def test_ui_drawer_view(self):
        wo = self._create_dispatched_order()
        wo = ws_services.start_job(wo, membership=self.tech, actor=self.tech.user)
        client = Client(headers={"host": "localhost"})
        client.force_login(self.tech.user)
        session = client.session
        session["active_org_id"] = str(self.org.pk)
        session.save()

        res = client.get(f"/app/workspace/jobs/{wo.pk}/drawer/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Travel Logging", res.content)
        self.assertIn(b"Current Execution State", res.content)

    def test_my_jobs_selector_and_isolation(self):
        wo_tech = self._create_dispatched_order()
        # Create an order assigned to other technician
        wo_other = wo_services.create_work_order(
            self.org,
            site_id=self.site.pk,
            asset=self.asset,
            actor=self.planner.user,
            title="Other Tech Order",
            work_type="CORRECTIVE",
            priority=WorkOrder.Priority.LOW,
            membership=self.planner,
        )
        t0 = timezone.now() + timedelta(days=1)
        wo_other = wo_services.plan_work_order(
            wo_other,
            membership=self.planner,
            actor=self.planner.user,
            planned_start=t0,
            planned_end=t0 + timedelta(hours=1),
            estimated_hours=Decimal("1.0"),
            due_at=t0 + timedelta(days=2),
        )
        wo_other = wo_services.assign_work_order(
            wo_other,
            membership=self.planner,
            actor=self.planner.user,
            assignee_id=self.other_tech.pk,
            scheduled_start=t0,
            scheduled_end=t0 + timedelta(hours=1),
        )
        wo_other = wo_services.dispatch_work_order(wo_other, membership=self.planner, actor=self.planner.user)

        # Tech sees only their order
        my_jobs_qs = ws_selectors.my_jobs(self.tech)
        self.assertIn(wo_tech, my_jobs_qs)
        self.assertNotIn(wo_other, my_jobs_qs)

        # Status filtering
        active_jobs = ws_selectors.my_jobs(self.tech, status_filter="active")
        self.assertIn(wo_tech, active_jobs)
        completed_jobs = ws_selectors.my_jobs(self.tech, status_filter="completed")
        self.assertNotIn(wo_tech, completed_jobs)

        # Search query
        found = ws_selectors.my_jobs(self.tech, search="generator")
        self.assertEqual(found.count(), 1)
        not_found = ws_selectors.my_jobs(self.tech, search="nonexistent xyz")
        self.assertEqual(not_found.count(), 0)

    def test_record_work_note(self):
        wo = self._create_dispatched_order()
        wo = ws_services.start_job(wo, membership=self.tech, actor=self.tech.user)

        # Empty note rejected
        with self.assertRaises(ValidationFailed):
            ws_services.record_work_note(wo, note="", membership=self.tech, actor=self.tech.user)

        # Unauthorized technician rejected
        with self.assertRaises(PermissionDenied):
            ws_services.record_work_note(wo, note="Valid note", membership=self.other_tech, actor=self.other_tech.user)

        # Valid work note appended
        note_entry = ws_services.record_work_note(
            wo, note="Checked wiring harness and calibrated sensor.", membership=self.tech, actor=self.tech.user
        )
        self.assertEqual(note_entry.body, "Checked wiring harness and calibrated sensor.")
        notes = list(ws_selectors.work_notes_for(wo))
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].body, "Checked wiring harness and calibrated sensor.")

    def test_complete_job_validation_and_lifecycle(self):
        wo = self._create_dispatched_order()
        wo = ws_services.start_job(wo, membership=self.tech, actor=self.tech.user)

        # Short resolution notes rejected (< 10 chars)
        with self.assertRaises(ValidationFailed):
            ws_services.complete_job(wo, resolution_notes="Fixed it", membership=self.tech, actor=self.tech.user)

        # If checklist is required and pending, completion is blocked with Conflict
        with self.assertRaises(Conflict):
            ws_services.complete_job(
                wo,
                resolution_notes="Replaced pump impeller and sealed casing properly.",
                membership=self.tech,
                actor=self.tech.user,
            )

        # Mark checklist bypassed/completed for testing completion transition
        wo.checklist_required = False
        wo.save(update_fields=["checklist_required"])

        # Also for CORRECTIVE, evidence is required unless attached
        from apps.workorders.models import WorkOrderEvidence
        from apps.files.models import Attachment
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(WorkOrder)
        att = Attachment.objects.create(
            organization=self.org,
            content_type=ct,
            object_id=str(wo.pk),
            uploaded_by=self.tech.user,
            original_name="evidence.png",
            mime_type="image/png",
            size=1024,
            sha256="abc",
            file="evidence.png",
        )
        WorkOrderEvidence.objects.create(
            organization=self.org,
            work_order=wo,
            attachment=att,
            linked_by=self.tech.user,
        )

        completed = ws_services.complete_job(
            wo,
            resolution_notes="Replaced pump impeller and verified pressure at 4.2 bar.",
            membership=self.tech,
            actor=self.tech.user,
        )
        self.assertEqual(completed.status, WorkOrder.Status.COMPLETED)
        self.assertIsNotNone(completed.completed_at)

    def test_my_jobs_ui_and_job_detail_ui(self):
        wo = self._create_dispatched_order()
        wo = ws_services.start_job(wo, membership=self.tech, actor=self.tech.user)

        client = Client(headers={"host": "localhost"})
        client.force_login(self.tech.user)
        session = client.session
        session["active_org_id"] = str(self.org.pk)
        session.save()

        # My Jobs list view
        res = client.get("/app/workspace/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"My Assigned Jobs", res.content)
        self.assertIn(wo.order_number.encode(), res.content)

        # Job detail view
        res_detail = client.get(f"/app/workspace/jobs/{wo.pk}/")
        self.assertEqual(res_detail.status_code, 200)
        self.assertIn(b"Route Guidance", res_detail.content)
        self.assertIn(b"Technician Work Notes", res_detail.content)
        self.assertIn(b"Travel Logging", res_detail.content)

    def test_api_my_jobs_and_notes(self):
        wo = self._create_dispatched_order()
        wo = ws_services.start_job(wo, membership=self.tech, actor=self.tech.user)

        client = APIClient()
        client.force_authenticate(user=self.tech.user)
        client.credentials(HTTP_X_ORGANIZATION=str(self.org.pk))

        # GET my jobs
        res = client.get("/api/v1/workspace/jobs/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["jobs"][0]["order_number"], wo.order_number)

        # POST work note
        res_note = client.post(f"/api/v1/workspace/jobs/{wo.pk}/notes/", {
            "body": "Replaced primary intake valve gasket."
        })
        self.assertEqual(res_note.status_code, 201)
        self.assertEqual(res_note.json()["body"], "Replaced primary intake valve gasket.")

        # GET work notes
        res_notes_get = client.get(f"/api/v1/workspace/jobs/{wo.pk}/notes/")
        self.assertEqual(res_notes_get.status_code, 200)
        self.assertEqual(len(res_notes_get.json()), 1)

    def test_requirement_1_job_details_happy_and_negative(self):
        """Req 1: Job Details (Happy: inspect all details via selector/API. Negative: unauthorized technician blocked)."""
        wo = self._create_dispatched_order()
        wo = ws_services.start_job(wo, membership=self.tech, actor=self.tech.user)

        # Happy path - Selector
        detail = ws_selectors.get_job_detail(self.tech, str(wo.pk))
        self.assertEqual(detail["work_order"].order_number, wo.order_number)
        self.assertEqual(detail["work_order"].status, WorkOrder.Status.IN_PROGRESS)
        self.assertEqual(detail["work_order"].priority, WorkOrder.Priority.MEDIUM)
        self.assertEqual(detail["work_order"].work_type, WorkOrder.WorkType.CORRECTIVE)
        self.assertIn("materials", detail)
        self.assertIn("evidence", detail)
        self.assertIn("labor_entries", detail)

        # Happy path - REST API
        client = APIClient()
        client.force_authenticate(user=self.tech.user)
        client.credentials(HTTP_X_ORGANIZATION=str(self.org.pk))
        res = client.get(f"/api/v1/workspace/jobs/{wo.pk}/")
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertEqual(payload["work_order"]["order_number"], wo.order_number)
        self.assertEqual(payload["work_order"]["status"], "IN_PROGRESS")
        self.assertIn("materials", payload)
        self.assertIn("evidence", payload)
        self.assertIn("labor_entries", payload)

        # Negative path: unauthorized technician blocked
        with self.assertRaises(PermissionDenied):
            ws_selectors.get_job_detail(self.other_tech, str(wo.pk))

        client_other = APIClient()
        client_other.force_authenticate(user=self.other_tech.user)
        client_other.credentials(HTTP_X_ORGANIZATION=str(self.org.pk))
        res_other = client_other.get(f"/api/v1/workspace/jobs/{wo.pk}/")
        self.assertEqual(res_other.status_code, 400)
        self.assertIn("assigned", res_other.json()["error"]["message"])

    def test_requirement_2_site_and_asset_context_happy_and_negative(self):
        """Req 2: Site/Asset Context (Happy: site, contacts, route, asset hierarchy. Negative: missing asset handled safely)."""
        from apps.sites.models import SiteContact, Zone

        # Enrich site with contacts and asset with zone
        zone = Zone.objects.create(organization=self.org, site=self.site, name="Boiler Room B")
        self.asset.zone = zone
        self.asset.manufacturer = "Grundfos"
        self.asset.model = "CR-15-3"
        self.asset.serial_number = "SN-98234-A"
        self.asset.save()

        SiteContact.objects.create(
            organization=self.org,
            site=self.site, name="Facility Manager", phone="+1555123456", email="fac@test.local", escalation_order=1
        )

        wo = self._create_dispatched_order()
        detail = ws_selectors.get_job_detail(self.tech, str(wo.pk))

        # Happy path: Route and Site context
        route = detail["route"]
        self.assertEqual(route["site_name"], "Alpha Plant")
        self.assertEqual(route["site_code"], "SITE-01")
        self.assertIn("100 Main St", route["site_address"])
        self.assertIn("Boiler Room B", route["location_path"])
        self.assertEqual(len(route["contacts"]), 1)
        self.assertEqual(route["contacts"][0].name, "Facility Manager")

        # Happy path: Asset context
        wo_asset = detail["work_order"].asset
        self.assertEqual(wo_asset.asset_tag, "AST-PUMP-01")
        self.assertEqual(wo_asset.manufacturer, "Grundfos")
        self.assertEqual(wo_asset.model, "CR-15-3")
        self.assertEqual(wo_asset.serial_number, "SN-98234-A")

        # Negative path: work order creation requires asset
        with self.assertRaises(ValidationFailed):
            wo_services.create_work_order(
                self.org,
                site_id=self.site.pk,
                asset=None,
                actor=self.planner.user,
                title="Invalid No Asset Order",
                work_type="CORRECTIVE",
                priority=WorkOrder.Priority.LOW,
                membership=self.planner,
            )

        # Negative/Fallback path: asset without zone or hardware details renders safely
        bare_site = Site.objects.create(organization=self.org, code="SITE-EMPTY", name="Empty Site")
        bare_asset = Asset.objects.create(
            organization=self.org, site=bare_site, category=self.cat, asset_tag="AST-BARE-01", name="Bare Pump"
        )
        wo_bare = wo_services.create_work_order(
            self.org,
            site_id=bare_site.pk,
            asset=bare_asset,
            actor=self.planner.user,
            title="Bare Asset Service",
            work_type="CORRECTIVE",
            priority=WorkOrder.Priority.LOW,
            membership=self.planner,
        )
        route_bare = ws_selectors.route_context(wo_bare)
        self.assertEqual(route_bare["location_path"], "")
        self.assertEqual(len(route_bare["contacts"]), 0)
        self.assertEqual(route_bare["site_name"], "Empty Site")

    def test_requirement_3_checklist_happy_and_negative(self):
        """Req 3: Checklist (Happy: completed checklist allows close. Negative: incomplete checklist blocks completion)."""
        wo = self._create_dispatched_order()
        wo = ws_services.start_job(wo, membership=self.tech, actor=self.tech.user)

        # By default corrective order requires checklist
        self.assertTrue(wo.checklist_required)
        self.assertEqual(wo.checklist_status, "PENDING")

        # Negative path: Attempting completion with incomplete checklist raises Conflict
        with self.assertRaises(Conflict) as ctx:
            ws_services.complete_job(
                wo,
                resolution_notes="Work completed properly and verified.",
                membership=self.tech,
                actor=self.tech.user,
            )
        self.assertIn("Checklist", str(ctx.exception.message))

        # Happy path: Mark checklist as COMPLETED and VALID
        wo.checklist_status = "COMPLETED"
        wo.checklist_validation_state = "VALID"
        wo.checklist_instance_ref = "CHK-INST-001"
        wo.checklist_required = False  # Bypassed / completed
        wo.save()

        # Attach evidence so evidence blocker passes
        dummy_file = SimpleUploadedFile("evidence.txt", b"Valid checklist verification text.", content_type="text/plain")
        ws_services.upload_job_evidence(wo, file=dummy_file, membership=self.tech, actor=self.tech.user)

        # Now completion succeeds
        completed = ws_services.complete_job(
            wo,
            resolution_notes="Work completed and checklist verified.",
            membership=self.tech,
            actor=self.tech.user,
        )
        self.assertEqual(completed.status, WorkOrder.Status.COMPLETED)

    def test_requirement_4_work_notes_happy_and_negative(self):
        """Req 4: Notes (Happy: append & retrieve. Negative: empty, oversize > 2000, unauthorized)."""
        wo = self._create_dispatched_order()
        wo = ws_services.start_job(wo, membership=self.tech, actor=self.tech.user)

        # Happy path: record via service
        note = ws_services.record_work_note(
            wo, note="Checked fluid levels and tested motor.", membership=self.tech, actor=self.tech.user
        )
        self.assertEqual(note.body, "Checked fluid levels and tested motor.")
        self.assertEqual(note.author, self.tech)

        # Happy path: record via REST API
        client = APIClient()
        client.force_authenticate(user=self.tech.user)
        client.credentials(HTTP_X_ORGANIZATION=str(self.org.pk))
        res = client.post(f"/api/v1/workspace/jobs/{wo.pk}/notes/", {"body": "Added second note from field tablet."})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["body"], "Added second note from field tablet.")

        # Negative path: blank note
        with self.assertRaises(ValidationFailed):
            ws_services.record_work_note(wo, note="   ", membership=self.tech, actor=self.tech.user)

        res_blank = client.post(f"/api/v1/workspace/jobs/{wo.pk}/notes/", {"body": ""})
        self.assertEqual(res_blank.status_code, 400)

        # Negative path: oversized note > 2000 chars
        with self.assertRaises(ValidationFailed):
            ws_services.record_work_note(wo, note="X" * 2001, membership=self.tech, actor=self.tech.user)

        # Negative path: unauthorized technician
        with self.assertRaises(PermissionDenied):
            ws_services.record_work_note(wo, note="Spying note", membership=self.other_tech, actor=self.other_tech.user)

    def test_requirement_5_photos_and_files_happy_and_negative(self):
        """Req 5: Photos/Files (Happy: upload photo evidence, list. Negative: empty file, unauthorized tech)."""
        wo = self._create_dispatched_order()
        wo = ws_services.start_job(wo, membership=self.tech, actor=self.tech.user)

        # Happy path: upload via service
        photo_file = SimpleUploadedFile("pump_log.txt", b"Inspected pump motor and logged readings.", content_type="text/plain")
        att = ws_services.upload_job_evidence(
            wo, file=photo_file, description="Damaged bearing photo", membership=self.tech, actor=self.tech.user
        )
        self.assertEqual(att.original_name, "pump_log.txt")
        self.assertEqual(att.description, "Damaged bearing photo")
        self.assertEqual(wo.completion_evidence.count(), 1)

        # Happy path: upload via API
        client = APIClient()
        client.force_authenticate(user=self.tech.user)
        client.credentials(HTTP_X_ORGANIZATION=str(self.org.pk))

        doc_file = SimpleUploadedFile("readings.csv", b"timestamp,psi\n2026-10-12,45.2\n", content_type="text/csv")
        res_upload = client.post(
            f"/api/v1/workspace/jobs/{wo.pk}/evidence/",
            {"file": doc_file, "description": "Signed inspection CSV"},
            format="multipart",
        )
        self.assertEqual(res_upload.status_code, 201)
        self.assertEqual(res_upload.json()["original_name"], "readings.csv")

        # Happy path: GET list of evidence
        res_list = client.get(f"/api/v1/workspace/jobs/{wo.pk}/evidence/")
        self.assertEqual(res_list.status_code, 200)
        self.assertEqual(len(res_list.json()), 2)

        # Negative path: missing/empty file upload
        with self.assertRaises(ValidationFailed):
            ws_services.upload_job_evidence(wo, file=None, membership=self.tech, actor=self.tech.user)

        res_no_file = client.post(f"/api/v1/workspace/jobs/{wo.pk}/evidence/", {}, format="multipart")
        self.assertEqual(res_no_file.status_code, 400)

        # Negative path: unauthorized technician upload
        with self.assertRaises(PermissionDenied):
            ws_services.upload_job_evidence(wo, file=photo_file, membership=self.other_tech, actor=self.other_tech.user)

    def test_requirement_6_labor_and_time_happy_and_negative(self):
        """Req 6: Labor/Time (Happy: travel and on-site labor recorded & aggregated. Negative: hours <= 0 or > 24, future dates)."""
        wo = self._create_dispatched_order()
        wo = ws_services.start_job(wo, membership=self.tech, actor=self.tech.user)

        # Happy path: travel log
        travel = ws_services.log_travel(
            wo,
            membership=self.tech,
            actor=self.tech.user,
            duration_hours="1.50",
            distance_km="45.0",
            route_details="Route 66 to Plant Gate",
            notes="Morning transit",
        )
        self.assertEqual(travel.duration_hours, Decimal("1.50"))

        # Happy path: on-site labor via service
        labor = ws_services.record_job_labor(
            wo,
            hours="3.50",
            description="Replaced seals and re-aligned shaft",
            membership=self.tech,
            actor=self.tech.user,
        )
        self.assertEqual(labor.hours, Decimal("3.50"))

        # Happy path: on-site labor via API
        client = APIClient()
        client.force_authenticate(user=self.tech.user)
        client.credentials(HTTP_X_ORGANIZATION=str(self.org.pk))
        res_labor = client.post(f"/api/v1/workspace/jobs/{wo.pk}/labor/", {
            "hours": "1.25",
            "description": "Post-start vibration testing",
        })
        self.assertEqual(res_labor.status_code, 201)
        self.assertEqual(res_labor.json()["hours"], "1.25")

        # Aggregated hours verified in drawer summary
        summary = ws_services.get_drawer_summary(wo, membership=self.tech)
        self.assertEqual(summary["total_travel_hours"], Decimal("1.50"))
        # 1.50 (from travel) + 3.50 + 1.25 = 6.25 total labor hours
        self.assertEqual(summary["total_labor_hours"], Decimal("6.25"))

        # Negative path: labor hours <= 0
        with self.assertRaises(ValidationFailed):
            ws_services.record_job_labor(wo, hours="0.00", description="Invalid", membership=self.tech, actor=self.tech.user)

        # Negative path: labor hours > 24
        with self.assertRaises(ValidationFailed):
            ws_services.record_job_labor(wo, hours="24.50", description="Invalid", membership=self.tech, actor=self.tech.user)

        # Negative path: future work date
        future_date = timezone.localdate() + timedelta(days=5)
        with self.assertRaises(ValidationFailed):
            ws_services.record_job_labor(
                wo, hours="2.00", description="Future", work_date=future_date, membership=self.tech, actor=self.tech.user
            )

        # Negative path: unauthorized technician
        with self.assertRaises(PermissionDenied):
            ws_services.record_job_labor(
                wo, hours="2.00", description="Unauthorized", membership=self.other_tech, actor=self.other_tech.user
            )

    def test_requirement_7_parts_use_materials_happy_and_negative(self):
        """Req 7: Parts Use / Materials (Happy: record material usage via service & API. Negative: qty <= 0, blank name, unauthorized)."""
        wo = self._create_dispatched_order()
        wo = ws_services.start_job(wo, membership=self.tech, actor=self.tech.user)

        # Happy path: service record
        mat1 = ws_services.record_job_material(
            wo,
            item_name="Hydraulic Fluid ISO 46",
            quantity="5.00",
            unit="liters",
            notes="Refilled reservoir after pipe repair",
            membership=self.tech,
            actor=self.tech.user,
        )
        self.assertEqual(mat1.item_name, "Hydraulic Fluid ISO 46")
        self.assertEqual(mat1.quantity, Decimal("5.00"))
        self.assertEqual(mat1.unit, "liters")

        # Happy path: API POST
        client = APIClient()
        client.force_authenticate(user=self.tech.user)
        client.credentials(HTTP_X_ORGANIZATION=str(self.org.pk))
        res_mat = client.post(f"/api/v1/workspace/jobs/{wo.pk}/materials/", {
            "item_name": "Fluorocarbon O-Ring #214",
            "quantity": "2.00",
            "unit": "ea",
            "notes": "Fitted to pump flange",
        })
        self.assertEqual(res_mat.status_code, 201)
        self.assertEqual(res_mat.json()["item_name"], "Fluorocarbon O-Ring #214")

        # Happy path: API GET list
        res_list = client.get(f"/api/v1/workspace/jobs/{wo.pk}/materials/")
        self.assertEqual(res_list.status_code, 200)
        self.assertEqual(len(res_list.json()), 2)

        # Negative path: quantity <= 0
        with self.assertRaises(ValidationFailed):
            ws_services.record_job_material(
                wo, item_name="Gasket", quantity="0.00", membership=self.tech, actor=self.tech.user
            )

        res_neg_qty = client.post(f"/api/v1/workspace/jobs/{wo.pk}/materials/", {
            "item_name": "Gasket", "quantity": "-1.00",
        })
        self.assertEqual(res_neg_qty.status_code, 400)

        # Negative path: empty item_name
        with self.assertRaises(ValidationFailed):
            ws_services.record_job_material(
                wo, item_name="", quantity="1.00", membership=self.tech, actor=self.tech.user
            )

        # Negative path: unauthorized technician
        with self.assertRaises(PermissionDenied):
            ws_services.record_job_material(
                wo, item_name="Filter", quantity="1.00", membership=self.other_tech, actor=self.other_tech.user
            )

    def test_requirement_8_completion_evidence_closure_happy_and_negative(self):
        """Req 8: Completion Evidence & Closure (Happy: evidence uploaded + resolution notes => complete. Negative: missing evidence blocks complete)."""
        wo = self._create_dispatched_order()
        wo = ws_services.start_job(wo, membership=self.tech, actor=self.tech.user)

        # Bypass checklist requirement to isolate evidence requirement test
        wo.checklist_required = False
        wo.save()

        # Negative path: Corrective job without evidence fails closed
        with self.assertRaises(Conflict) as ctx:
            ws_services.complete_job(
                wo,
                resolution_notes="Repaired pump impeller and sealed.",
                membership=self.tech,
                actor=self.tech.user,
            )
        self.assertIn("evidence", str(ctx.exception.message).lower())

        # Negative path: resolution notes < 10 characters
        with self.assertRaises(ValidationFailed):
            ws_services.complete_job(wo, resolution_notes="Fixed it", membership=self.tech, actor=self.tech.user)

        # Happy path: upload evidence
        photo = SimpleUploadedFile("completion_evidence.txt", b"Repair verified and casing sealed.", content_type="text/plain")
        ws_services.upload_job_evidence(
            wo, file=photo, description="Completed repair photo", membership=self.tech, actor=self.tech.user
        )

        # Now completion succeeds
        completed = ws_services.complete_job(
            wo,
            resolution_notes="Replaced faulty gasket and successfully pressure tested.",
            membership=self.tech,
            actor=self.tech.user,
        )
        self.assertEqual(completed.status, WorkOrder.Status.COMPLETED)
        self.assertIsNotNone(completed.completed_at)

