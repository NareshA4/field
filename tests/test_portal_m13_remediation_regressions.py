from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import Client
from rest_framework import status

from apps.core.exceptions import Conflict
from apps.incidents.models import ServiceRequest
from apps.incidents.services import _notify_reporter, create_request
from apps.notifications.models import Notification
from apps.portal.models import PortalAccount, PortalClosureConfirmation
from apps.portal.selectors import get_portal_dashboard_metrics
from apps.portal.services import disable_portal_account, enable_portal_account
from apps.rbac import services as rbac
from apps.rbac.models import MembershipRole
from apps.tenancy.models import Membership, Organization

User = get_user_model()
PASSWORD = "Password123!"


@pytest.fixture
def make_remediation_client(db):
    def _make(org, email, company_name="Beta Client Co"):
        user = User.objects.filter(email=email).first() or User.objects.create_user(
            email=email, password=PASSWORD, full_name="Client Test User"
        )
        mem, _ = Membership.objects.unscoped().get_or_create(
            organization=org,
            user=user,
            defaults={"status": Membership.Status.ACTIVE, "job_title": "Client"},
        )
        role = rbac.system_role_by_key(org, "client_requester")
        rbac.set_membership_roles(mem, [role], actor=None, system=True)
        acc, _ = PortalAccount.objects.unscoped().get_or_create(
            organization=org,
            membership=mem,
            defaults={"company_name": company_name, "is_enabled": True},
        )
        return user, mem, acc

    return _make


# Regression Test 1: Defect 1 - Client redirect to portal:dashboard vs staff home
def test_regression_defect_1_client_redirect_and_staff_preserved(client, org_a, make_remediation_client, make_member):
    client_user, _, _ = make_remediation_client(org_a, "client.redir.reg@test.com")
    staff_user = make_member(org_a, "staff.home.reg@test.com", "technician").user

    # Client user lands on portal dashboard
    client.force_login(client_user)
    r_client = client.get("/app/")
    assert r_client.status_code == 302
    assert r_client["Location"] == "/app/portal/"

    # Staff user lands on internal staff home view (200 OK)
    client.force_login(staff_user)
    r_staff = client.get("/app/")
    assert r_staff.status_code == 200
    assert "Active Members" in r_staff.content.decode() or "Roles" in r_staff.content.decode()


# Regression Test 2: Defect 2 - Portal account toggle allows client_requester incident permissions
def test_regression_defect_2_portal_account_toggle_permissions(org_a, make_remediation_client, make_member):
    _, client_mem, client_acc = make_remediation_client(org_a, "client.toggle.reg@test.com")

    # Disabling and enabling client account succeeds without Conflict
    disable_portal_account(client_acc)
    client_acc.refresh_from_db()
    assert client_acc.is_enabled is False

    enable_portal_account(client_acc)
    client_acc.refresh_from_db()
    assert client_acc.is_enabled is True

    # Staff member with technician permissions cannot have portal account enabled
    staff_mem = make_member(org_a, "staff.illegal.portal@test.com", "technician")
    illegal_acc = PortalAccount.objects.create(
        organization=org_a,
        membership=staff_mem,
        company_name="Illegal Staff Portal",
        is_enabled=False,
    )
    with pytest.raises(Conflict) as exc_info:
        enable_portal_account(illegal_acc)
    assert "Only client-only members" in str(exc_info.value)


# Regression Test 3: Defect 3 - Acme Corporation fallback eliminated
def test_regression_defect_3_no_acme_hardcoded_fallback(client, org_a, make_remediation_client):
    # Company name is empty string
    client_user, _, acc = make_remediation_client(org_a, "client.noacme@test.com", company_name="")
    acc.company_name = ""
    acc.save(update_fields=["company_name"])

    client.force_login(client_user)
    r = client.get("/app/portal/")
    assert r.status_code == 200
    html = r.content.decode()

    # Must display organization name instead of Acme Corporation
    assert "Acme Corporation" not in html
    assert org_a.name in html


# Regression Test 4: Defect 4 - Notification links route to /app/portal/requests/ for clients
def test_regression_defect_4_notification_routing(org_a, make_remediation_client, make_member, make_asset, site_a1):
    client_user, client_mem, client_acc = make_remediation_client(org_a, "client.notif.reg@test.com")
    staff_mem = make_member(org_a, "staff.notif.reg@test.com", "supervisor")
    asset = make_asset(org_a, site_a1, "CHILLER-REG-01")

    from apps.portal.services import create_portal_service_request, grant_portal_asset
    grant_portal_asset(client_acc, asset)

    # Client request notification
    sr_client = create_portal_service_request(
        client_mem,
        asset=asset,
        title="Client Notification Link Check",
    )
    _notify_reporter(sr_client, "resolve", "Work finished")
    notif_client = Notification.objects.filter(recipient=client_user, organization=org_a).order_by("-created_at").first()
    assert notif_client is not None
    assert notif_client.link == f"/app/portal/requests/{sr_client.pk}/"

    # Staff request notification
    sr_staff = create_request(
        org=org_a,
        asset=asset,
        reporter=staff_mem,
        title="Staff Incident Notification Link Check",
        actor=staff_mem.user,
    )
    _notify_reporter(sr_staff, "resolve", "Staff fix applied")
    notif_staff = Notification.objects.filter(recipient=staff_mem.user, organization=org_a).order_by("-created_at").first()
    assert notif_staff is not None
    assert notif_staff.link == f"/app/incidents/{sr_staff.pk}/"


# Regression Test 5: Defect 5 - Database-backed KPI metrics selector accuracy
def test_regression_defect_5_dashboard_kpis_accuracy(org_a, make_remediation_client, make_asset, site_a1):
    client_user, client_mem, client_acc = make_remediation_client(org_a, "client.kpi.reg@test.com")
    asset = make_asset(org_a, site_a1, "PUMP-REG-01")

    from apps.portal.services import create_portal_service_request, grant_portal_asset
    grant_portal_asset(client_acc, asset)

    # Initial state: 0
    m_initial = get_portal_dashboard_metrics(client_mem)
    assert m_initial["total"] == 0
    assert m_initial["open"] == 0

    # Create 1 Open request
    sr1 = create_portal_service_request(client_mem, asset=asset, title="Vibration Alarm")
    m1 = get_portal_dashboard_metrics(client_mem)
    assert m1["total"] == 1
    assert m1["open"] == 1
    assert m1["in_progress"] == 0

    # Resolve request -> Awaiting confirmation
    sr1.status = "RESOLVED"
    sr1.save(update_fields=["status"])
    m2 = get_portal_dashboard_metrics(client_mem)
    assert m2["awaiting_confirmation"] == 1

    # Record closure confirmation rating
    PortalClosureConfirmation.objects.create(
        organization=org_a,
        request=sr1,
        portal_account=client_acc,
        confirmed_by=client_user,
        rating=5,
        feedback="Outstanding service and speed",
    )
    m3 = get_portal_dashboard_metrics(client_mem)
    assert m3["avg_rating"] == 5.0
    assert m3["rating_count"] == 1


# Regression Test 6: Defect 6 - Sidebar navigation filtering and backend protection
def test_regression_defect_6_client_navigation_and_cockpit_protection(client, org_a, make_remediation_client):
    client_user, _, _ = make_remediation_client(org_a, "client.nav.reg@test.com")
    client.force_login(client_user)

    # Client directly hitting My Work Cockpit gets 403 Forbidden
    r_cockpit = client.get("/app/dashboards/my-work/")
    assert r_cockpit.status_code == 403

    # Client dashboard does not contain links to internal incident dashboard
    r_dash = client.get("/app/portal/")
    assert r_dash.status_code == 200
    html = r_dash.content.decode()
    assert "/app/incidents/dashboard/" not in html
    assert "/app/dashboards/my-work" not in html

    # Client sidebar only contains Portal sub-modules
    sections = r_dash.context["shell_nav_sections"]
    assert "Portal" in sections
    assert "Administration" not in sections
    assert "Analytics" not in sections
    assert "Assets & Locations" not in sections
    assert "Compliance" not in sections
    assert "Coverage" not in sections
    assert "Operations" not in sections
    assert "Overview" not in sections
    portal_items = [item["label"] for item in sections["Portal"]]
    assert "Client Portal" in portal_items
    assert "My Requests" in portal_items
    assert "New Request" in portal_items
    assert "Portal Accounts" not in portal_items
