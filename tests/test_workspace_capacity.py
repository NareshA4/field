from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, Client
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assets.models import Asset, AssetCategory
from apps.rbac import services as rbac
from apps.sites.models import Site
from apps.tenancy import services as tenancy
from apps.tenancy.models import Membership
from apps.workorders.models import WorkOrder, WorkOrderLabor
from apps.workorders import services as wo_services
from apps.workspace import selectors, services as ws_services


class WorkspaceDailyCapacityTests(TestCase):
    """
    Test suite for PRD Section 4.5 & Add-On 4:
    Technician Workday Capacity & Utilization Gauge in Cockpit and API.
    """

    def setUp(self):
        # 1. Organization & Admin
        self.admin = User.objects.create_user(
            email="admin@capacity.test",
            password="TestPassword123!",
            full_name="Admin Capacity",
            is_platform_admin=True,
        )
        self.org, self.owner_m = tenancy.create_organization(
            name="Apex Field Services",
            owner_email="ops@capacity.test",
            owner_name="Operations Lead",
            actor=self.admin,
        )

        # 2. Site & Asset
        self.site = Site.objects.create(
            organization=self.org,
            code="FAC-CAP-01",
            name="North Turbine Station",
        )
        self.category = AssetCategory.objects.create(
            organization=self.org,
            name="Turbines",
        )
        self.asset = Asset.objects.create(
            organization=self.org,
            site=self.site,
            category=self.category,
            asset_tag="AST-TRB-400",
            name="Primary Gas Turbine Unit",
        )

        # 3. Roles and Memberships
        planner_user = User.objects.create_user(
            email="planner@capacity.test",
            password="TestPassword123!",
            full_name="Perry Planner",
        )
        self.planner = Membership.objects.create(
            organization=self.org,
            user=planner_user,
            status=Membership.Status.ACTIVE,
        )
        planner_role = rbac.system_role_by_key(self.org, "maintenance_planner")
        rbac.set_membership_roles(self.planner, [planner_role], actor=None, system=True)

        tech_user = User.objects.create_user(
            email="tech@capacity.test",
            password="TestPassword123!",
            full_name="Terry Tech",
        )
        self.technician = Membership.objects.create(
            organization=self.org,
            user=tech_user,
            status=Membership.Status.ACTIVE,
        )
        tech_role = rbac.system_role_by_key(self.org, "technician")
        rbac.set_membership_roles(self.technician, [tech_role], actor=None, system=True)

        other_user = User.objects.create_user(
            email="other.tech@capacity.test",
            password="TestPassword123!",
            full_name="Other Tech",
        )
        self.other_technician = Membership.objects.create(
            organization=self.org,
            user=other_user,
            status=Membership.Status.ACTIVE,
        )
        rbac.set_membership_roles(self.other_technician, [tech_role], actor=None, system=True)

        # 4. Work Order assigned to technician
        self.work_order = wo_services.create_work_order(
            self.org,
            site_id=self.site.pk,
            asset=self.asset,
            actor=self.planner.user,
            title="Service Gas Turbine Lubrication Line",
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
            planned_end=t_start + timedelta(hours=4),
            estimated_hours=Decimal("4.00"),
            due_at=t_start + timedelta(days=1),
        )
        self.work_order = wo_services.assign_work_order(
            self.work_order,
            membership=self.planner,
            actor=self.planner.user,
            assignee_id=self.technician.pk,
            scheduled_start=t_start,
            scheduled_end=t_start + timedelta(hours=4),
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

    def test_capacity_summary_defaults_zero(self):
        """When no labor is logged today, gauge returns 0h logged and 0% utilization."""
        today = timezone.localdate()
        summary = selectors.daily_capacity_summary(self.technician, target_date=today)

        self.assertEqual(summary["date"], today.isoformat())
        self.assertEqual(summary["standard_capacity_hours"], 8.0)
        self.assertEqual(summary["total_logged_hours"], 0.0)
        self.assertEqual(summary["direct_wrench_hours"], 0.0)
        self.assertEqual(summary["travel_hours"], 0.0)
        self.assertEqual(summary["remaining_hours"], 8.0)
        self.assertEqual(summary["utilization_pct"], 0.0)
        self.assertEqual(summary["progress_pct"], 0.0)
        self.assertFalse(summary["is_overtime"])

    def test_capacity_summary_partial_labor(self):
        """When 4.0 hours are logged today, utilization reflects 50.0%."""
        today = timezone.localdate()
        ws_services.record_job_labor(
            self.work_order,
            membership=self.technician,
            actor=self.technician.user,
            hours=Decimal("4.00"),
            description="Replaced turbine oil filter",
            work_date=today,
        )

        summary = selectors.daily_capacity_summary(self.technician, target_date=today)
        self.assertEqual(summary["total_logged_hours"], 4.0)
        self.assertEqual(summary["direct_wrench_hours"], 4.0)
        self.assertEqual(summary["travel_hours"], 0.0)
        self.assertEqual(summary["remaining_hours"], 4.0)
        self.assertEqual(summary["utilization_pct"], 50.0)
        self.assertEqual(summary["progress_pct"], 50.0)
        self.assertFalse(summary["is_overtime"])

    def test_capacity_summary_full_day(self):
        """When 8.0 hours are logged, utilization is 100.0% with 0h remaining."""
        today = timezone.localdate()
        ws_services.record_job_labor(
            self.work_order,
            membership=self.technician,
            actor=self.technician.user,
            hours=Decimal("8.00"),
            description="Complete overhaul inspection",
            work_date=today,
        )

        summary = selectors.daily_capacity_summary(self.technician, target_date=today)
        self.assertEqual(summary["total_logged_hours"], 8.0)
        self.assertEqual(summary["remaining_hours"], 0.0)
        self.assertEqual(summary["utilization_pct"], 100.0)
        self.assertEqual(summary["progress_pct"], 100.0)
        self.assertFalse(summary["is_overtime"])

    def test_capacity_summary_overtime(self):
        """When logged hours exceed 8.0h, utilization exceeds 100%, progress is capped at 100%, and is_overtime is True."""
        today = timezone.localdate()
        ws_services.record_job_labor(
            self.work_order,
            membership=self.technician,
            actor=self.technician.user,
            hours=Decimal("10.00"),
            description="Emergency shutdown repairs",
            work_date=today,
        )

        summary = selectors.daily_capacity_summary(self.technician, target_date=today)
        self.assertEqual(summary["total_logged_hours"], 10.0)
        self.assertEqual(summary["remaining_hours"], 0.0)
        self.assertEqual(summary["utilization_pct"], 125.0)
        self.assertEqual(summary["progress_pct"], 100.0)
        self.assertTrue(summary["is_overtime"])

    def test_capacity_summary_with_travel_logs(self):
        """Transit travel duration and direct labor hours are correctly broken down."""
        today = timezone.localdate()
        # 1.5h travel
        ws_services.log_travel(
            self.work_order,
            membership=self.technician,
            actor=self.technician.user,
            duration_hours=Decimal("1.50"),
            distance_km=Decimal("35.0"),
            route_details="Highway 10 to North Site",
            record_labor_entry=True,
            work_date=today,
        )
        # 4.0h direct labor
        ws_services.record_job_labor(
            self.work_order,
            membership=self.technician,
            actor=self.technician.user,
            hours=Decimal("4.00"),
            description="Turbine calibration",
            work_date=today,
        )

        summary = selectors.daily_capacity_summary(self.technician, target_date=today)
        self.assertEqual(summary["total_logged_hours"], 5.5)
        self.assertEqual(summary["travel_hours"], 1.5)
        self.assertEqual(summary["direct_wrench_hours"], 4.0)
        self.assertEqual(summary["remaining_hours"], 2.5)
        self.assertEqual(summary["utilization_pct"], 68.8)
        self.assertFalse(summary["is_overtime"])

    def test_capacity_summary_different_dates_isolation(self):
        """Labor logged on other days does not affect today's capacity summary."""
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        ws_services.record_job_labor(
            self.work_order,
            membership=self.technician,
            actor=self.technician.user,
            hours=Decimal("6.00"),
            description="Yesterday's work",
            work_date=yesterday,
        )

        summary_today = selectors.daily_capacity_summary(self.technician, target_date=today)
        self.assertEqual(summary_today["total_logged_hours"], 0.0)

        summary_yesterday = selectors.daily_capacity_summary(self.technician, target_date=yesterday)
        self.assertEqual(summary_yesterday["total_logged_hours"], 6.0)

    def test_capacity_summary_technician_isolation(self):
        """Labor logged by another technician is never mixed into this technician's capacity."""
        today = timezone.localdate()
        WorkOrderLabor.objects.create(
            organization=self.org,
            work_order=self.work_order,
            technician=self.other_technician,
            entered_by=self.other_technician.user,
            hours=Decimal("7.50"),
            description="Other tech work",
            work_date=today,
        )

        summary = selectors.daily_capacity_summary(self.technician, target_date=today)
        self.assertEqual(summary["total_logged_hours"], 0.0)

    def test_ui_my_jobs_renders_capacity_card(self):
        """GET workspace:my_jobs renders the capacity gauge card with live data."""
        from django.urls import reverse
        today = timezone.localdate()
        ws_services.record_job_labor(
            self.work_order,
            membership=self.technician,
            actor=self.technician.user,
            hours=Decimal("4.00"),
            description="Turbine inspect",
            work_date=today,
        )

        response = self.ui_client.get(reverse("workspace:my_jobs"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("dailyCapacityCard", content)
        self.assertIn("Today's Workday Capacity", content)
        self.assertIn("4.0h", content)
        self.assertIn("8.0h", content)
        self.assertIn("50.0%", content)
        self.assertIn("Full Analytics", content)

    def test_api_technician_capacity_get_today(self):
        """GET /api/v1/workspace/capacity/ returns 200 with current day capacity metrics."""
        today = timezone.localdate()
        ws_services.record_job_labor(
            self.work_order,
            membership=self.technician,
            actor=self.technician.user,
            hours=Decimal("5.00"),
            description="Field repair",
            work_date=today,
        )

        response = self.api_client.get("/api/v1/workspace/capacity/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["date"], today.isoformat())
        self.assertEqual(data["standard_capacity_hours"], 8.0)
        self.assertEqual(data["total_logged_hours"], 5.0)
        self.assertEqual(data["remaining_hours"], 3.0)
        self.assertEqual(data["utilization_pct"], 62.5)
        self.assertFalse(data["is_overtime"])

    def test_api_technician_capacity_custom_date(self):
        """GET /api/v1/workspace/capacity/?date=YYYY-MM-DD returns capacity for the requested date."""
        target_date = timezone.localdate() - timedelta(days=2)
        ws_services.record_job_labor(
            self.work_order,
            membership=self.technician,
            actor=self.technician.user,
            hours=Decimal("3.00"),
            description="Past job",
            work_date=target_date,
        )

        response = self.api_client.get(f"/api/v1/workspace/capacity/?date={target_date.isoformat()}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["date"], target_date.isoformat())
        self.assertEqual(data["total_logged_hours"], 3.0)
        self.assertEqual(data["remaining_hours"], 5.0)
        self.assertEqual(data["utilization_pct"], 37.5)

    def test_api_technician_capacity_invalid_date(self):
        """GET /api/v1/workspace/capacity/?date=not-a-date returns 400 error."""
        response = self.api_client.get("/api/v1/workspace/capacity/?date=invalid-date")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        data = response.json()
        self.assertEqual(data["error"]["code"], "invalid_date")

    def test_api_my_jobs_includes_capacity_object(self):
        """GET /api/v1/workspace/jobs/ includes the capacity object in the response payload."""
        today = timezone.localdate()
        ws_services.record_job_labor(
            self.work_order,
            membership=self.technician,
            actor=self.technician.user,
            hours=Decimal("6.00"),
            description="Diagnostic check",
            work_date=today,
        )

        response = self.api_client.get("/api/v1/workspace/jobs/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn("capacity", data)
        self.assertEqual(data["capacity"]["total_logged_hours"], 6.0)
        self.assertEqual(data["capacity"]["standard_capacity_hours"], 8.0)
        self.assertEqual(data["capacity"]["utilization_pct"], 75.0)
