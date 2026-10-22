from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, Client
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assets.models import Asset, AssetCategory, AssetMeter, AssetMeterReading
from apps.assets import services as asset_services
from apps.audit.models import AuditLog
from apps.core.exceptions import Conflict, PermissionDenied, ValidationFailed
from apps.rbac import services as rbac
from apps.sites.models import Site
from apps.tenancy import services as tenancy
from apps.tenancy.models import Membership
from apps.workspace import selectors as ws_selectors, services as ws_services
from apps.workorders.models import WorkOrder
from apps.workorders import services as wo_services


class WorkspaceMeterTrackingTests(TestCase):
    """
    Test suite for PRD Section 3.3: Equipment Meter & Running Hours Logging in Technician Workspace.
    Validates selectors, business logic, RBAC, domain rules (monotonicity, timestamps),
    HTMX/UI drawer rendering, and REST API endpoints.
    """

    def setUp(self):
        # 1. Organization & Admin
        self.admin = User.objects.create_user(
            email="admin@meters.test",
            password="TestPassword123!",
            full_name="Platform Admin",
            is_platform_admin=True,
        )
        self.org, self.owner_m = tenancy.create_organization(
            name="Apex Meter Fleet",
            owner_email="ops@meters.test",
            owner_name="Fleet Manager",
            actor=self.admin,
        )

        # 2. Site & Asset
        self.site = Site.objects.create(
            organization=self.org,
            code="FAC-METER-01",
            name="Boston Industrial Plant",
        )
        self.category = AssetCategory.objects.create(
            organization=self.org,
            name="Turbines & Compressors",
        )
        self.asset = Asset.objects.create(
            organization=self.org,
            site=self.site,
            category=self.category,
            asset_tag="AST-TRB-101",
            name="Gas Turbine 01",
            manufacturer="GE",
            model="Frame 7F",
            serial_number="GE-TRB-7001",
        )

        # 3. Roles & Users
        planner_user = User.objects.create_user(
            email="planner@meters.test",
            password="TestPassword123!",
            full_name="Jane Planner",
        )
        self.planner = Membership.objects.create(
            organization=self.org,
            user=planner_user,
            status=Membership.Status.ACTIVE,
        )
        planner_role = rbac.system_role_by_key(self.org, "maintenance_planner")
        rbac.set_membership_roles(self.planner, [planner_role], actor=None, system=True)

        tech_user = User.objects.create_user(
            email="tech@meters.test",
            password="TestPassword123!",
            full_name="Alex Tech",
        )
        self.technician = Membership.objects.create(
            organization=self.org,
            user=tech_user,
            status=Membership.Status.ACTIVE,
        )
        tech_role = rbac.system_role_by_key(self.org, "technician")
        rbac.set_membership_roles(self.technician, [tech_role], actor=None, system=True)

        other_user = User.objects.create_user(
            email="other.tech@meters.test",
            password="TestPassword123!",
            full_name="Other Tech",
        )
        self.other_technician = Membership.objects.create(
            organization=self.org,
            user=other_user,
            status=Membership.Status.ACTIVE,
        )
        rbac.set_membership_roles(self.other_technician, [tech_role], actor=None, system=True)

        # 4. Work Order (assigned to technician and started)
        self.work_order = wo_services.create_work_order(
            self.org,
            site_id=self.site.pk,
            asset=self.asset,
            actor=self.planner.user,
            title="Inspect Turbine Operating Hours and Odometer",
            work_type="PREVENTIVE",
            priority=WorkOrder.Priority.MEDIUM,
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
        self.work_order = ws_services.start_job(
            self.work_order,
            membership=self.technician,
            actor=self.technician.user,
        )

        # 5. Asset Meters
        self.meter_hours = asset_services.create_meter(
            self.asset,
            name="Operating Hours",
            unit="hrs",
            actor=self.admin,
        )
        self.meter_cycles = asset_services.create_meter(
            self.asset,
            name="Start Cycles",
            unit="cycles",
            actor=self.admin,
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

    def test_meters_for_work_order_without_asset(self):
        """A work order without an asset returns an empty meters list."""
        wo_no_asset = WorkOrder(organization=self.org, asset=None)
        meters = ws_selectors.meters_for(wo_no_asset)
        self.assertEqual(meters, [])

    def test_meters_for_includes_active_and_excludes_inactive(self):
        """Active meters are returned with latest readings, inactive meters are omitted."""
        # Record an initial baseline reading
        asset_services.record_reading(
            self.meter_hours,
            value=Decimal("1200.50"),
            actor=self.admin,
            notes="Baseline factory test",
        )

        # Deactivate meter_cycles
        asset_services.set_meter_active(self.meter_cycles, active=False, actor=self.admin)

        meters = ws_selectors.meters_for(self.work_order)
        self.assertEqual(len(meters), 1)
        m = meters[0]
        self.assertEqual(m["id"], str(self.meter_hours.pk))
        self.assertEqual(m["name"], "Operating Hours")
        self.assertEqual(m["unit"], "hrs")
        self.assertEqual(m["latest_value"], Decimal("1200.50"))
        self.assertEqual(m["latest_notes"], "Baseline factory test")
        self.assertEqual(m["readings_count"], 1)

        # Verify get_job_detail returns has_meters True
        detail = ws_selectors.get_job_detail(self.technician, str(self.work_order.pk))
        self.assertTrue(detail["has_meters"])
        self.assertEqual(len(detail["meters"]), 1)

        # Verify get_drawer_summary returns meters
        drawer = ws_services.get_drawer_summary(self.work_order, membership=self.technician)
        self.assertTrue(drawer["has_meters"])
        self.assertEqual(drawer["meters_count"], 1)

    def test_record_job_meter_reading_service_happy_path(self):
        """Technician logs a meter reading from the workspace service."""
        reading = ws_services.record_job_meter_reading(
            self.work_order,
            meter_id=str(self.meter_hours.pk),
            value=Decimal("1250.75"),
            notes="Turbine running smoothly on site.",
            membership=self.technician,
            actor=self.technician.user,
        )

        self.assertIsInstance(reading, AssetMeterReading)
        self.assertEqual(reading.meter, self.meter_hours)
        self.assertEqual(reading.value, Decimal("1250.75"))
        self.assertEqual(reading.source, "technician_workspace")
        self.assertEqual(reading.recorded_by, self.technician.user)
        self.assertEqual(reading.notes, "Turbine running smoothly on site.")

        # Assert audit trail recorded
        audit_entry = AuditLog.objects.filter(
            action="workspace.meter_reading_recorded",
            target_id=str(self.work_order.pk),
        ).first()
        self.assertIsNotNone(audit_entry)
        self.assertEqual(audit_entry.metadata["meter_name"], "Operating Hours")
        self.assertEqual(audit_entry.metadata["value"], "1250.75")

        # Verify updated selector reflect the new latest value
        meters = ws_selectors.meters_for(self.work_order)
        hours_m = next(m for m in meters if m["id"] == str(self.meter_hours.pk))
        self.assertEqual(hours_m["latest_value"], Decimal("1250.75"))

    def test_record_job_meter_reading_monotonicity_rejection(self):
        """Logging a meter value lower than previous reading raises ValidationFailed."""
        ws_services.record_job_meter_reading(
            self.work_order,
            meter_id=str(self.meter_hours.pk),
            value=Decimal("1500.00"),
            membership=self.technician,
            actor=self.technician.user,
        )

        # Attempt to log a lower reading
        with self.assertRaises(ValidationFailed) as ctx:
            ws_services.record_job_meter_reading(
                self.work_order,
                meter_id=str(self.meter_hours.pk),
                value=Decimal("1490.00"),
                membership=self.technician,
                actor=self.technician.user,
            )
        self.assertEqual(ctx.exception.code, "meter_not_monotonic")

    def test_record_job_meter_reading_negative_value_rejection(self):
        """Negative meter reading values are rejected."""
        with self.assertRaises(ValidationFailed) as ctx:
            ws_services.record_job_meter_reading(
                self.work_order,
                meter_id=str(self.meter_hours.pk),
                value=Decimal("-5.00"),
                membership=self.technician,
                actor=self.technician.user,
            )
        self.assertEqual(ctx.exception.code, "invalid_reading")

    def test_record_job_meter_reading_future_timestamp_rejection(self):
        """Readings with future timestamps (> 5 minutes ahead) are rejected."""
        future_time = timezone.now() + timedelta(hours=2)
        with self.assertRaises(ValidationFailed) as ctx:
            ws_services.record_job_meter_reading(
                self.work_order,
                meter_id=str(self.meter_hours.pk),
                value=Decimal("2000.00"),
                read_at=future_time,
                membership=self.technician,
                actor=self.technician.user,
            )
        self.assertEqual(ctx.exception.code, "reading_in_future")

    def test_record_job_meter_reading_inactive_meter_rejection(self):
        """Recording a reading for an inactive meter raises Conflict."""
        asset_services.set_meter_active(self.meter_cycles, active=False, actor=self.admin)
        with self.assertRaises(Conflict) as ctx:
            ws_services.record_job_meter_reading(
                self.work_order,
                meter_id=str(self.meter_cycles.pk),
                value=Decimal("50"),
                membership=self.technician,
                actor=self.technician.user,
            )
        self.assertEqual(ctx.exception.code, "meter_inactive")

    def test_record_job_meter_reading_rbac_unassigned_technician_rejection(self):
        """An unassigned technician is denied permission to record meter readings."""
        with self.assertRaises(PermissionDenied):
            ws_services.record_job_meter_reading(
                self.work_order,
                meter_id=str(self.meter_hours.pk),
                value=Decimal("1300.00"),
                membership=self.other_technician,
                actor=self.other_technician.user,
            )

    def test_record_job_meter_reading_meter_from_different_asset_rejection(self):
        """Attempting to log a reading for a meter belonging to a different asset fails."""
        other_asset = Asset.objects.create(
            organization=self.org,
            site=self.site,
            category=self.category,
            asset_tag="AST-PMP-999",
            name="Centrifugal Water Pump",
        )
        foreign_meter = asset_services.create_meter(
            other_asset,
            name="Pump Hours",
            unit="hrs",
            actor=self.admin,
        )

        with self.assertRaises(ValidationFailed) as ctx:
            ws_services.record_job_meter_reading(
                self.work_order,
                meter_id=str(foreign_meter.pk),
                value=Decimal("100.00"),
                membership=self.technician,
                actor=self.technician.user,
            )
        self.assertEqual(ctx.exception.code, "meter_not_found")
        self.assertIn("Meter not found on this asset", ctx.exception.message)

    def test_ui_post_meter_reading_redirect(self):
        """Standard web form submission records reading and redirects to job_detail."""
        url = f"/app/workspace/jobs/{self.work_order.pk}/meters/reading/"
        resp = self.ui_client.post(
            url,
            {"meter_id": str(self.meter_hours.pk), "value": "1110.50", "notes": "Shift 1 reading"},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Meter reading recorded: 1110.50 hrs for Operating Hours.")

        # Verify database record
        reading = AssetMeterReading.objects.filter(meter=self.meter_hours).first()
        self.assertIsNotNone(reading)
        self.assertEqual(reading.value, Decimal("1110.50"))

    def test_ui_post_meter_reading_htmx(self):
        """HTMX submission records reading and renders updated execution drawer."""
        url = f"/app/workspace/jobs/{self.work_order.pk}/meters/reading/"
        resp = self.ui_client.post(
            url,
            {"meter_id": str(self.meter_hours.pk), "value": "1150.00", "notes": "HTMX drawer update"},
            headers={"HX-Request": "true"},
        )
        self.assertEqual(resp.status_code, 200)
        # Should render the drawer template
        self.assertContains(resp, "id=\"jobDrawer\"")
        self.assertContains(resp, "1150.00 hrs")

    def test_api_get_meters_list(self):
        """REST API GET returns active meters and their latest readings."""
        asset_services.record_reading(
            self.meter_hours,
            value=Decimal("1400.00"),
            actor=self.admin,
        )

        url = f"/api/v1/workspace/jobs/{self.work_order.pk}/meters/reading/"
        resp = self.api_client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 2)
        hours_item = next(m for m in resp.data if m["id"] == str(self.meter_hours.pk))
        self.assertEqual(hours_item["name"], "Operating Hours")
        self.assertEqual(hours_item["unit"], "hrs")
        self.assertEqual(Decimal(hours_item["latest_value"]), Decimal("1400.00"))

    def test_api_post_meter_reading_success(self):
        """REST API POST successfully records a reading with 201 Created."""
        url = f"/api/v1/workspace/jobs/{self.work_order.pk}/meters/reading/"
        payload = {
            "meter_id": str(self.meter_hours.pk),
            "value": "1450.25",
            "notes": "Logged via Mobile REST API",
        }
        resp = self.api_client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["meter_name"], "Operating Hours")
        self.assertEqual(Decimal(resp.data["value"]), Decimal("1450.25"))
        self.assertEqual(resp.data["unit"], "hrs")
        self.assertEqual(resp.data["notes"], "Logged via Mobile REST API")

        # Verify DB
        last_reading = self.meter_hours.readings.order_by("-read_at").first()
        self.assertEqual(last_reading.value, Decimal("1450.25"))
        self.assertEqual(last_reading.source, "technician_workspace")

    def test_api_post_meter_reading_non_monotonic_error(self):
        """REST API POST returns 400 Bad Request when reading is non-monotonic."""
        asset_services.record_reading(
            self.meter_hours,
            value=Decimal("2000.00"),
            actor=self.admin,
        )

        url = f"/api/v1/workspace/jobs/{self.work_order.pk}/meters/reading/"
        payload = {
            "meter_id": str(self.meter_hours.pk),
            "value": "1990.00",
        }
        resp = self.api_client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data["error"]["code"], "meter_not_monotonic")

    def test_api_rbac_enforcement(self):
        """User without work_order.record permission is forbidden from recording readings."""
        # Authenticate as unassigned other technician
        client = APIClient()
        client.force_authenticate(user=self.other_technician.user)
        client.credentials(HTTP_X_ORGANIZATION=str(self.org.pk))

        url = f"/api/v1/workspace/jobs/{self.work_order.pk}/meters/reading/"
        resp = client.post(url, {"meter_id": str(self.meter_hours.pk), "value": "2500.00"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
