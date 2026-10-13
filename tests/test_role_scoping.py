"""Tests for FON-M01 Site-Scoped Role Filtering and RBAC delegation."""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.test import Client, TestCase

from apps.core.exceptions import PermissionDenied, ValidationFailed
from apps.rbac import services as rbac
from apps.rbac.models import MembershipRole, Permission, Role
from apps.sites import services as site_services
from apps.tenancy import forms as tenancy_forms
from apps.tenancy import services as tenancy
from apps.tenancy.models import Membership

User = get_user_model()
PASSWORD = "Test-Password-123!"


class RoleScopingTests(TestCase):
    def setUp(self):
        # Platform admin
        self.platform_admin = User.objects.create_user(
            email="root@platform.test", password=PASSWORD, full_name="Root Admin", is_platform_admin=True
        )

        # Tenant Org A
        self.org_a, self.owner_m_a = tenancy.create_organization(
            name="Alpha Org", owner_email="owner@alpha.test", owner_name="Alpha Owner", actor=self.platform_admin
        )
        self.owner_m_a.status = Membership.Status.ACTIVE
        self.owner_m_a.save(update_fields=["status"])
        self.owner_a = self.owner_m_a.user
        self.owner_a.set_password(PASSWORD)
        self.owner_a.save()

        # Tenant Org B (for cross-tenant checks)
        self.org_b, self.owner_m_b = tenancy.create_organization(
            name="Beta Org", owner_email="owner@beta.test", owner_name="Beta Owner", actor=self.platform_admin
        )
        self.owner_m_b.status = Membership.Status.ACTIVE
        self.owner_m_b.save(update_fields=["status"])

        # Sites for Org A
        self.site_a1 = site_services.create_site(self.org_a, actor=self.owner_a, code="SITE-A1", name="Alpha Site 1")
        self.site_a2 = site_services.create_site(self.org_a, actor=self.owner_a, code="SITE-A2", name="Alpha Site 2")

        # Site for Org B
        self.site_b1 = site_services.create_site(self.org_b, actor=self.owner_m_b.user, code="SITE-B1", name="Beta Site 1")

        # System roles for Org A
        self.owner_role = rbac.system_role_by_key(self.org_a, "owner")
        self.admin_role = rbac.system_role_by_key(self.org_a, "admin")
        self.tech_role = rbac.system_role_by_key(self.org_a, "technician")
        self.supervisor_role = rbac.system_role_by_key(self.org_a, "supervisor")
        self.auditor_role = rbac.system_role_by_key(self.org_a, "auditor")

        # System roles for Org B
        self.role_b_tech = rbac.system_role_by_key(self.org_b, "technician")

        # Target member in Org A
        self.target_user = User.objects.create_user(email="target@alpha.test", password=PASSWORD, full_name="Target User")
        self.target_m = Membership.objects.create(organization=self.org_a, user=self.target_user, status=Membership.Status.ACTIVE)
        rbac.set_membership_roles(self.target_m, [self.tech_role], actor=None, system=True)

        # 1. Organization-wide Admin
        self.admin_user = User.objects.create_user(email="admin@alpha.test", password=PASSWORD, full_name="Admin User")
        self.admin_m = Membership.objects.create(organization=self.org_a, user=self.admin_user, status=Membership.Status.ACTIVE)
        rbac.set_membership_roles(self.admin_m, [self.admin_role], actor=None, system=True)

        # 2. Site-scoped Admin (granted auditor_role org-wide for user.view, and admin_role only on site_a1)
        self.site_admin_user = User.objects.create_user(email="siteadmin@alpha.test", password=PASSWORD, full_name="Site Admin")
        self.site_admin_m = Membership.objects.create(organization=self.org_a, user=self.site_admin_user, status=Membership.Status.ACTIVE)
        rbac.set_membership_assignments(
            self.site_admin_m,
            [(self.auditor_role, None), (self.admin_role, self.site_a1)],
            actor=None,
            system=True,
        )

        # 3. User with site.update alone (no user.update authority)
        self.site_editor_user = User.objects.create_user(email="editor@alpha.test", password=PASSWORD, full_name="Site Editor")
        self.site_editor_m = Membership.objects.create(organization=self.org_a, user=self.site_editor_user, status=Membership.Status.ACTIVE)
        # Asset manager has site.update, but NO user.update
        self.asset_mgr_role = rbac.system_role_by_key(self.org_a, "asset_manager")
        rbac.set_membership_roles(self.site_editor_m, [self.asset_mgr_role], actor=None, system=True)

        # 4. Ordinary Technician (no user.update, no site.update)
        self.tech_user = User.objects.create_user(email="tech@alpha.test", password=PASSWORD, full_name="Tech Guy")
        self.tech_m = Membership.objects.create(organization=self.org_a, user=self.tech_user, status=Membership.Status.ACTIVE)
        rbac.set_membership_roles(self.tech_m, [self.tech_role], actor=None, system=True)

        self.client = Client()

    # --- 1. Organization Owner Permissions ---

    def test_owner_form_options(self):
        form = tenancy_forms.RoleAssignmentForm(org=self.org_a, membership=self.owner_m_a)
        # Owner can see all sites and org-wide option
        self.assertFalse(form.fields["site"].required)
        self.assertEqual(form.fields["site"].empty_label, "All sites (organization-wide)")
        site_pks = set(form.fields["site"].queryset.values_list("pk", flat=True))
        self.assertEqual(site_pks, {self.site_a1.pk, self.site_a2.pk})
        # Owner can assign Owner role
        role_pks = set(form.fields["role"].queryset.values_list("pk", flat=True))
        self.assertIn(self.owner_role.pk, role_pks)

    def test_owner_can_assign_orgwide_and_site_roles(self):
        # Assign org-wide supervisor
        rbac.set_membership_assignments(
            self.target_m, [(self.tech_role, None), (self.supervisor_role, None)], actor=self.owner_a
        )
        self.assertEqual(self.target_m.membership_roles.count(), 2)

        # Assign site-scoped role
        rbac.set_membership_assignments(
            self.target_m, [(self.tech_role, self.site_a1)], actor=self.owner_a
        )
        mr = self.target_m.membership_roles.first()
        self.assertEqual(mr.site, self.site_a1)

    # --- 2. Organization-wide Authorized Administrators ---

    def test_org_admin_form_options(self):
        form = tenancy_forms.RoleAssignmentForm(org=self.org_a, membership=self.admin_m)
        self.assertFalse(form.fields["site"].required)
        self.assertEqual(form.fields["site"].empty_label, "All sites (organization-wide)")
        # All sites in org are available
        site_pks = set(form.fields["site"].queryset.values_list("pk", flat=True))
        self.assertEqual(site_pks, {self.site_a1.pk, self.site_a2.pk})
        # Owner role is excluded for non-owners
        role_pks = set(form.fields["role"].queryset.values_list("pk", flat=True))
        self.assertNotIn(self.owner_role.pk, role_pks)
        self.assertIn(self.tech_role.pk, role_pks)

    def test_org_admin_cannot_assign_owner_role(self):
        # Form validation rejects owner role
        form = tenancy_forms.RoleAssignmentForm(
            {"role": str(self.owner_role.pk), "site": ""}, org=self.org_a, membership=self.admin_m
        )
        self.assertFalse(form.is_valid())
        self.assertIn("role", form.errors)

        # Backend service rejects owner role
        with self.assertRaises(PermissionDenied) as ctx:
            rbac.set_membership_assignments(
                self.target_m, [(self.owner_role, None)], actor=self.admin_user
            )
        self.assertEqual(ctx.exception.code, "owner_only")

    # --- 3. Site-scoped Administrators ---

    def test_site_admin_form_options(self):
        form = tenancy_forms.RoleAssignmentForm(org=self.org_a, membership=self.site_admin_m)
        # Site field must be required and empty_label should not offer org-wide
        self.assertTrue(form.fields["site"].required)
        self.assertEqual(form.fields["site"].empty_label, "Select a site")
        # Only site_a1 should be in the queryset
        site_pks = list(form.fields["site"].queryset.values_list("pk", flat=True))
        self.assertEqual(site_pks, [self.site_a1.pk])
        # Owner role excluded
        role_pks = set(form.fields["role"].queryset.values_list("pk", flat=True))
        self.assertNotIn(self.owner_role.pk, role_pks)

    def test_site_admin_cannot_assign_orgwide(self):
        # Form rejects org-wide assignment
        form = tenancy_forms.RoleAssignmentForm(
            {"role": str(self.tech_role.pk), "site": ""}, org=self.org_a, membership=self.site_admin_m
        )
        self.assertFalse(form.is_valid())
        self.assertIn("site", form.errors)

        # Backend service rejects org-wide assignment
        with self.assertRaises(PermissionDenied) as ctx:
            rbac.set_membership_assignments(
                self.target_m, [(self.tech_role, None), (self.supervisor_role, None)], actor=self.site_admin_user
            )
        self.assertEqual(ctx.exception.code, "site_scope_exceeded")

    def test_site_admin_cannot_assign_unauthorized_site(self):
        # Form rejects site_a2
        form = tenancy_forms.RoleAssignmentForm(
            {"role": str(self.tech_role.pk), "site": str(self.site_a2.pk)},
            org=self.org_a,
            membership=self.site_admin_m,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("site", form.errors)

        # Backend service rejects site_a2
        with self.assertRaises(PermissionDenied) as ctx:
            rbac.set_membership_assignments(
                self.target_m, [(self.tech_role, self.site_a2)], actor=self.site_admin_user
            )
        self.assertEqual(ctx.exception.code, "unauthorized_site")

    def test_site_admin_can_assign_authorized_site_role(self):
        # Form is valid for site_a1 with a role within site admin's authority (auditor_role)
        form = tenancy_forms.RoleAssignmentForm(
            {"role": str(self.auditor_role.pk), "site": str(self.site_a1.pk)},
            org=self.org_a,
            membership=self.site_admin_m,
        )
        self.assertTrue(form.is_valid(), form.errors)

        # Backend service succeeds
        rbac.set_membership_assignments(
            self.target_m, [(self.tech_role, None), (self.auditor_role, self.site_a1)], actor=self.site_admin_user
        )
        self.assertEqual(self.target_m.membership_roles.filter(site=self.site_a1).count(), 1)

    # --- 4. Users Without Role-Assignment Permissions ---

    def test_user_with_site_update_alone_cannot_assign_roles(self):
        # Asset manager holds site.update but NOT user.update
        self.assertTrue(rbac.has_permission(self.site_editor_m, "site.update"))
        self.assertFalse(rbac.has_permission(self.site_editor_m, "user.update"))
        self.assertFalse(rbac.has_role_assignment_permission(self.site_editor_m))

        # Form has empty choices
        form = tenancy_forms.RoleAssignmentForm(org=self.org_a, membership=self.site_editor_m)
        self.assertEqual(form.fields["site"].queryset.count(), 0)
        self.assertEqual(form.fields["role"].queryset.count(), 0)

        # Backend service rejects
        with self.assertRaises(PermissionDenied) as ctx:
            rbac.set_membership_assignments(
                self.target_m, [(self.tech_role, self.site_a1)], actor=self.site_editor_user
            )
        self.assertEqual(ctx.exception.code, "permission_denied")

    def test_technician_cannot_assign_roles(self):
        self.assertFalse(rbac.has_role_assignment_permission(self.tech_m))
        form = tenancy_forms.RoleAssignmentForm(org=self.org_a, membership=self.tech_m)
        self.assertEqual(form.fields["site"].queryset.count(), 0)
        self.assertEqual(form.fields["role"].queryset.count(), 0)

        with self.assertRaises(PermissionDenied) as ctx:
            rbac.set_membership_assignments(
                self.target_m, [(self.tech_role, self.site_a1)], actor=self.tech_user
            )
        self.assertEqual(ctx.exception.code, "permission_denied")

    # --- 5. Cross-Tenant Protection ---

    def test_cross_tenant_role_rejected(self):
        with self.assertRaises(ValidationFailed) as ctx:
            rbac.set_membership_assignments(
                self.target_m, [(self.role_b_tech, None)], actor=self.owner_a
            )
        self.assertEqual(ctx.exception.code, "cross_tenant_role")

    def test_cross_tenant_site_rejected(self):
        with self.assertRaises(ValidationFailed) as ctx:
            rbac.set_membership_assignments(
                self.target_m, [(self.tech_role, self.site_b1)], actor=self.owner_a
            )
        self.assertEqual(ctx.exception.code, "cross_tenant_site")

    # --- 6. Privilege Escalation Prevention ---

    def test_cannot_assign_roles_with_excess_permissions(self):
        # Create a custom role with a site-scoped permission that site_admin does not have (e.g. work_order.start)
        from apps.rbac.models import RolePermission
        role_super = Role.objects.create(organization=self.org_a, name="Super Role", system_key="custom_super")
        perm_codes = rbac.catalog.expand(["work_order.start"])
        for code in perm_codes:
            p_obj = Permission.objects.get(code=code)
            RolePermission.objects.create(role=role_super, permission=p_obj)

        with self.assertRaises(PermissionDenied) as ctx:
            rbac.set_membership_assignments(
                self.target_m, [(role_super, self.site_a1)], actor=self.site_admin_user
            )
        self.assertEqual(ctx.exception.code, "privilege_escalation")

    # --- 7. Role Removal Workflows ---

    def test_role_removal_preservation(self):
        # Target starts with tech_role
        mrs = list(self.target_m.membership_roles.all())
        self.assertEqual(len(mrs), 1)

        # Add supervisor role
        tenancy.add_role_assignment(
            self.target_m, role_id=self.supervisor_role.pk, site_id=self.site_a1.pk, actor=self.owner_a
        )
        self.assertEqual(self.target_m.membership_roles.count(), 2)

        # Remove the tech_role assignment
        tenancy.remove_role_assignment(
            self.target_m, assignment_id=mrs[0].pk, actor=self.owner_a
        )
        self.assertEqual(self.target_m.membership_roles.count(), 1)
        self.assertEqual(self.target_m.membership_roles.first().role, self.supervisor_role)

    def test_non_owner_cannot_remove_owner_role(self):
        # Give target an owner role (owner_m_a giving to target)
        rbac.set_membership_assignments(
            self.target_m, [(self.owner_role, None), (self.tech_role, None)], actor=self.owner_a
        )
        owner_mr = self.target_m.membership_roles.get(role=self.owner_role)

        # Admin tries to remove owner role
        with self.assertRaises(PermissionDenied) as ctx:
            tenancy.remove_role_assignment(
                self.target_m, assignment_id=owner_mr.pk, actor=self.admin_user
            )
        self.assertEqual(ctx.exception.code, "owner_only")

    # --- 8. UI MemberDetailView POST Enforcement ---

    def test_ui_unauthorized_user_post_assign_role_denied(self):
        self.client.force_login(self.tech_user)
        url = f"/app/users/{self.target_m.pk}/"
        response = self.client.post(
            url,
            {"action": "assign_role", "role": str(self.supervisor_role.pk), "site": str(self.site_a1.pk)},
        )
        # Should raise PermissionDenied -> 403
        self.assertEqual(response.status_code, 403)

    def test_ui_site_admin_post_unauthorized_site_rejected(self):
        self.client.force_login(self.site_admin_user)
        url = f"/app/users/{self.target_m.pk}/"
        response = self.client.post(
            url,
            {"action": "assign_role", "role": str(self.auditor_role.pk), "site": str(self.site_a2.pk)},
        )
        # Re-renders form with error (200), not assigned
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.target_m.membership_roles.filter(site=self.site_a2).exists())

    def test_ui_site_admin_post_authorized_site_success(self):
        self.client.force_login(self.site_admin_user)
        url = f"/app/users/{self.target_m.pk}/"
        response = self.client.post(
            url,
            {"action": "assign_role", "role": str(self.auditor_role.pk), "site": str(self.site_a1.pk)},
        )
        # Redirects on success (302)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.target_m.membership_roles.filter(site=self.site_a1, role=self.auditor_role).exists())


class CalendarHolidayTests(TestCase):
    def setUp(self):
        import datetime
        self.platform_admin = User.objects.create_user(
            email="root2@platform.test", password=PASSWORD, full_name="Root Admin", is_platform_admin=True
        )
        self.org, self.owner_m = tenancy.create_organization(
            name="Holiday Org", owner_email="owner@holiday.test", owner_name="Holiday Owner", actor=self.platform_admin
        )
        self.owner_m.status = Membership.Status.ACTIVE
        self.owner_m.save(update_fields=["status"])
        self.owner = self.owner_m.user
        self.owner.set_password(PASSWORD)
        self.owner.save()

        self.site = site_services.create_site(self.org, actor=self.owner, code="H-SITE", name="Holiday Site")
        self.cal = site_services.create_calendar(self.site, actor=self.owner, name="Standard Cal", is_24x7=True)

        self.tech_user = User.objects.create_user(email="tech@holiday.test", password=PASSWORD, full_name="Tech")
        self.tech_m = Membership.objects.create(organization=self.org, user=self.tech_user, status=Membership.Status.ACTIVE)
        rbac.set_membership_roles(self.tech_m, [rbac.system_role_by_key(self.org, "technician")], actor=None, system=True)

        self.client = Client()

    def test_calendar_holiday_add_duplicate_and_remove(self):
        import datetime
        from apps.core.exceptions import Conflict

        h_date = datetime.date(2026, 12, 25)
        holiday = site_services.add_holiday(self.cal, date=h_date, name="Christmas", actor=self.owner)
        self.assertEqual(holiday.name, "Christmas")
        self.assertEqual(holiday.date, h_date)

        # Duplicate date is rejected
        with self.assertRaises(Conflict) as ctx:
            site_services.add_holiday(self.cal, date=h_date, name="Duplicate Christmas", actor=self.owner)
        self.assertEqual(ctx.exception.code, "duplicate_holiday")

        # Removal succeeds
        site_services.remove_holiday(holiday, actor=self.owner)
        self.assertEqual(self.cal.holidays.count(), 0)

    def test_calendar_holiday_unauthorized_user_forbidden(self):
        self.client.force_login(self.tech_user)
        url = f"/app/calendars/{self.cal.pk}/holidays/"
        response = self.client.post(url, {"date": "2026-01-01", "name": "New Year"})
        self.assertEqual(response.status_code, 403)

