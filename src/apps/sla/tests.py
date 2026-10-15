import re
from pathlib import Path
from django.test import TestCase, Client

from apps.accounts.models import User
from apps.rbac.services import seed_system_roles
from apps.rbac.models import MembershipRole
from apps.sla.models import SLAProfile, SLATarget
from apps.tenancy.models import Membership, Organization


class SlaProfileFormTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name="Apex Industrial",
            slug="apex-industrial"
        )
        self.roles = seed_system_roles(self.org)
        self.user = User.objects.create_user(
            email="admin@apexindustrial.com",
            password="Password123!",
            full_name="Apex Admin"
        )
        self.membership = Membership.objects.create(
            organization=self.org,
            user=self.user,
            status=Membership.Status.ACTIVE
        )
        # Assign owner role to membership
        MembershipRole.objects.create(
            membership=self.membership,
            role=self.roles["owner"]
        )
        self.client = Client()
        self.client.force_login(self.user)
        # Set active organization in session
        session = self.client.session
        session["tenant_org_id"] = str(self.org.pk)
        session.save()

    def test_new_sla_profile_form_rendering_and_business_terms(self):
        """Verify GET /app/sla/profiles/new/ renders with official business terms and zero module codes."""
        response = self.client.get("/app/sla/profiles/new/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        # Official business terms
        self.assertIn("New SLA Profile", content)
        self.assertIn("Incident / Breakdown", content)
        self.assertIn("Work Order", content)
        self.assertIn("Pause the timers", content)

        # Zero internal module numbers in visible text (e.g. M05, M06, M10, M11, M14)
        visible_text = re.sub(r"<svg[\s\S]*?</svg>", "", content)
        module_number_matches = re.findall(r"\bM\d\d\b", visible_text)
        self.assertEqual(module_number_matches, [], f"Found internal module numbers in UI: {module_number_matches}")

    def test_create_required_example_sla_profile_exact(self):
        """The form must support this exact example:
        Profile name: Critical Breakdown SLA
        Applies to: Incident / Breakdown
        Priority: Critical
        Response target: 15 minutes
        Resolution target: 4 hours
        Warning alert: 75%
        """
        payload = {
            "name": "Critical Breakdown SLA",
            "applies_to": "Incident / Breakdown",
            "priority": "Critical",
            "response_target": "15 minutes",
            "resolution_target": "4 hours",
            "warning_alert": "75%",
            "is_active": "on",
        }
        response = self.client.post("/app/sla/profiles/new/", data=payload, follow=True)
        if response.status_code != 200:
            form = response.context.get("form")
            form_errors = form.errors if form else "no form"
            t_formset = response.context.get("target_formset")
            t_errors = t_formset.errors if t_formset else "no t_formset"
            self.fail(f"Form submission failed: form={form_errors}, targets={t_errors}")
        self.assertEqual(response.status_code, 200)

        # Verify DB persistence
        profile = SLAProfile.objects.for_organization(self.org).filter(name="Critical Breakdown SLA").first()
        self.assertIsNotNone(profile)
        self.assertEqual(profile.applies_to, "REQUEST")
        self.assertTrue(profile.is_active)

        targets = list(profile.targets.all())
        self.assertEqual(len(targets), 1)
        target = targets[0]
        self.assertEqual(target.priority, "CRITICAL")
        self.assertEqual(target.response_minutes, 15)
        self.assertEqual(target.resolution_minutes, 240)  # 4 hours = 240 minutes
        self.assertEqual(target.warning_percent, 75)

        # Verify profile detail page rendering
        detail_resp = self.client.get(f"/app/sla/profiles/{profile.pk}/")
        self.assertEqual(detail_resp.status_code, 200)
        detail_content = detail_resp.content.decode("utf-8")
        self.assertIn("Critical Breakdown SLA", detail_content)
        self.assertIn("Incident / Breakdown", detail_content)
        self.assertIn("CRITICAL", detail_content)
        self.assertIn("15 min", detail_content)
        self.assertIn("240 min", detail_content)
        self.assertIn("75%", detail_content)
        self.assertIn("Response Target", detail_content)
        self.assertIn("Resolution Target", detail_content)
        self.assertIn("Warning Alert", detail_content)

        # Zero internal module numbers in visible detail page
        visible_detail = re.sub(r"<svg[\s\S]*?</svg>", "", detail_content)
        module_matches = re.findall(r"\bM\d\d\b", visible_detail)
        self.assertEqual(module_matches, [], f"Found module numbers: {module_matches}")

    def test_create_via_formset_with_blank_rows_ignored(self):
        """Formset submission where only Critical row is filled and other rows left blank."""
        payload = {
            "name": "Mixed Formset SLA",
            "applies_to": "REQUEST",
            "targets-TOTAL_FORMS": "4",
            "targets-INITIAL_FORMS": "0",
            "targets-MIN_NUM_FORMS": "0",
            "targets-MAX_NUM_FORMS": "4",
            # Row 0: Critical filled
            "targets-0-priority": "CRITICAL",
            "targets-0-response_minutes": "15 minutes",
            "targets-0-resolution_minutes": "4 hours",
            "targets-0-warning_percent": "75%",
            # Rows 1-3: Blank
            "targets-1-priority": "",
            "targets-1-response_minutes": "",
            "targets-1-resolution_minutes": "",
            "targets-1-warning_percent": "80",
            "targets-2-priority": "",
            "targets-2-response_minutes": "",
            "targets-2-resolution_minutes": "",
            "targets-2-warning_percent": "80",
            "targets-3-priority": "",
            "targets-3-response_minutes": "",
            "targets-3-resolution_minutes": "",
            "targets-3-warning_percent": "80",
            "rules-TOTAL_FORMS": "0",
            "rules-INITIAL_FORMS": "0",
            "rules-MIN_NUM_FORMS": "0",
            "rules-MAX_NUM_FORMS": "6",
            "is_active": "on",
        }
        response = self.client.post("/app/sla/profiles/new/", data=payload, follow=True)
        self.assertEqual(response.status_code, 200)

        profile = SLAProfile.objects.for_organization(self.org).filter(name="Mixed Formset SLA").first()
        self.assertIsNotNone(profile)
        self.assertEqual(profile.targets.count(), 1)
        target = profile.targets.first()
        self.assertEqual(target.priority, "CRITICAL")
        self.assertEqual(target.response_minutes, 15)
        self.assertEqual(target.resolution_minutes, 240)
        self.assertEqual(target.warning_percent, 75)

    def test_create_work_order_sla_profile(self):
        """Test creating an SLA Profile for Work Orders."""
        payload = {
            "name": "Emergency Work Order SLA",
            "applies_to": "Work Order",
            "priority": "Urgent",
            "response_target": "30 minutes",
            "resolution_target": "2 hours",
            "warning_alert": "80%",
            "is_active": "on",
        }
        response = self.client.post("/app/sla/profiles/new/", data=payload, follow=True)
        self.assertEqual(response.status_code, 200)

        profile = SLAProfile.objects.for_organization(self.org).filter(name="Emergency Work Order SLA").first()
        self.assertIsNotNone(profile)
        self.assertEqual(profile.applies_to, "WORK_ORDER")
        target = profile.targets.first()
        self.assertEqual(target.priority, "URGENT")
        self.assertEqual(target.response_minutes, 30)
        self.assertEqual(target.resolution_minutes, 120)
        self.assertEqual(target.warning_percent, 80)

    def test_validation_resolution_less_than_response(self):
        """Resolution Target cannot be less than Response Target."""
        payload = {
            "name": "Invalid Timing SLA",
            "applies_to": "Incident / Breakdown",
            "priority": "Critical",
            "response_target": "4 hours",
            "resolution_target": "15 minutes",
            "warning_alert": "75%",
        }
        response = self.client.post("/app/sla/profiles/new/", data=payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SLAProfile.objects.for_organization(self.org).filter(name="Invalid Timing SLA").exists())

    def test_validation_at_least_one_target_required(self):
        """SLA Profile must have at least one priority target."""
        payload = {
            "name": "No Targets SLA",
            "applies_to": "Incident / Breakdown",
            "targets-TOTAL_FORMS": "0",
            "targets-INITIAL_FORMS": "0",
            "targets-MIN_NUM_FORMS": "0",
            "targets-MAX_NUM_FORMS": "4",
            "rules-TOTAL_FORMS": "0",
            "rules-INITIAL_FORMS": "0",
            "rules-MIN_NUM_FORMS": "0",
            "rules-MAX_NUM_FORMS": "6",
        }
        response = self.client.post("/app/sla/profiles/new/", data=payload)
        self.assertEqual(response.status_code, 400)

    def test_target_set_on_detail_page_with_durations(self):
        """Setting a target on the profile detail page accepts duration and percentage strings."""
        profile = SLAProfile.objects.create(
            organization=self.org,
            name="Detail Target SLA",
            applies_to="REQUEST",
            is_active=True
        )
        payload = {
            "priority": "HIGH",
            "response_minutes": "45 minutes",
            "resolution_minutes": "6 hours",
            "warning_percent": "70%",
        }
        response = self.client.post(f"/app/sla/profiles/{profile.pk}/targets/", data=payload, follow=True)
        self.assertEqual(response.status_code, 200)

        target = profile.targets.filter(priority="HIGH").first()
        self.assertIsNotNone(target)
        self.assertEqual(target.response_minutes, 45)
        self.assertEqual(target.resolution_minutes, 360)
        self.assertEqual(target.warning_percent, 70)

    def test_no_module_numbers_in_any_sla_template(self):
        """Verify that zero user-facing SLA templates contain internal module numbers like M05, M06, M11."""
        templates_dir = Path(__file__).resolve().parent.parent.parent / "templates" / "sla"
        for template_file in templates_dir.glob("*.html"):
            content = template_file.read_text(encoding="utf-8")
            matches = re.findall(r"\bM\d\d\b", content)
            self.assertEqual(matches, [], f"Template {template_file.name} contains module numbers: {matches}")

    def test_applies_to_dropdown_options_unique(self):
        """Verify that the 'Applies to' dropdown renders exactly 2 unique options with no duplicate values or labels."""
        response = self.client.get("/app/sla/profiles/new/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        # Find the <select name="applies_to" ...> ... </select>
        match = re.search(r'<select[^>]*name=["\']applies_to["\'][^>]*>([\s\S]*?)</select>', content)
        self.assertIsNotNone(match, "Could not find applies_to select element")
        options = re.findall(r'<option[^>]*value=["\']([^"\']*)["\'][^>]*>([^<]*)</option>', match.group(1))

        # Check values and labels
        values = [val for val, text in options]
        labels = [text.strip() for val, text in options]
        self.assertEqual(len(values), len(set(values)), f"Duplicate applies_to values: {values}")
        self.assertEqual(len(labels), len(set(labels)), f"Duplicate applies_to labels: {labels}")
        self.assertEqual(len(values), 2)
        self.assertIn("REQUEST", values)
        self.assertIn("WORK_ORDER", values)

    def test_duplicate_priority_targets_rejected(self):
        """Submitting two rows with the same priority (e.g. Critical and Critical) raises a clear duplicate error."""
        payload = {
            "name": "Duplicate Priority SLA",
            "applies_to": "REQUEST",
            "targets-TOTAL_FORMS": "2",
            "targets-INITIAL_FORMS": "0",
            "targets-MIN_NUM_FORMS": "0",
            "targets-MAX_NUM_FORMS": "4",
            "targets-0-priority": "CRITICAL",
            "targets-0-response_minutes": "15 minutes",
            "targets-0-resolution_minutes": "4 hours",
            "targets-0-warning_percent": "75%",
            "targets-1-priority": "CRITICAL",
            "targets-1-response_minutes": "30 minutes",
            "targets-1-resolution_minutes": "8 hours",
            "targets-1-warning_percent": "80%",
            "action": "activate",
        }
        response = self.client.post("/app/sla/profiles/new/", data=payload)
        self.assertEqual(response.status_code, 400)
        target_formset = response.context.get("target_formset")
        self.assertTrue(target_formset.non_form_errors())
        error_msg = str(target_formset.non_form_errors()[0])
        self.assertIn("Duplicate priority target detected", error_msg)
        self.assertIn("Critical", error_msg)

    def test_duplicate_scope_conflict_message(self):
        """Creating an active profile with duplicate scope raises clear message indicating existing profile."""
        SLAProfile.objects.create(
            organization=self.org,
            name="Existing Active SLA",
            applies_to="REQUEST",
            is_active=True
        )
        payload = {
            "name": "Another Active SLA",
            "applies_to": "REQUEST",
            "priority": "Critical",
            "response_target": "15 minutes",
            "resolution_target": "4 hours",
            "warning_alert": "75%",
            "action": "activate",
        }
        response = self.client.post("/app/sla/profiles/new/", data=payload)
        self.assertEqual(response.status_code, 400)
        content = response.content.decode("utf-8")
        self.assertIn("Existing Active SLA", content)
        self.assertIn("already covers", content)

    def test_save_as_draft_without_targets(self):
        """Saving with action='draft' creates an inactive draft even when no targets are specified."""
        payload = {
            "name": "Draft Incomplete SLA",
            "applies_to": "REQUEST",
            "action": "draft",
            "targets-TOTAL_FORMS": "0",
            "targets-INITIAL_FORMS": "0",
            "targets-MIN_NUM_FORMS": "0",
            "targets-MAX_NUM_FORMS": "4",
        }
        response = self.client.post("/app/sla/profiles/new/", data=payload, follow=True)
        self.assertEqual(response.status_code, 200)
        profile = SLAProfile.objects.for_organization(self.org).filter(name="Draft Incomplete SLA").first()
        self.assertIsNotNone(profile)
        self.assertFalse(profile.is_active)
        self.assertEqual(profile.targets.count(), 0)

    def test_activate_without_targets_rejected(self):
        """Activating an incomplete profile without priority targets is rejected with a clear error."""
        payload = {
            "name": "Active Incomplete SLA",
            "applies_to": "REQUEST",
            "action": "activate",
            "targets-TOTAL_FORMS": "0",
            "targets-INITIAL_FORMS": "0",
            "targets-MIN_NUM_FORMS": "0",
            "targets-MAX_NUM_FORMS": "4",
        }
        response = self.client.post("/app/sla/profiles/new/", data=payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SLAProfile.objects.for_organization(self.org).filter(name="Active Incomplete SLA").exists())

    def test_run_check_now_standard_post_renders_report(self):
        """POST /app/sla/process/ redirects to /app/sla/trackings/ without hash anchor and displays execution report."""
        response = self.client.post("/app/sla/process/", follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/app/sla/trackings/")

        # Follow redirect to trackings list
        redirect_resp = self.client.get(response.url)
        self.assertEqual(redirect_resp.status_code, 200)
        content = redirect_resp.content.decode("utf-8")
        self.assertIn("SLA Check completed", content)
        self.assertIn("running SLA timer", content)
        self.assertIn("warning alert", content)
        # Verify checklist report card is removed, only message is displayed
        self.assertNotIn("SLA Check Execution Report", content)
        self.assertEqual(content.count("<table"), 1, "There should only be one tracking table, no duplicate second table")

    def test_run_check_now_preserves_filters_and_page(self):
        """POST /app/sla/process/ with next parameter preserves filters and page on redirect."""
        target_url = "/app/sla/trackings/?q=INC-001&status=ACTIVE&page=1"
        response = self.client.post("/app/sla/process/", data={"next": target_url}, follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, target_url)

    def test_run_check_now_ajax_returns_json_report(self):
        """AJAX POST to /app/sla/process/ returns JSON with execution summary."""
        response = self.client.post(
            "/app/sla/process/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("ok"))
        self.assertIn("result", data)
        res = data["result"]
        self.assertIn("checked", res)
        self.assertIn("warnings", res)
        self.assertIn("breaches", res)
        self.assertIn("escalations", res)
        self.assertIn("notifications", res)
        self.assertIn("timestamp", res)

    def test_sla_tracking_search_by_number_and_title(self):
        """Verify search query in SLA tracking works with request number, request title, WO number, and WO title without FieldError."""
        from django.utils import timezone
        from apps.sites.models import Site
        from apps.assets.models import Asset, AssetCategory
        from apps.incidents.models import ServiceRequest
        from apps.workorders.models import WorkOrder
        from apps.sla.models import SLATracking

        site = Site.objects.create(organization=self.org, name="Main Facility", code="MF-01")
        category = AssetCategory.objects.create(organization=self.org, name="Pumps")
        asset = Asset.objects.create(organization=self.org, site=site, category=category, name="Pump 101", serial_number="SN-01")

        req = ServiceRequest.objects.create(
            organization=self.org,
            site=site,
            asset=asset,
            number="INC-9901",
            title="Overheating Hydraulic Pump",
            severity="CRITICAL",
            reported_by=self.membership,
            occurred_at=timezone.now(),
        )

        wo = WorkOrder.objects.create(
            organization=self.org,
            site=site,
            asset=asset,
            created_by=self.user,
            order_number="WO-8802",
            title="Bearing Replacement Routine",
            work_type="CORRECTIVE",
            priority="HIGH",
        )

        profile = SLAProfile.objects.create(
            organization=self.org,
            name="General Policy",
            applies_to="REQUEST",
            is_active=True,
        )

        now = timezone.now()
        SLATracking.objects.create(
            organization=self.org,
            site=site,
            profile=profile,
            request=req,
            priority="CRITICAL",
            response_minutes=15,
            resolution_minutes=60,
            warning_percent=80,
            started_at=now,
            response_due_at=now + timezone.timedelta(minutes=15),
            resolution_due_at=now + timezone.timedelta(minutes=60),
            status=SLATracking.Status.ACTIVE,
        )

        SLATracking.objects.create(
            organization=self.org,
            site=site,
            profile=profile,
            work_order=wo,
            priority="HIGH",
            response_minutes=30,
            resolution_minutes=120,
            warning_percent=80,
            started_at=now,
            response_due_at=now + timezone.timedelta(minutes=30),
            resolution_due_at=now + timezone.timedelta(minutes=120),
            status=SLATracking.Status.ACTIVE,
        )

        # 1. Search by Request Number
        resp1 = self.client.get("/app/sla/trackings/?q=INC-9901")
        self.assertEqual(resp1.status_code, 200)
        self.assertIn("INC-9901", resp1.content.decode("utf-8"))
        self.assertNotIn("WO-8802", resp1.content.decode("utf-8"))

        # 2. Search by Request Title
        resp2 = self.client.get("/app/sla/trackings/?q=Overheating")
        self.assertEqual(resp2.status_code, 200)
        self.assertIn("INC-9901", resp2.content.decode("utf-8"))
        self.assertNotIn("WO-8802", resp2.content.decode("utf-8"))

        # 3. Search by Work Order Number
        resp3 = self.client.get("/app/sla/trackings/?q=WO-8802")
        self.assertEqual(resp3.status_code, 200)
        self.assertIn("WO-8802", resp3.content.decode("utf-8"))
        self.assertNotIn("INC-9901", resp3.content.decode("utf-8"))

        # 4. Search by Work Order Title
        resp4 = self.client.get("/app/sla/trackings/?q=Bearing")
        self.assertEqual(resp4.status_code, 200)
        self.assertIn("WO-8802", resp4.content.decode("utf-8"))
        self.assertNotIn("INC-9901", resp4.content.decode("utf-8"))

    def test_sla_tracking_reset_and_auto_filter_ui_elements(self):
        """Verify the Reset button and automatic filter attributes exist on the tracking page."""
        response = self.client.get("/app/sla/trackings/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        # Reset button
        self.assertIn('id="reset-filter-btn"', content)
        self.assertIn("Reset", content)

        # Automatic filter and table container
        self.assertIn('id="trackings-table-container"', content)
        self.assertIn('id="tracking-filter-form"', content)
        self.assertIn('hx-target="#trackings-table-container"', content)



