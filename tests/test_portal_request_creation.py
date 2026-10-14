from datetime import timedelta
import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.incidents.models import ServiceRequest
from apps.portal.models import PortalAccount
from apps.portal import services as portal_services
from apps.rbac import services as rbac
from apps.tenancy.models import Membership

pytestmark = pytest.mark.django_db
User = get_user_model()
PASSWORD = "Correct-Horse-Battery-9"

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc`\x00\x02\x00\x00\x05\x00\x01\x1e\x9c\xe1\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def make_portal_client(db):
    def _make(
        org,
        email="client@acme.test",
        company_name="Acme Corporation",
        *,
        is_enabled=True,
        active=True,
        role_key="client_requester",
    ):
        user = User.objects.filter(email=email).first() or User.objects.create_user(
            email=email,
            password=PASSWORD,
            full_name=email.split("@")[0].replace(".", " ").title(),
        )
        membership, _ = Membership.objects.unscoped().get_or_create(
            organization=org,
            user=user,
            defaults={
                "status": Membership.Status.ACTIVE if active else Membership.Status.SUSPENDED,
                "job_title": "Requester",
            },
        )
        if not active:
            membership.status = Membership.Status.SUSPENDED
            membership.save(update_fields=["status"])

        if role_key:
            role = rbac.system_role_by_key(org, role_key)
            rbac.set_membership_roles(membership, [role], actor=None, system=True)
        else:
            rbac.set_membership_roles(membership, [], actor=None, system=True)

        account, _ = PortalAccount.objects.unscoped().get_or_create(
            organization=org,
            membership=membership,
            defaults={"company_name": company_name, "is_enabled": is_enabled},
        )
        account.is_enabled = is_enabled
        account.save(update_fields=["is_enabled"])

        # Auto-grant existing assets for org so existing functional tests continue to pass seamlessly
        from apps.assets.models import Asset
        from apps.portal.models import PortalAssetGrant
        for ast in Asset.objects.filter(organization=org).exclude(status__in=["RETIRED", "DISPOSED"]):
            PortalAssetGrant.objects.get_or_create(
                organization=org,
                portal_account=account,
                asset=ast,
            )

        return user, membership, account


    return _make


@pytest.fixture
def asset_a(org_a, site_a1, make_asset):
    return make_asset(org_a, site_a1, "CHILLER-01")


@pytest.fixture
def asset_b(org_b, site_b1, make_asset):
    return make_asset(org_b, site_b1, "BOILER-01")


# 1. Authorized client successfully creates a service request via API
def test_authorized_client_creates_service_request_via_api(as_user, org_a, site_a1, asset_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.creator@acme.test", "Acme Corporation")
    api = as_user(user, org_a)

    payload = {
        "title": "Chiller abnormal vibration detected",
        "description": "High vibration on compressor bearing B-2 with noticeable noise.",
        "asset": str(asset_a.pk),
        "site": str(site_a1.pk),
        "severity": "HIGH",
        "service_impact": "DEGRADED",
        "impact_notes": "Cooling efficiency dropped by 25%",
    }

    res = api.post("/api/v1/portal/requests/", payload, format="json")
    assert res.status_code == status.HTTP_201_CREATED, res.data

    data = res.json()
    assert data["number"].startswith("SR-")
    assert data["title"] == "Chiller abnormal vibration detected"
    assert data["description"] == "High vibration on compressor bearing B-2 with noticeable noise."
    assert data["status"] == "NEW"
    assert data["severity"] == "HIGH"
    assert data["service_impact"] == "DEGRADED"
    assert data["impact_notes"] == "Cooling efficiency dropped by 25%"
    assert data["asset_id"] == str(asset_a.pk)
    assert data["asset_tag"] == "CHILLER-01"
    assert data["site_id"] == str(site_a1.pk)
    assert data["reported_by_id"] == str(membership.pk)
    assert data["reported_by_email"] == user.email


# 2. Request is persisted in the existing M05 ServiceRequest table and PostgreSQL
def test_request_persisted_in_m05_servicerequest_table(as_user, org_a, asset_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.db@acme.test", "Apex Client")
    api = as_user(user, org_a)

    payload = {
        "title": "Air handler filter replacement required",
        "description": "Monthly differential pressure alarm triggered.",
        "asset": str(asset_a.pk),
        "severity": "MEDIUM",
    }

    res = api.post("/api/v1/portal/requests/", payload, format="json")
    assert res.status_code == status.HTTP_201_CREATED
    sr_id = res.json()["id"]

    # Verify directly against M05 ServiceRequest database table
    sr = ServiceRequest.objects.for_organization(org_a).filter(pk=sr_id).first()
    assert sr is not None
    assert sr.number.startswith("SR-")
    assert sr.title == "Air handler filter replacement required"
    assert sr.status == "NEW"
    assert sr.asset_id == asset_a.pk
    assert sr.site_id == asset_a.site_id
    assert sr.reported_by_id == membership.pk
    assert sr.kind == ServiceRequest.Kind.SERVICE_REQUEST

    # Check M05 history row was created
    assert sr.history.count() == 1
    hist = sr.history.first()
    assert hist.to_status == "NEW"
    assert hist.action == "report"

    # Check audit log trail
    portal_audit = AuditLog.objects.filter(
        organization=org_a,
        action="portal.request_created",
        target_id=str(sr.pk),
    ).first()
    assert portal_audit is not None
    assert portal_audit.actor == user


# 3. Unauthenticated request is rejected
def test_unauthenticated_request_rejected(org_a, asset_a, client):
    api = APIClient()
    # API without token/credentials
    r_api = api.post(
        "/api/v1/portal/requests/",
        {"title": "Unauthenticated test", "asset": str(asset_a.pk)},
        format="json",
    )
    assert r_api.status_code == status.HTTP_401_UNAUTHORIZED

    # Web UI without login redirects to login page
    r_ui = client.get("/app/portal/requests/new/")
    assert r_ui.status_code == 302
    assert "/accounts/login/" in r_ui["Location"]

    r_ui_post = client.post(
        "/app/portal/requests/new/",
        {"title": "Unauthenticated test", "asset": str(asset_a.pk)},
    )
    assert r_ui_post.status_code == 302
    assert "/accounts/login/" in r_ui_post["Location"]


# 4. User without portal.request.create is rejected
def test_user_without_portal_request_create_permission_rejected(as_user, client, org_a, asset_a, make_portal_client):
    user, membership, account = make_portal_client(
        org_a, "client.noview@acme.test", "Viewer Only", role_key=None
    )
    # Grant only portal.request.view, omitting portal.request.create
    role = rbac.create_role(
        org_a,
        name="Portal Reader",
        description="Reader",
        permission_codes=["portal.request.view"],
        actor=None,
    )
    rbac.set_membership_roles(membership, [role], actor=None)

    api = as_user(user, org_a)
    payload = {"title": "Should be forbidden", "asset": str(asset_a.pk)}
    r_api = api.post("/api/v1/portal/requests/", payload, format="json")
    assert r_api.status_code == status.HTTP_403_FORBIDDEN

    # Web UI is also forbidden
    client.force_login(user)
    r_ui_get = client.get("/app/portal/requests/new/")
    assert r_ui_get.status_code == 403

    r_ui_post = client.post("/app/portal/requests/new/", payload)
    assert r_ui_post.status_code == 403


# 5. Disabled or suspended PortalAccount is rejected
def test_disabled_or_suspended_portal_account_rejected(as_user, client, org_a, asset_a, make_portal_client):
    # Disabled PortalAccount
    user_dis, mem_dis, acc_dis = make_portal_client(org_a, "client.dis@acme.test", is_enabled=False)
    api_dis = as_user(user_dis, org_a)
    r_dis = api_dis.post("/api/v1/portal/requests/", {"title": "Disabled account", "asset": str(asset_a.pk)}, format="json")
    assert r_dis.status_code == status.HTTP_403_FORBIDDEN

    client.force_login(user_dis)
    assert client.get("/app/portal/requests/new/").status_code == 403

    # Suspended Membership
    user_susp, mem_susp, acc_susp = make_portal_client(org_a, "client.susp@acme.test", active=False)
    api_susp = as_user(user_susp, org_a)
    r_susp = api_susp.post("/api/v1/portal/requests/", {"title": "Suspended membership", "asset": str(asset_a.pk)}, format="json")
    assert r_susp.status_code == status.HTTP_403_FORBIDDEN

    # Missing PortalAccount row entirely
    user_no_acc = User.objects.create_user(email="client.noacc@acme.test", password=PASSWORD)
    mem_no_acc = Membership.objects.unscoped().create(organization=org_a, user=user_no_acc, status=Membership.Status.ACTIVE)
    role = rbac.system_role_by_key(org_a, "client_requester")
    rbac.set_membership_roles(mem_no_acc, [role], actor=None, system=True)

    api_no_acc = as_user(user_no_acc, org_a)
    r_no_acc = api_no_acc.post("/api/v1/portal/requests/", {"title": "No portal account", "asset": str(asset_a.pk)}, format="json")
    assert r_no_acc.status_code == status.HTTP_403_FORBIDDEN


# 6. Cross-organization asset/site association is rejected
def test_cross_organization_asset_or_site_rejected(as_user, org_a, org_b, site_a1, site_b1, asset_a, asset_b, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.cross@acme.test", "Acme Cross Org")
    api = as_user(user, org_a)

    # Attempt to associate asset from Org B
    res_asset = api.post(
        "/api/v1/portal/requests/",
        {"title": "Targeting foreign asset", "asset": str(asset_b.pk)},
        format="json",
    )
    assert res_asset.status_code == status.HTTP_400_BAD_REQUEST
    assert "belongs to a different organization" in str(res_asset.data)

    # Attempt to associate site from Org B with asset from Org A
    res_site = api.post(
        "/api/v1/portal/requests/",
        {"title": "Targeting foreign site", "asset": str(asset_a.pk), "site": str(site_b1.pk)},
        format="json",
    )
    assert res_site.status_code == status.HTTP_400_BAD_REQUEST
    assert "belongs to a different organization" in str(res_site.data)


# 7. Invalid or missing required fields are rejected
def test_invalid_or_missing_required_fields_rejected(as_user, org_a, asset_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.validation@acme.test", "Acme Corp")
    api = as_user(user, org_a)

    # Missing title
    res1 = api.post("/api/v1/portal/requests/", {"asset": str(asset_a.pk)}, format="json")
    assert res1.status_code == status.HTTP_400_BAD_REQUEST
    assert "title" in str(res1.data)

    # Title too short (< 3 characters)
    res2 = api.post("/api/v1/portal/requests/", {"title": "ab", "asset": str(asset_a.pk)}, format="json")
    assert res2.status_code == status.HTTP_400_BAD_REQUEST
    assert "title" in str(res2.data)

    # Missing asset
    res3 = api.post("/api/v1/portal/requests/", {"title": "Valid title without asset"}, format="json")
    assert res3.status_code == status.HTTP_400_BAD_REQUEST
    assert "asset" in str(res3.data)

    # Invalid asset UUID
    res4 = api.post("/api/v1/portal/requests/", {"title": "Valid title", "asset": "not-a-uuid"}, format="json")
    assert res4.status_code == status.HTTP_400_BAD_REQUEST

    # Client cannot select CRITICAL severity
    res5 = api.post(
        "/api/v1/portal/requests/",
        {"title": "Attempting critical severity", "asset": str(asset_a.pk), "severity": "CRITICAL"},
        format="json",
    )
    assert res5.status_code == status.HTTP_400_BAD_REQUEST

    # Future occurred_at rejected
    future_time = timezone.now() + timedelta(days=2)
    res6 = api.post(
        "/api/v1/portal/requests/",
        {"title": "Future timestamp", "asset": str(asset_a.pk), "occurred_at": future_time.isoformat()},
        format="json",
    )
    assert res6.status_code == status.HTTP_400_BAD_REQUEST


# 8. Client cannot impersonate another requester
def test_client_cannot_impersonate_another_requester(as_user, org_a, org_b, asset_a, make_portal_client, make_member):
    client_user, client_mem, _ = make_portal_client(org_a, "real.client@acme.test", "Real Client")
    impersonated_mem = make_member(org_a, "victim.user@acme.test", "technician")

    api = as_user(client_user, org_a)
    payload = {
        "title": "Impersonation attempt",
        "description": "Trying to forge reported_by membership",
        "asset": str(asset_a.pk),
        "reported_by": str(impersonated_mem.pk),
        "reporter": str(impersonated_mem.pk),
        "organization": str(org_b.pk),
    }

    res = api.post("/api/v1/portal/requests/", payload, format="json")
    assert res.status_code == status.HTTP_201_CREATED

    sr = ServiceRequest.objects.get(pk=res.json()["id"])
    # Backend MUST derive reporter and organization from authenticated context
    assert sr.reported_by == client_mem
    assert sr.reported_by != impersonated_mem
    assert sr.organization == org_a


# 9. Duplicate detection follows existing M05 rules
def test_duplicate_detection_follows_m05_rules(as_user, org_a, asset_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.dupes@acme.test", "Acme Dupes")
    api = as_user(user, org_a)

    payload = {
        "title": "Water pump pressure low",
        "asset": str(asset_a.pk),
        "severity": "MEDIUM",
    }

    # First request succeeds
    res1 = api.post("/api/v1/portal/requests/", payload, format="json")
    assert res1.status_code == status.HTTP_201_CREATED
    sr1_num = res1.json()["number"]

    # Second request follows M05 rules: gets sequential unique number, persists independently
    res2 = api.post("/api/v1/portal/requests/", payload, format="json")
    assert res2.status_code == status.HTTP_201_CREATED
    sr2_num = res2.json()["number"]
    assert sr1_num != sr2_num

    # Terminal asset (RETIRED) is rejected following M05 rules (asset_terminal)
    asset_a.status = "RETIRED"
    asset_a.save(update_fields=["status"])

    res_retired = api.post("/api/v1/portal/requests/", payload, format="json")
    assert res_retired.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_409_CONFLICT)
    assert "Retired or disposed" in str(res_retired.data)


# 10. Request creation with attachment support
def test_authorized_client_creates_request_with_attachment(as_user, org_a, asset_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.attach@acme.test", "Acme Files")
    api = as_user(user, org_a)

    file_upload = SimpleUploadedFile("diagnostic_photo.png", PNG, "image/png")
    payload = {
        "title": "Coolant leak with photo proof",
        "asset": str(asset_a.pk),
        "severity": "MEDIUM",
        "file": file_upload,
    }

    res = api.post("/api/v1/portal/requests/", payload, format="multipart")
    assert res.status_code == status.HTTP_201_CREATED, res.data
    assert res.json()["attachments_count"] == 1

    sr = ServiceRequest.objects.get(pk=res.json()["id"])
    from apps.incidents.selectors import evidence_for
    attachments = evidence_for(org_a, sr)
    assert attachments.count() == 1
    assert attachments.first().original_name == "diagnostic_photo.png"


# 11. Client Portal UI: Request creation form and dashboard integration
def test_client_portal_ui_request_create_and_dashboard_display(client, org_a, asset_a, make_portal_client):
    user, membership, account = make_portal_client(org_a, "client.ui@acme.test", "Acme UI Co")
    client.force_login(user)

    # GET /app/portal/requests/new/ renders form
    r_get = client.get("/app/portal/requests/new/")
    assert r_get.status_code == 200
    html = r_get.content.decode()
    assert "Create Service Request" in html
    assert "CHILLER-01" in html
    assert "submitRequestBtn" in html

    # POST valid form
    r_post = client.post(
        "/app/portal/requests/new/",
        {
            "title": "Conveyor belt alignment issue",
            "asset": str(asset_a.pk),
            "description": "Belt drifting to the right edge during peak cycle.",
            "severity": "HIGH",
            "service_impact": "DEGRADED",
            "impact_notes": "Slight noise observed",
        },
    )
    assert r_post.status_code == 302
    assert "/app/portal/?created=" in r_post["Location"]

    # Request persisted in M05
    sr = ServiceRequest.objects.for_organization(org_a).filter(title="Conveyor belt alignment issue").first()
    assert sr is not None
    assert sr.number in r_post["Location"]

    # Dashboard displays confirmation banner and recent requests table
    r_dash = client.get(r_post["Location"])
    assert r_dash.status_code == 200
    dash_html = r_dash.content.decode()
    assert sr.number in dash_html
    assert "Conveyor belt alignment issue" in dash_html
    assert "Service Request Created Successfully!" in dash_html
