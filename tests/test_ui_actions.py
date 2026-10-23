"""Every state-changing UI control must hit a real backend action that persists, audits and enforces rules."""
import pytest
from django.core import mail

from apps.audit.models import AuditLog
from apps.notifications import services as notif
from apps.notifications.models import Notification
from apps.rbac.models import MembershipRole, Role
from apps.tenancy.models import Membership, Organization

pytestmark = pytest.mark.django_db


def _role(org, key):
    return Role.objects.unscoped().get(organization=org, system_key=key)


def test_role_create_edit_delete_via_ui(client, owner_a, org_a):
    client.force_login(owner_a)
    r = client.post("/app/roles/new/", {"name": "Field Lead", "description": "d",
                                        "permissions": ["user.view", "audit.view"]})
    assert r.status_code == 302
    role = Role.objects.unscoped().get(organization=org_a, name="Field Lead")
    assert set(role.role_permissions.values_list("permission__code", flat=True)) == {"user.view", "audit.view"}
    assert client.get(f"/app/roles/{role.pk}/").status_code == 200

    client.post(f"/app/roles/{role.pk}/", {"name": "Field Lead", "description": "x", "permissions": ["user.view"]})
    assert set(role.role_permissions.values_list("permission__code", flat=True)) == {"user.view"}
    assert AuditLog.objects.filter(organization=org_a, action="role.updated").exists()

    client.post(f"/app/roles/{role.pk}/", {"action": "delete"})
    assert not Role.objects.unscoped().filter(pk=role.pk).exists()
    assert AuditLog.objects.filter(organization=org_a, action="role.deleted").exists()


def test_role_duplicate_name_shows_error(client, owner_a, org_a):
    client.force_login(owner_a)
    r = client.post("/app/roles/new/", {"name": "technician", "description": "", "permissions": []})
    assert r.status_code == 200 and "already exists" in r.content.decode()


def test_role_ui_requires_manage_permission(client, org_a, make_member):
    viewer = make_member(org_a, "viewer@alpha.test", "auditor").user  # has role.view, not role.manage
    client.force_login(viewer)
    assert client.get("/app/roles/").status_code == 200
    assert client.get("/app/roles/new/").status_code == 403
    role = _role(org_a, "technician")
    assert client.post(f"/app/roles/{role.pk}/", {"action": "delete"}).status_code == 403


def test_member_update_roles_via_ui(client, owner_a, org_a, tech_a):
    m = Membership.objects.unscoped().get(organization=org_a, user=tech_a)
    client.force_login(owner_a)
    r = client.post(f"/app/users/{m.pk}/", {"action": "update", "full_name": "Tess Tech", "job_title": "Lead"})
    assert r.status_code == 302
    r = client.post(f"/app/users/{m.pk}/", {"action": "assign_role", "role": _role(org_a, "supervisor").pk})
    assert r.status_code == 302
    tech_assignment = MembershipRole.objects.get(membership=m, role__system_key="technician")
    r = client.post(f"/app/users/{m.pk}/", {"action": "remove_role", "assignment_id": tech_assignment.pk})
    assert r.status_code == 302
    m.refresh_from_db()
    tech_a.refresh_from_db()
    assert tech_a.full_name == "Tess Tech" and m.job_title == "Lead"
    assert list(MembershipRole.objects.filter(membership=m).values_list("role__system_key", flat=True)) == ["supervisor"]
    assert AuditLog.objects.filter(organization=org_a, action="membership.roles_changed").exists()


def test_member_deactivate_blocks_access_then_reactivate(client, owner_a, org_a, tech_a):
    m = Membership.objects.unscoped().get(organization=org_a, user=tech_a)
    client.force_login(owner_a)
    client.post(f"/app/users/{m.pk}/", {"action": "deactivate"})
    m.refresh_from_db()
    assert m.status == "SUSPENDED"
    from django.test import Client
    t = Client()
    t.force_login(tech_a)
    assert t.get("/app/").status_code in (302, 403)  # no active membership -> no tenant access
    assert t.get("/app/users/").status_code in (302, 403)
    client.post(f"/app/users/{m.pk}/", {"action": "reactivate"})
    m.refresh_from_db()
    assert m.status == "ACTIVE"
    assert {"user.deactivated", "user.reactivated"} <= set(
        AuditLog.objects.filter(organization=org_a).values_list("action", flat=True))


def test_owner_cannot_deactivate_self_via_ui(client, owner_a, org_a):
    m = Membership.objects.unscoped().get(organization=org_a, user=owner_a)
    client.force_login(owner_a)
    client.post(f"/app/users/{m.pk}/", {"action": "deactivate"})
    m.refresh_from_db()
    assert m.status == "ACTIVE"


def test_resend_invitation_sends_email(client, owner_a, org_a, django_capture_on_commit_callbacks):
    client.force_login(owner_a)
    with django_capture_on_commit_callbacks(execute=True):
        client.post("/app/users/invite/", {"email": "pending@alpha.test", "full_name": "Pen Ding",
                                           "roles": [_role(org_a, "technician").pk]})
    m = Membership.objects.unscoped().get(organization=org_a, user__email="pending@alpha.test")
    assert len(mail.outbox) == 1
    with django_capture_on_commit_callbacks(execute=True):
        client.post(f"/app/users/{m.pk}/", {"action": "resend"})
    assert len(mail.outbox) == 2 and "/accounts/activate/" in mail.outbox[1].body


def test_organization_profile_edit_via_ui(client, owner_a, org_a):
    client.force_login(owner_a)
    r = client.post("/app/organization/", {"name": "Alpha Renamed", "legal_name": "Alpha Pvt Ltd",
                                           "timezone": "Asia/Kolkata", "country": "in", "contact_email": "",
                                           "contact_phone": "", "address": "Plot 1"})
    assert r.status_code == 302
    org = Organization.objects.get(pk=org_a.pk)
    assert org.name == "Alpha Renamed" and org.timezone == "Asia/Kolkata"
    bad = client.post("/app/organization/", {"name": "Alpha Renamed", "timezone": "Mars/Base"})
    assert bad.status_code == 200 and "Unknown timezone" in bad.content.decode()


def test_organization_profile_read_only_for_viewers(client, org_a, make_member):
    viewer = make_member(org_a, "v@alpha.test", "auditor").user
    client.force_login(viewer)
    assert client.get("/app/organization/").status_code == 200
    assert client.post("/app/organization/", {"name": "Hacked", "timezone": "UTC"}).status_code == 403




def test_notifications_ui_mark_read_and_all(client, owner_a, org_a):
    ns = notif.notify(org_a, [owner_a], title="One", link="/app/users/") + notif.notify(org_a, [owner_a], title="Two")
    client.force_login(owner_a)
    assert "One" in client.get("/app/notifications/bell/").content.decode()
    r = client.post(f"/app/notifications/{ns[0].pk}/read/")
    assert r.status_code == 302 and r["Location"] == "/app/users/"
    assert Notification.objects.unscoped().get(pk=ns[0].pk).read_at is not None
    client.post("/app/notifications/read-all/")
    assert not Notification.objects.unscoped().filter(recipient=owner_a, read_at__isnull=True).exists()


def test_notification_open_redirect_blocked(client, owner_a, org_a):
    n = notif.notify(org_a, [owner_a], title="Evil", link="//evil.example/x")[0]
    client.force_login(owner_a)
    r = client.post(f"/app/notifications/{n.pk}/read/")
    assert "evil.example" not in r["Location"]


def test_notification_email_task(org_a, owner_a, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        notif.notify(org_a, [owner_a], title="Escalation", body="Check it", link="/app/", email=True)
    assert mail.outbox and mail.outbox[-1].subject == "[Alpha Industries] Escalation"


def test_global_search_is_scoped_and_permissioned(client, owner_a, tech_a, org_a, org_b, make_member):
    make_member(org_b, "zoe@beta.test", "technician")
    client.force_login(owner_a)
    html = client.get("/app/search/?q=zoe").content.decode()
    assert "zoe@beta.test" not in html  # other tenant
    assert "tech@alpha.test" in client.get("/app/search/?q=tech").content.decode()
    client.force_login(tech_a)
    assert "tech@alpha.test" not in client.get("/app/search/?q=tech").content.decode()  # no user.view


def test_choose_org_and_switch(client, org_a, org_b, make_member):
    m = make_member(org_a, "multi@x.test", "admin")
    make_member(org_b, "multi@x.test", "admin")
    client.force_login(m.user)
    assert client.get("/app/choose-organization/").status_code == 200
    client.post("/app/switch-organization/", {"organization": str(org_b.pk)})
    assert "Beta Utilities" in client.get("/app/").content.decode()
    client.post("/app/switch-organization/", {"organization": str(org_a.pk)})
    assert "Alpha Industries" in client.get("/app/").content.decode()


def test_audit_ui_filters_and_detail(client, owner_a, org_a):
    client.force_login(owner_a)
    html = client.get("/app/audit/?action=organization.").content.decode()
    assert "organization.created" in html
    entry = AuditLog.objects.filter(organization=org_a, action="organization.created").first()
    assert client.get(f"/app/audit/{entry.pk}/").status_code == 200
    assert "organization.created" not in client.get("/app/audit/?q=nomatch-xyz").content.decode().split("<tbody>")[1]


def test_service_request_workflow_ui_actions_and_permissions(
    client, org_a, site_a1, make_asset, make_member
):
    from apps.incidents.models import ServiceRequest
    from apps.incidents import services as incident_services
    from apps.workorders.models import WorkOrder

    asset = make_asset(org_a, site_a1, "CHILLER-SR-UI")
    ops = make_member(org_a, "ops.ui@alpha.test", "operations_manager")
    planner = make_member(org_a, "planner.ui@alpha.test", "maintenance_planner")
    tech = make_member(org_a, "tech.ui@alpha.test", "technician")

    # 1. Create a request in NEW status
    sr = incident_services.create_request(
        org_a,
        asset=asset,
        title="AC compressor failure",
        description="Overheating alarm tripped",
        severity="HIGH",
        reporter=ops,
        actor=ops.user,
    )
    assert sr.status == "NEW"

    # Planner views NEW request: cannot triage, sees warning notice
    client.force_login(planner.user)
    res_planner = client.get(f"/app/incidents/{sr.pk}/")
    assert res_planner.status_code == 200
    html_planner = res_planner.content.decode()
    assert "btn-triage-request" not in html_planner
    assert "Action Unavailable" in html_planner
    assert "incident.triage" in html_planner

    # Ops views NEW request: can triage, sees Triage Request button
    client.force_login(ops.user)
    res_ops = client.get(f"/app/incidents/{sr.pk}/")
    assert res_ops.status_code == 200
    assert "btn-triage-request" in res_ops.content.decode()

    # Technician tries to POST triage: blocked
    client.force_login(tech.user)
    res_tech_triage = client.post(f"/app/incidents/{sr.pk}/transition/triage/")
    assert res_tech_triage.status_code == 403

    # Ops POSTs triage: succeeds, status -> TRIAGED
    client.force_login(ops.user)
    res_triage = client.post(f"/app/incidents/{sr.pk}/transition/triage/")
    assert res_triage.status_code == 302
    sr.refresh_from_db()
    assert sr.status == "TRIAGED"

    # Ops views TRIAGED request: sees Approve Request & Reject Request buttons
    res_ops_triaged = client.get(f"/app/incidents/{sr.pk}/")
    assert "btn-approve-request" in res_ops_triaged.content.decode()
    assert "btn-reject-request" in res_ops_triaged.content.decode()

    # Reject without reason fails validation
    res_reject_empty = client.post(f"/app/incidents/{sr.pk}/transition/reject/", {"reason": ""})
    assert res_reject_empty.status_code == 302
    sr.refresh_from_db()
    assert sr.status == "TRIAGED"  # Still TRIAGED

    # Ops POSTs approve: succeeds, status -> APPROVED
    res_approve = client.post(f"/app/incidents/{sr.pk}/transition/approve/")
    assert res_approve.status_code == 302
    sr.refresh_from_db()
    assert sr.status == "APPROVED"

    # Ops views APPROVED request: cannot convert (lacks work_order.create), sees guidance notice
    res_ops_approved = client.get(f"/app/incidents/{sr.pk}/")
    assert "btn-convert-work-order" not in res_ops_approved.content.decode()
    assert "work_order.create" in res_ops_approved.content.decode()

    # Planner views APPROVED request: sees Convert to Work Order button
    client.force_login(planner.user)
    res_planner_approved = client.get(f"/app/incidents/{sr.pk}/")
    assert "btn-convert-work-order" in res_planner_approved.content.decode()

    # Planner POSTs create work order: succeeds -> redirects to work order detail
    res_convert = client.post(f"/app/incidents/{sr.pk}/create-work-order/")
    assert res_convert.status_code == 302
    wo = WorkOrder.objects.filter(source_reference=sr.number).first()
    assert wo is not None
    assert res_convert.url == f"/app/work-orders/{wo.pk}/"

    sr.refresh_from_db()
    assert sr.status == "WORK_ORDER_CREATED"

    # Attempting duplicate conversion: blocked
    res_dup = client.post(f"/app/incidents/{sr.pk}/create-work-order/")
    assert res_dup.status_code == 302
    # Count of work orders remains 1
    assert WorkOrder.objects.filter(source_reference=sr.number).count() == 1

    # Viewing detail page now shows linked work order badge and duplicate prevention notice
    res_converted_detail = client.get(f"/app/incidents/{sr.pk}/")
    assert wo.order_number in res_converted_detail.content.decode()
    assert "Duplicate work order conversion is prevented" in res_converted_detail.content.decode()


def test_client_portal_account_forbidden_from_internal_actions(
    client, org_a, site_a1, make_asset, make_member
):
    from apps.portal.models import PortalAccount
    from apps.incidents import services as incident_services

    asset = make_asset(org_a, site_a1, "CHILLER-CLIENT-BLOCK")
    ops = make_member(org_a, "ops.cb@alpha.test", "operations_manager")
    sr = incident_services.create_request(
        org_a,
        asset=asset,
        title="Client Action Block Test",
        description="Testing internal endpoint protection",
        severity="LOW",
        reporter=ops,
        actor=ops.user,
    )

    # Setup client account
    client_member = make_member(org_a, "client.blocked@acme.test", "client_requester")
    PortalAccount.objects.create(organization=org_a, membership=client_member, company_name="Acme", is_enabled=True)

    client.force_login(client_member.user)

    # Client cannot access internal incident detail page (no incident.view)
    assert client.get(f"/app/incidents/{sr.pk}/").status_code in (403, 404)

    # Client cannot POST triage
    assert client.post(f"/app/incidents/{sr.pk}/transition/triage/").status_code == 403

    # Client cannot POST create work order
    assert client.post(f"/app/incidents/{sr.pk}/create-work-order/").status_code == 403


def test_admin_and_staff_actions_on_incidents_list_and_detail(
    client, org_a, site_a1, make_asset, make_member
):
    from apps.incidents import services as incident_services
    from apps.workorders.models import WorkOrder

    asset = make_asset(org_a, site_a1, "CHILLER-ADMIN-ACT")
    admin_member = make_member(org_a, "admin.act@alpha.test", "admin")
    reporter = make_member(org_a, "rep.act@alpha.test", "technician")

    sr = incident_services.create_request(
        org_a,
        asset=asset,
        title="Admin Approval Test",
        description="Testing Admin actions on list and detail",
        severity="HIGH",
        reporter=reporter,
        actor=reporter.user,
    )

    client.force_login(admin_member.user)

    # 1. Admin views list: Actions column present, Triage button shown for NEW request
    res_list = client.get("/app/incidents/")
    assert res_list.status_code == 200
    content_list = res_list.content.decode()
    assert "Actions" in content_list
    assert f"/app/incidents/{sr.pk}/transition/triage/" in content_list

    # 2. Admin executes Triage from list
    res_triage = client.post(f"/app/incidents/{sr.pk}/transition/triage/", follow=True)
    assert res_triage.status_code == 200
    sr.refresh_from_db()
    assert sr.status == "TRIAGED"

    # 3. Admin views list: Approve and Reject buttons shown for TRIAGED request
    res_list2 = client.get("/app/incidents/")
    content_list2 = res_list2.content.decode()
    assert f"/app/incidents/{sr.pk}/transition/approve/" in content_list2

    # 4. Admin executes Approve from detail or list
    res_approve = client.post(f"/app/incidents/{sr.pk}/transition/approve/", {"reason": "Admin approved work"}, follow=True)
    assert res_approve.status_code == 200
    sr.refresh_from_db()
    assert sr.status == "APPROVED"

    # 5. Admin views list: Convert WO button shown for APPROVED request
    res_list3 = client.get("/app/incidents/")
    content_list3 = res_list3.content.decode()
    assert f"/app/incidents/{sr.pk}/create-work-order/" in content_list3

    # 6. Admin executes Convert to Work Order
    res_conv = client.post(f"/app/incidents/{sr.pk}/create-work-order/", follow=True)
    assert res_conv.status_code == 200
    sr.refresh_from_db()
    assert sr.status == "WORK_ORDER_CREATED"
    wo = WorkOrder.objects.filter(source_reference=sr.number).first()
    assert wo is not None


def test_client_attachment_download_authorization(
    client, org_a, site_a1, make_asset, make_member
):
    from apps.portal.models import PortalAccount
    from apps.incidents import services as incident_services
    from django.core.files.uploadedfile import SimpleUploadedFile

    asset = make_asset(org_a, site_a1, "CHILLER-ATTACH-AUTH")
    client1_member = make_member(org_a, "client1.att@acme.test", "client_requester")
    PortalAccount.objects.create(organization=org_a, membership=client1_member, company_name="Acme 1", is_enabled=True)

    client2_member = make_member(org_a, "client2.att@acme.test", "client_requester")
    PortalAccount.objects.create(organization=org_a, membership=client2_member, company_name="Acme 2", is_enabled=True)

    admin_member = make_member(org_a, "admin.att@alpha.test", "admin")

    sr = incident_services.create_request(
        org_a,
        asset=asset,
        title="Attachment Test",
        description="Testing attachment download",
        severity="LOW",
        reporter=client1_member,
        actor=client1_member.user,
    )

    # Attach file uploaded by client1
    test_file = SimpleUploadedFile("diagnostic.txt", b"Test diagnostic content", content_type="text/plain")
    att = incident_services.add_evidence(
        sr,
        test_file,
        description="Diagnostic report",
        actor=client1_member.user,
    )

    # Client1 can download their own uploaded attachment
    client.force_login(client1_member.user)
    res_c1 = client.get(f"/app/files/{att.pk}/download/")
    assert res_c1.status_code in (200, 302)

    # Client2 cannot download Client1's attachment (out of scope returns 404 / 403)
    client.force_login(client2_member.user)
    res_c2 = client.get(f"/app/files/{att.pk}/download/")
    assert res_c2.status_code in (403, 404)

    # Staff Admin can download attachment
    client.force_login(admin_member.user)
    res_admin = client.get(f"/app/files/{att.pk}/download/")
    assert res_admin.status_code in (200, 302)


def test_client_only_portal_account_provisioning(org_a, make_member):
    from apps.portal import services as portal_services
    from apps.core.exceptions import ValidationFailed

    # Staff member with technician role cannot be provisioned as a PortalAccount
    tech_member = make_member(org_a, "tech.noportal@alpha.test", "technician")
    with pytest.raises(ValidationFailed) as exc_info:
        portal_services.create_portal_account(
            org_a,
            tech_member,
            company_name="Tech Co",
        )
    assert exc_info.value.code == "not_client_only"


