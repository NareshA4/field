"""Automated Unit Tests for Module M14 (Operational Dashboards).

Tests the backend metric calculation engine, formulas, edge cases (zero division,
empty windows, large windows), and multi-tenant security isolation.
"""
import datetime
from decimal import Decimal
import uuid

from django.test import Client, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assets.models import Asset, AssetCategory
from apps.core.exceptions import PermissionDenied
from apps.dashboards import metrics
from apps.incidents.models import Downtime, ServiceRequest
from apps.inventory.models import Part, StockBalance, StockMovement, Warehouse
from apps.rbac import services as rbac
from apps.sites.models import Site
from apps.sla.models import SLABreach, SLAProfile, SLATarget, SLATracking
from apps.tenancy import services as tenancy
from apps.tenancy.models import Membership
from apps.workorders.models import WorkOrder, WorkOrderLabor


class M14DashboardMetricsTests(TestCase):
    def setUp(self):
        # 1. Admin & Tenant A
        self.admin = User.objects.create_user(
            email="admin@dashboard.test",
            password="TestPassword123!",
            full_name="Platform Admin",
            is_platform_admin=True,
        )
        self.org_a, self.owner_a = tenancy.create_organization(
            name="Alpha Operations",
            owner_email="owner@alpha.test",
            owner_name="Owner Alpha",
            actor=self.admin,
        )

        # Tenant B for isolation testing
        self.org_b, self.owner_b = tenancy.create_organization(
            name="Beta Operations",
            owner_email="owner@beta.test",
            owner_name="Owner Beta",
            actor=self.admin,
        )

        # 2. Sites
        self.site_a1 = Site.objects.create(
            organization=self.org_a,
            code="SITE-A1",
            name="Alpha Plant 1",
            address="100 Industrial Parkway",
        )
        self.site_a2 = Site.objects.create(
            organization=self.org_a,
            code="SITE-A2",
            name="Alpha Plant 2",
            address="200 Harbor Road",
        )
        self.site_b1 = Site.objects.create(
            organization=self.org_b,
            code="SITE-B1",
            name="Beta Facility",
            address="500 West Highway",
        )

        # 3. Assets
        self.cat_a = AssetCategory.objects.create(organization=self.org_a, name="Pumps & Turbines")
        self.asset_a1 = Asset.objects.create(
            organization=self.org_a,
            site=self.site_a1,
            category=self.cat_a,
            asset_tag="AST-A1-001",
            name="Chilled Water Pump 1",
            status=Asset.Status.ACTIVE,
        )
        self.asset_a2 = Asset.objects.create(
            organization=self.org_a,
            site=self.site_a2,
            category=self.cat_a,
            asset_tag="AST-A2-001",
            name="Booster Fan 1",
            status=Asset.Status.ACTIVE,
        )

        # 4. Users & Memberships
        tech_user = User.objects.create_user(
            email="tech@alpha.test", password="Password123!", full_name="Kasun Perera"
        )
        self.tech_m = Membership.objects.create(
            organization=self.org_a, user=tech_user, status=Membership.Status.ACTIVE
        )
        tech_role = rbac.system_role_by_key(self.org_a, "technician")
        rbac.set_membership_roles(self.tech_m, [tech_role], actor=None, system=True)

        manager_user = User.objects.create_user(
            email="mgr@alpha.test", password="Password123!", full_name="Maintenance Manager"
        )
        self.mgr_m = Membership.objects.create(
            organization=self.org_a, user=manager_user, status=Membership.Status.ACTIVE
        )
        planner_role = rbac.system_role_by_key(self.org_a, "maintenance_planner")
        rbac.set_membership_roles(self.mgr_m, [planner_role], actor=None, system=True)

        # Standard test window (parsed window covering through end of today UTC)
        self.now = timezone.now()
        self.start_dt, self.end_dt = metrics.parse_date_window()

    def _make_wo(self, **kwargs) -> WorkOrder:
        kwargs.setdefault("organization", self.org_a)
        kwargs.setdefault("site", self.site_a1)
        kwargs.setdefault("asset", self.asset_a1)
        kwargs.setdefault("title", f"Test WO {uuid.uuid4().hex[:6]}")
        kwargs.setdefault("order_number", f"WO-{uuid.uuid4().hex[:8].upper()}")
        kwargs.setdefault("created_by", self.admin)
        return WorkOrder.objects.create(**kwargs)

    def _make_sr(self, **kwargs) -> ServiceRequest:
        kwargs.setdefault("organization", self.org_a)
        kwargs.setdefault("site", self.site_a1)
        kwargs.setdefault("asset", self.asset_a1)
        kwargs.setdefault("title", f"Test SR {uuid.uuid4().hex[:6]}")
        kwargs.setdefault("number", f"SR-{uuid.uuid4().hex[:8].upper()}")
        kwargs.setdefault("reported_by", self.owner_a)
        kwargs.setdefault("occurred_at", self.now - datetime.timedelta(days=1))
        return ServiceRequest.objects.create(**kwargs)

    def test_kpi_definitions_catalog(self):
        """Verifies that all core KPIs are registered with valid metadata."""
        catalog = metrics.KPI_DEFINITIONS
        self.assertIn("mttr", catalog)
        self.assertIn("mtbf", catalog)
        self.assertIn("downtime_hours", catalog)
        self.assertIn("open_work_orders", catalog)
        self.assertIn("overdue_work_orders", catalog)
        self.assertIn("technician_utilization", catalog)
        self.assertIn("pm_compliance", catalog)
        self.assertIn("parts_consumption", catalog)
        self.assertIn("sla_breaches", catalog)

        for key, defn in catalog.items():
            self.assertIn("name", defn)
            self.assertIn("section", defn)
            self.assertIn("unit", defn)
            self.assertIn("formula", defn)
            self.assertIn("source", defn)

    def test_parse_date_window_boundaries(self):
        """Tests date window parsing, swapping, and 400 days limit clamping."""
        # Default window
        start, end = metrics.parse_date_window()
        delta_days = (end.date() - start.date()).days
        self.assertIn(delta_days, [30, 31])

        # Custom valid window
        start, end = metrics.parse_date_window("2026-01-01", "2026-01-15")
        self.assertEqual(start.date(), datetime.date(2026, 1, 1))
        self.assertEqual(end.date(), datetime.date(2026, 1, 15))

        # Swapped dates (from > to)
        start, end = metrics.parse_date_window("2026-02-01", "2026-01-01")
        self.assertLessEqual(start, end)

        # Excessively large window (> 400 days)
        start, end = metrics.parse_date_window("2024-01-01", "2026-01-01")
        self.assertLessEqual((end.date() - start.date()).days, 400)

    def test_mttr_calculation_happy_and_zero_records(self):
        """Tests Mean Time to Repair (MTTR) calculation and empty fallback."""
        sites = [self.site_a1, self.site_a2]

        # Zero records -> returns "n/a"
        empty_res = metrics.compute_mttr(self.org_a, sites, self.start_dt, self.end_dt)
        self.assertEqual(empty_res["value"], "n/a")
        self.assertIsNone(empty_res["numeric"])
        self.assertEqual(empty_res["count"], 0)

        # Create 2 incident requests with downtimes: 4 hours and 2 hours
        req1 = self._make_sr(
            title="Breakdown 1",
            occurred_at=self.now - datetime.timedelta(days=10, hours=4),
        )
        Downtime.objects.create(
            organization=self.org_a,
            request=req1,
            asset=self.asset_a1,
            started_at=self.now - datetime.timedelta(days=10, hours=4),
            ended_at=self.now - datetime.timedelta(days=10),
        )

        req2 = self._make_sr(
            title="Breakdown 2",
            occurred_at=self.now - datetime.timedelta(days=5, hours=2),
        )
        Downtime.objects.create(
            organization=self.org_a,
            request=req2,
            asset=self.asset_a1,
            started_at=self.now - datetime.timedelta(days=5, hours=2),
            ended_at=self.now - datetime.timedelta(days=5),
        )

        # MTTR = (4.0 + 2.0) / 2 = 3.0 hours
        mttr_res = metrics.compute_mttr(self.org_a, sites, self.start_dt, self.end_dt)
        self.assertEqual(mttr_res["numeric"], 3.0)
        self.assertEqual(mttr_res["value"], "3.00 h")
        self.assertEqual(mttr_res["count"], 2)

    def test_downtime_hours_overlap_calculation(self):
        """Verifies calculation of overlapping downtime hours."""
        sites = [self.site_a1]

        # Create an outage starting 2 days ago and ending 1 day ago (24 hours)
        req = self._make_sr(
            title="Outage 1",
            occurred_at=self.now - datetime.timedelta(days=2),
        )
        Downtime.objects.create(
            organization=self.org_a,
            request=req,
            asset=self.asset_a1,
            started_at=self.now - datetime.timedelta(days=2),
            ended_at=self.now - datetime.timedelta(days=1),
        )

        dt_res = metrics.compute_downtime_hours(self.org_a, sites, self.start_dt, self.end_dt)
        self.assertEqual(dt_res["numeric"], 24.0)
        self.assertEqual(dt_res["value"], "24.0 h")
        self.assertEqual(dt_res["closed_outages"], 1)

    def test_mtbf_calculation(self):
        """Tests Mean Time Between Failures (MTBF) calculation."""
        sites = [self.site_a1]

        # 0 failures -> returns "n/a"
        mtbf_empty = metrics.compute_mtbf(self.org_a, sites, self.start_dt, self.end_dt)
        self.assertEqual(mtbf_empty["value"], "n/a")
        self.assertIsNone(mtbf_empty["numeric"])

        # 1 failure with 10h downtime
        req = self._make_sr(
            title="Failure 1",
            occurred_at=self.now - datetime.timedelta(days=2),
        )
        Downtime.objects.create(
            organization=self.org_a,
            request=req,
            asset=self.asset_a1,
            started_at=self.now - datetime.timedelta(days=2),
            ended_at=self.now - datetime.timedelta(days=2) + datetime.timedelta(hours=10),
        )

        # 1 asset, window hours approx 720h. Operating = window - 10. MTBF approx operating / 1
        mtbf_res = metrics.compute_mtbf(self.org_a, sites, self.start_dt, self.end_dt)
        self.assertEqual(mtbf_res["failures"], 1)
        self.assertGreater(mtbf_res["numeric"], 700.0)

    def test_technician_utilization_calculation(self):
        """Tests technician utilization calculation against 8-hour workday capacity."""
        sites = [self.site_a1]

        # Create a work order and log 16 hours of labor across 2 days
        wo = self._make_wo(title="Service job")
        WorkOrderLabor.objects.create(
            organization=self.org_a,
            work_order=wo,
            technician=self.tech_m,
            entered_by=self.admin,
            work_date=self.now.date() - datetime.timedelta(days=1),
            hours=Decimal("8.00"),
            description="Day 1 labor",
        )
        WorkOrderLabor.objects.create(
            organization=self.org_a,
            work_order=wo,
            technician=self.tech_m,
            entered_by=self.admin,
            work_date=self.now.date(),
            hours=Decimal("8.00"),
            description="Day 2 labor",
        )

        # Window: 2 days (yesterday to today) -> Capacity for 1 tech = 2 * 8 = 16 hours. Util = 100%
        window_start = datetime.datetime.combine(self.now.date() - datetime.timedelta(days=1), datetime.time.min, tzinfo=datetime.timezone.utc)
        window_end = datetime.datetime.combine(self.now.date(), datetime.time.max, tzinfo=datetime.timezone.utc)
        util_res = metrics.compute_technician_utilization(self.org_a, sites, window_start, window_end)
        self.assertEqual(util_res["total_hours"], 16.0)
        self.assertEqual(util_res["numeric"], 100.0)
        self.assertEqual(util_res["value"], "100.0%")

    def test_pm_compliance_calculation(self):
        """Tests Preventive Maintenance compliance percentage."""
        sites = [self.site_a1]

        # Order 1: On-time (completed before due)
        self._make_wo(
            title="PM On-Time",
            work_type=WorkOrder.WorkType.PREVENTIVE,
            status=WorkOrder.Status.COMPLETED,
            due_at=self.now - datetime.timedelta(days=5),
            completed_at=self.now - datetime.timedelta(days=6),
        )

        # Order 2: Late (completed after due)
        self._make_wo(
            title="PM Late",
            work_type=WorkOrder.WorkType.PREVENTIVE,
            status=WorkOrder.Status.COMPLETED,
            due_at=self.now - datetime.timedelta(days=4),
            completed_at=self.now - datetime.timedelta(days=2),
        )

        # PM Compliance: 1 on-time / 2 total = 50.0%
        pm_res = metrics.compute_pm_compliance(self.org_a, sites, self.start_dt, self.end_dt)
        self.assertEqual(pm_res["total_due"], 2)
        self.assertEqual(pm_res["on_time"], 1)
        self.assertEqual(pm_res["late"], 1)
        self.assertEqual(pm_res["numeric"], 50.0)
        self.assertEqual(pm_res["value"], "50.0%")

    def test_parts_consumption_calculation(self):
        """Tests net parts consumption calculation (ISSUE - RETURN)."""
        sites = [self.site_a1]

        wh = Warehouse.objects.create(
            organization=self.org_a,
            site=self.site_a1,
            code="WH-A1",
            name="Main Storeroom",
        )
        part = Part.objects.create(
            organization=self.org_a,
            part_number="FLTR-OIL-01",
            name="Oil Filter Cartridge",
            unit="pcs",
        )
        bal = StockBalance.objects.create(
            organization=self.org_a,
            warehouse=wh,
            part=part,
            on_hand=Decimal("100"),
            reserved=Decimal("0"),
        )

        # Issue 10 units, return 3 units -> Net consumed = 7 units
        StockMovement.objects.create(
            organization=self.org_a,
            balance=bal,
            warehouse=wh,
            part=part,
            movement_type=StockMovement.Type.ISSUE,
            quantity=Decimal("10.00"),
            on_hand_delta=Decimal("-10.00"),
            on_hand_after=Decimal("90.00"),
            reserved_after=Decimal("0.00"),
            actor=self.admin,
        )
        StockMovement.objects.create(
            organization=self.org_a,
            balance=bal,
            warehouse=wh,
            part=part,
            movement_type=StockMovement.Type.RETURN,
            quantity=Decimal("3.00"),
            on_hand_delta=Decimal("3.00"),
            on_hand_after=Decimal("93.00"),
            reserved_after=Decimal("0.00"),
            actor=self.admin,
        )

        parts_res = metrics.compute_parts_consumption(self.org_a, sites, self.start_dt, self.end_dt)
        self.assertEqual(parts_res["issued"], 10.0)
        self.assertEqual(parts_res["returned"], 3.0)
        self.assertEqual(parts_res["numeric"], 7.0)
        self.assertEqual(parts_res["value"], "7.0 units")
        self.assertEqual(len(parts_res["top_parts"]), 1)

    def test_work_order_snapshots(self):
        """Tests open and overdue work order snapshot counts."""
        sites = [self.site_a1]

        # 1 In Progress (open)
        self._make_wo(
            title="Job 1",
            status=WorkOrder.Status.IN_PROGRESS,
            planned_end=self.now + datetime.timedelta(days=1),
        )

        # 1 Planned and Overdue
        self._make_wo(
            title="Job 2 (Overdue)",
            status=WorkOrder.Status.PLANNED,
            planned_end=self.now - datetime.timedelta(days=1),
        )

        # 1 Completed (not open, not overdue)
        self._make_wo(
            title="Job 3 (Closed)",
            status=WorkOrder.Status.COMPLETED,
            completed_at=self.now,
        )

        snap = metrics.compute_work_order_snapshots(self.org_a, sites)
        self.assertEqual(snap["open_count"], 2)
        self.assertEqual(snap["overdue_count"], 1)

    def test_my_work_dashboard_aggregation(self):
        """Verifies technician personal dashboard aggregates only their own jobs and hours."""
        wo1 = self._make_wo(
            title="Kasun Job 1",
            status=WorkOrder.Status.IN_PROGRESS,
            assigned_to=self.tech_m,
        )
        WorkOrderLabor.objects.create(
            organization=self.org_a,
            work_order=wo1,
            technician=self.tech_m,
            entered_by=self.admin,
            work_date=self.now.date(),
            hours=Decimal("4.50"),
            description="Repair work",
        )

        my_res = metrics.get_my_work_dashboard(self.tech_m, self.start_dt, self.end_dt)
        self.assertEqual(my_res["kpis"]["open_jobs"], 1)
        self.assertEqual(my_res["kpis"]["active_in_progress"], 1)
        self.assertEqual(my_res["kpis"]["logged_hours"], "4.5 h")

    def test_multi_tenant_isolation(self):
        """Guarantees Tenant A queries strictly exclude data belonging to Tenant B."""
        # Create work order in Tenant B
        cat_b = AssetCategory.objects.create(organization=self.org_b, name="Beta Category")
        asset_b = Asset.objects.create(
            organization=self.org_b,
            site=self.site_b1,
            category=cat_b,
            asset_tag="AST-B1-001",
            name="Beta Machine",
        )
        WorkOrder.objects.create(
            organization=self.org_b,
            site=self.site_b1,
            asset=asset_b,
            title="Tenant B Secret Order",
            order_number=f"WO-B-{uuid.uuid4().hex[:8].upper()}",
            status=WorkOrder.Status.IN_PROGRESS,
            created_by=self.admin,
        )

        # Query Tenant A
        sites_a = [self.site_a1, self.site_a2]
        ops_a = metrics.get_operations_dashboard(self.org_a, sites_a, self.start_dt, self.end_dt)

        # Tenant A sees 0 work orders, Tenant B's order is not visible
        self.assertEqual(ops_a["kpis"]["open_work_orders"], 0)


class M14DashboardAPITests(TestCase):
    """Automated REST API tests for M14 Operational Dashboards."""

    def setUp(self):
        # 1. Admin & Organizations
        self.admin = User.objects.create_user(
            email="admin.api@dashboard.test",
            password="TestPassword123!",
            full_name="Platform Admin API",
            is_platform_admin=True,
        )
        self.org_a, self.owner_a = tenancy.create_organization(
            name="Alpha Corp API",
            owner_email="owner@alpha-api.test",
            owner_name="Owner Alpha API",
            actor=self.admin,
        )
        self.org_b, self.owner_b = tenancy.create_organization(
            name="Beta Corp API",
            owner_email="owner@beta-api.test",
            owner_name="Owner Beta API",
            actor=self.admin,
        )
        self.owner_a.status = Membership.Status.ACTIVE
        self.owner_a.save(update_fields=["status"])
        self.owner_b.status = Membership.Status.ACTIVE
        self.owner_b.save(update_fields=["status"])

        # 2. Sites
        self.site_a1 = Site.objects.create(
            organization=self.org_a,
            code="SITE-API-1",
            name="Alpha Plant API 1",
            address="100 Test Blvd",
        )
        self.site_a2 = Site.objects.create(
            organization=self.org_a,
            code="SITE-API-2",
            name="Alpha Plant API 2",
            address="200 Test Blvd",
        )
        self.site_b1 = Site.objects.create(
            organization=self.org_b,
            code="SITE-BETA-1",
            name="Beta Plant API 1",
            address="999 Beta Ave",
        )

        # 3. Assets & Categories
        self.cat_a = AssetCategory.objects.create(organization=self.org_a, name="Turbines")
        self.asset_a1 = Asset.objects.create(
            organization=self.org_a,
            site=self.site_a1,
            category=self.cat_a,
            asset_tag="AST-API-001",
            name="Gas Turbine 1",
            status=Asset.Status.ACTIVE,
        )

        # 4. Memberships & Roles in Org A
        # Operations Manager (has report.view and all operational data perms)
        mgr_user = User.objects.create_user(
            email="mgr.api@alpha-api.test",
            password="Password123!",
            full_name="Manager Alpha",
        )
        self.mgr_m = Membership.objects.create(
            organization=self.org_a,
            user=mgr_user,
            status=Membership.Status.ACTIVE,
        )
        mgr_role = rbac.system_role_by_key(self.org_a, "operations_manager")
        rbac.set_membership_roles(self.mgr_m, [mgr_role], actor=None, system=True)

        # Technician (has work_order.view_assigned, but NOT report.view)
        tech_user = User.objects.create_user(
            email="tech.api@alpha-api.test",
            password="Password123!",
            full_name="Technician Alpha",
        )
        self.tech_m = Membership.objects.create(
            organization=self.org_a,
            user=tech_user,
            status=Membership.Status.ACTIVE,
        )
        tech_role = rbac.system_role_by_key(self.org_a, "technician")
        rbac.set_membership_roles(self.tech_m, [tech_role], actor=None, system=True)

        # 5. Populate Sample Transactional Data
        self.now = timezone.now()

        # Work Order
        self.wo1 = WorkOrder.objects.create(
            organization=self.org_a,
            site=self.site_a1,
            asset=self.asset_a1,
            title="API Overhaul Pump 1",
            order_number=f"WO-API-{uuid.uuid4().hex[:8].upper()}",
            status=WorkOrder.Status.IN_PROGRESS,
            work_type=WorkOrder.WorkType.PREVENTIVE,
            priority=WorkOrder.Priority.HIGH,
            assigned_to=self.tech_m,
            created_by=self.admin,
        )

        # Service Request
        self.sr1 = ServiceRequest.objects.create(
            organization=self.org_a,
            site=self.site_a1,
            asset=self.asset_a1,
            number=f"SR-API-{uuid.uuid4().hex[:8].upper()}",
            title="Vibration alert",
            description="High vibration on turbine",
            status="NEW",
            severity=ServiceRequest.Severity.HIGH,
            reported_by=self.owner_a,
            occurred_at=self.now - datetime.timedelta(days=1),
        )

        # Downtime (3 hours)
        self.dt1 = Downtime.objects.create(
            organization=self.org_a,
            request=self.sr1,
            asset=self.asset_a1,
            started_at=self.now - datetime.timedelta(hours=5),
            ended_at=self.now - datetime.timedelta(hours=2),
            recorded_by=self.admin,
        )

        # Warehouse & Stock Balance
        self.wh1 = Warehouse.objects.create(
            organization=self.org_a,
            site=self.site_a1,
            code="WH-API-1",
            name="Main Warehouse API",
        )
        self.part1 = Part.objects.create(
            organization=self.org_a,
            part_number="PRT-API-001",
            name="Mechanical Gasket",
        )
        self.balance1 = StockBalance.objects.create(
            organization=self.org_a,
            warehouse=self.wh1,
            part=self.part1,
            on_hand=Decimal("5.00"),
            min_level=Decimal("10.00"),  # is_low = True
        )

        # Stock Movement (Parts consumption)
        StockMovement.objects.create(
            organization=self.org_a,
            balance=self.balance1,
            warehouse=self.wh1,
            part=self.part1,
            movement_type=StockMovement.Type.ISSUE,
            quantity=Decimal("3.00"),
            on_hand_delta=Decimal("-3.00"),
            on_hand_after=Decimal("2.00"),
            reserved_after=Decimal("0.00"),
            actor=self.admin,
        )

        # SLA Breach
        profile = SLAProfile.objects.create(
            organization=self.org_a,
            name="Turbine API SLA",
            applies_to="WORK_ORDER",
        )
        tracking = SLATracking.objects.create(
            organization=self.org_a,
            profile=profile,
            work_order=self.wo1,
            site=self.site_a1,
            priority="HIGH",
            response_minutes=60,
            resolution_minutes=240,
            warning_percent=80,
            started_at=self.now - datetime.timedelta(hours=6),
            response_due_at=self.now - datetime.timedelta(hours=5),
            resolution_due_at=self.now - datetime.timedelta(hours=2),
            status=SLATracking.Status.ACTIVE,
            resolution_state=SLATracking.TargetState.BREACHED,
        )
        SLABreach.objects.create(
            organization=self.org_a,
            tracking=tracking,
            profile=profile,
            site=self.site_a1,
            priority="HIGH",
            severity="MAJOR",
            target_kind="RESOLUTION",
            breached_at=self.now - datetime.timedelta(hours=1),
            detected_at=self.now - datetime.timedelta(hours=1),
            status=SLABreach.Status.OPEN,
        )

        # Technician Labor
        WorkOrderLabor.objects.create(
            organization=self.org_a,
            work_order=self.wo1,
            technician=self.tech_m,
            entered_by=self.admin,
            work_date=self.now.date(),
            hours=Decimal("6.00"),
            description="Replaced turbine seal",
        )

        # API Clients
        self.mgr_client = APIClient()
        self.mgr_client.force_authenticate(user=self.mgr_m.user)
        self.mgr_client.credentials(HTTP_X_ORGANIZATION=str(self.org_a.pk))

        self.tech_client = APIClient()
        self.tech_client.force_authenticate(user=self.tech_m.user)
        self.tech_client.credentials(HTTP_X_ORGANIZATION=str(self.org_a.pk))

        self.beta_client = APIClient()
        self.beta_client.force_authenticate(user=self.owner_b.user)
        self.beta_client.credentials(HTTP_X_ORGANIZATION=str(self.org_b.pk))

    def test_api_catalog_success(self):
        """GET /api/v1/dashboards/ returns 200 with full KPI definitions catalog."""
        res = self.mgr_client.get("/api/v1/dashboards/")
        self.assertEqual(res.status_code, 200)
        data = res.data
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["organization_id"], str(self.org_a.pk))
        self.assertEqual(len(data["kpis"]), 9)
        self.assertIn("mttr", data["kpis"])
        self.assertIn("technician_utilization", data["kpis"])
        self.assertIn("pm_compliance", data["kpis"])
        self.assertIn("parts_consumption", data["kpis"])
        self.assertIn("operations", data["sections"])

    def test_api_catalog_requires_report_view(self):
        """Technician lacking report.view is rejected with 403 on catalog endpoint."""
        res = self.tech_client.get("/api/v1/dashboards/")
        self.assertEqual(res.status_code, 403)

    def test_api_operations_section_success(self):
        """GET /api/v1/dashboards/operations/ returns real aggregated operational metrics."""
        res = self.mgr_client.get("/api/v1/dashboards/operations/")
        self.assertEqual(res.status_code, 200)
        body = res.data
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["section"], "operations")
        self.assertEqual(body["data"]["kpis"]["open_work_orders"], 1)
        self.assertEqual(body["data"]["kpis"]["downtime_hours"], "3.0 h")
        self.assertEqual(body["data"]["kpis"]["mttr"], "3.00 h")
        self.assertEqual(body["data"]["kpis"]["sla_breaches"], "1")

    def test_api_assets_section_success(self):
        """GET /api/v1/dashboards/assets/ returns downtime, asset counts, and top failing."""
        res = self.mgr_client.get("/api/v1/dashboards/assets/")
        self.assertEqual(res.status_code, 200)
        body = res.data
        self.assertEqual(body["section"], "assets")
        self.assertEqual(body["data"]["kpis"]["total_assets"], 1)
        self.assertEqual(body["data"]["kpis"]["downtime_hours"], "3.0 h")
        self.assertGreater(len(body["data"]["details"]["top_failing_assets"]), 0)

    def test_api_maintenance_section_success(self):
        """GET /api/v1/dashboards/maintenance/ returns PM compliance and work order type mix."""
        res = self.mgr_client.get("/api/v1/dashboards/maintenance/")
        self.assertEqual(res.status_code, 200)
        body = res.data
        self.assertEqual(body["section"], "maintenance")
        self.assertIn("pm_compliance", body["data"]["kpis"])
        self.assertEqual(body["data"]["details"]["work_type_mix"]["Preventive"], 1)

    def test_api_service_section_success(self):
        """GET /api/v1/dashboards/service/ returns open service requests and SLA breach counts."""
        res = self.mgr_client.get("/api/v1/dashboards/service/")
        self.assertEqual(res.status_code, 200)
        body = res.data
        self.assertEqual(body["section"], "service")
        self.assertEqual(body["data"]["kpis"]["open_requests"], 1)
        self.assertEqual(body["data"]["kpis"]["sla_breaches"], "1")

    def test_api_inventory_section_success(self):
        """GET /api/v1/dashboards/inventory/ returns stock counts and parts consumption."""
        res = self.mgr_client.get("/api/v1/dashboards/inventory/")
        self.assertEqual(res.status_code, 200)
        body = res.data
        self.assertEqual(body["section"], "inventory")
        self.assertEqual(body["data"]["kpis"]["total_stock_lines"], 1)
        self.assertEqual(body["data"]["kpis"]["low_stock_alerts"], 1)
        self.assertEqual(body["data"]["kpis"]["net_parts_consumed"], "3.0 units")

    def test_api_my_work_accessible_by_technician(self):
        """Technician accesses their own personal workspace execution metrics without report.view."""
        res = self.tech_client.get("/api/v1/dashboards/my-work/")
        self.assertEqual(res.status_code, 200)
        body = res.data
        self.assertEqual(body["section"], "my-work")
        self.assertEqual(body["data"]["kpis"]["open_jobs"], 1)
        self.assertEqual(body["data"]["kpis"]["logged_hours"], "6.0 h")

    def test_api_date_window_query_params(self):
        """Query parameters 'from' and 'to' filter the calculation window correctly."""
        # Querying an old time window where no downtime occurred
        res = self.mgr_client.get("/api/v1/dashboards/operations/?from=2020-01-01&to=2020-01-10")
        self.assertEqual(res.status_code, 200)
        body = res.data
        self.assertEqual(body["date_window"]["from"], "2020-01-01")
        self.assertEqual(body["date_window"]["to"], "2020-01-10")
        self.assertEqual(body["data"]["kpis"]["downtime_hours"], "0.0 h")

    def test_api_site_filter_uuid_and_code(self):
        """Query parameter 'site' filters metrics by site UUID or site code."""
        # Filter by site UUID (site_a1 has open WOs)
        res1 = self.mgr_client.get(f"/api/v1/dashboards/operations/?site={self.site_a1.pk}")
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.data["data"]["kpis"]["open_work_orders"], 1)

        # Filter by site code (site_a2 has 0 open WOs)
        res2 = self.mgr_client.get("/api/v1/dashboards/operations/?site=SITE-API-2")
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.data["data"]["kpis"]["open_work_orders"], 0)

    def test_api_unauthorized_site_rejected(self):
        """Requesting a site outside the organization is rejected with 403."""
        res = self.mgr_client.get(f"/api/v1/dashboards/operations/?site={self.site_b1.pk}")
        self.assertEqual(res.status_code, 403)

    def test_api_invalid_section_rejected(self):
        """Requesting an invalid dashboard section name returns 400 Bad Request."""
        res = self.mgr_client.get("/api/v1/dashboards/financial_projections/")
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.data)

    def test_api_unauthenticated_request_rejected(self):
        """Anonymous request without JWT/session credentials is rejected with 401."""
        anon = APIClient()
        res = anon.get("/api/v1/dashboards/")
        self.assertEqual(res.status_code, 401)

    def test_api_missing_organization_header(self):
        """Authenticated request without X-Organization header when ambiguous is rejected with 403."""
        # Create second membership so user belongs to two organizations and cannot be auto-defaulted
        Membership.objects.create(
            organization=self.org_b,
            user=self.mgr_m.user,
            status=Membership.Status.ACTIVE,
        )
        client = APIClient()
        client.force_authenticate(user=self.mgr_m.user)
        res = client.get("/api/v1/dashboards/operations/")
        self.assertEqual(res.status_code, 403)

    def test_api_multi_tenant_isolation(self):
        """Beta Organization cannot see any Alpha Organization metrics."""
        res = self.beta_client.get("/api/v1/dashboards/operations/")
        self.assertEqual(res.status_code, 200)
        body = res.data
        self.assertEqual(body["data"]["kpis"]["open_work_orders"], 0)
        self.assertEqual(body["data"]["kpis"]["downtime_hours"], "0.0 h")


class M14DashboardUIViewTests(TestCase):
    """Automated server-rendered UI view tests for M14 Dashboards Hub and My Work."""

    def setUp(self):
        # 1. Admin & Organizations
        self.admin = User.objects.create_user(
            email="admin.ui@dashboard.test",
            password="TestPassword123!",
            full_name="Platform Admin UI",
            is_platform_admin=True,
        )
        self.org_a, self.owner_a = tenancy.create_organization(
            name="Alpha Corp UI",
            owner_email="owner@alpha-ui.test",
            owner_name="Owner Alpha UI",
            actor=self.admin,
        )
        self.owner_a.status = Membership.Status.ACTIVE
        self.owner_a.save(update_fields=["status"])

        # 2. Site & Asset
        self.site_a1 = Site.objects.create(
            organization=self.org_a,
            code="SITE-UI-1",
            name="Alpha Plant UI 1",
            address="100 Test Blvd",
        )
        self.cat_a = AssetCategory.objects.create(organization=self.org_a, name="UI Turbines")
        self.asset_a1 = Asset.objects.create(
            organization=self.org_a,
            site=self.site_a1,
            category=self.cat_a,
            asset_tag="AST-UI-001",
            name="Gas Turbine UI",
            status=Asset.Status.ACTIVE,
        )

        # 3. Manager & Technician
        mgr_user = User.objects.create_user(
            email="mgr.ui@alpha-ui.test",
            password="Password123!",
            full_name="Manager Alpha UI",
        )
        self.mgr_m = Membership.objects.create(
            organization=self.org_a,
            user=mgr_user,
            status=Membership.Status.ACTIVE,
        )
        mgr_role = rbac.system_role_by_key(self.org_a, "operations_manager")
        rbac.set_membership_roles(self.mgr_m, [mgr_role], actor=None, system=True)

        tech_user = User.objects.create_user(
            email="tech.ui@alpha-ui.test",
            password="Password123!",
            full_name="Technician Alpha UI",
        )
        self.tech_m = Membership.objects.create(
            organization=self.org_a,
            user=tech_user,
            status=Membership.Status.ACTIVE,
        )
        tech_role = rbac.system_role_by_key(self.org_a, "technician")
        rbac.set_membership_roles(self.tech_m, [tech_role], actor=None, system=True)

        # 4. Work Order & Service Request & Downtime
        self.now = timezone.now()
        self.wo1 = WorkOrder.objects.create(
            organization=self.org_a,
            site=self.site_a1,
            asset=self.asset_a1,
            title="UI Overhaul Pump 1",
            order_number=f"WO-UI-{uuid.uuid4().hex[:8].upper()}",
            status=WorkOrder.Status.IN_PROGRESS,
            work_type=WorkOrder.WorkType.PREVENTIVE,
            priority=WorkOrder.Priority.HIGH,
            assigned_to=self.tech_m,
            created_by=self.admin,
        )
        self.sr1 = ServiceRequest.objects.create(
            organization=self.org_a,
            site=self.site_a1,
            asset=self.asset_a1,
            number=f"SR-UI-{uuid.uuid4().hex[:8].upper()}",
            title="UI Vibration alert",
            description="High vibration on turbine",
            status="NEW",
            severity=ServiceRequest.Severity.HIGH,
            reported_by=self.owner_a,
            occurred_at=self.now - datetime.timedelta(days=1),
        )
        self.dt1 = Downtime.objects.create(
            organization=self.org_a,
            request=self.sr1,
            asset=self.asset_a1,
            started_at=self.now - datetime.timedelta(hours=4),
            ended_at=self.now - datetime.timedelta(hours=2),
            recorded_by=self.admin,
        )

        # 5. UI Clients
        self.mgr_client = Client(headers={"host": "localhost"})
        self.mgr_client.force_login(self.mgr_m.user)
        session = self.mgr_client.session
        session["active_org_id"] = str(self.org_a.pk)
        session.save()

        self.tech_client = Client(headers={"host": "localhost"})
        self.tech_client.force_login(self.tech_m.user)
        session = self.tech_client.session
        session["active_org_id"] = str(self.org_a.pk)
        session.save()

    def test_hub_view_operations_renders(self):
        """GET /app/dashboards/ renders HTTP 200 with operations KPI cards and CSS bars."""
        res = self.mgr_client.get("/app/dashboards/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Operational Dashboards")
        self.assertContains(res, "Open Work Orders")
        self.assertContains(res, "Mean Time To Repair")
        self.assertIn("kpis", res.context)
        self.assertEqual(res.context["section"], "operations")

    def test_hub_view_assets_renders(self):
        """GET /app/dashboards/?section=assets renders HTTP 200 with asset metrics."""
        res = self.mgr_client.get("/app/dashboards/?section=assets")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Total Assets")
        self.assertEqual(res.context["section"], "assets")

    def test_hub_view_maintenance_renders(self):
        """GET /app/dashboards/?section=maintenance renders HTTP 200 with PM metrics."""
        res = self.mgr_client.get("/app/dashboards/?section=maintenance")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "PM Compliance")
        self.assertEqual(res.context["section"], "maintenance")

    def test_hub_view_service_renders(self):
        """GET /app/dashboards/?section=service renders HTTP 200 with service requests."""
        res = self.mgr_client.get("/app/dashboards/?section=service")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Open Service Requests")
        self.assertEqual(res.context["section"], "service")

    def test_hub_view_inventory_renders(self):
        """GET /app/dashboards/?section=inventory renders HTTP 200 with inventory stock."""
        res = self.mgr_client.get("/app/dashboards/?section=inventory")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Stocked SKU Lines")
        self.assertEqual(res.context["section"], "inventory")

    def test_hub_view_filters(self):
        """Date and site query parameters are applied to the hub context."""
        res = self.mgr_client.get(f"/app/dashboards/?section=operations&site={self.site_a1.pk}&from=2026-01-01&to=2026-01-15")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["date_from"], "2026-01-01")
        self.assertEqual(res.context["date_to"], "2026-01-15")

    def test_hub_view_requires_report_view(self):
        """Technician without report.view gets HTTP 403 on dashboards hub."""
        res = self.tech_client.get("/app/dashboards/")
        self.assertEqual(res.status_code, 403)

    def test_my_work_view_renders_for_technician(self):
        """Technician gets HTTP 200 on My Work cockpit without report.view."""
        res = self.tech_client.get("/app/dashboards/my-work/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "My Work Cockpit")
        self.assertContains(res, "Workday Capacity Utilization")
        self.assertIn("open_jobs", res.context)


