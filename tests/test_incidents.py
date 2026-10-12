"""M05 Incident / Breakdown: services (severity, impact, downtime, evidence, workflow), API, UI, RBAC, tenant isolation,
audit and the demo seed."""
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core.exceptions import Conflict, InvalidTransition, ValidationFailed
from apps.incidents import services
from apps.incidents.models import Downtime, ServiceRequest
from apps.tenancy.models import Membership

pytestmark = pytest.mark.django_db

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc`\x00\x02\x00\x00\x05\x00\x01\x1e\x9c\xe1\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture
def pump(org_a, site_a1, make_asset):
    return make_asset(org_a, site_a1, "PUMP-1")


@pytest.fixture
def reporter(org_a, make_member):
    return make_member(org_a, "reporter@alpha.test", "technician")


@pytest.fixture
def ops(org_a, make_member):
    return make_member(org_a, "ops@alpha.test", "operations_manager")


@pytest.fixture
def report(org_a, pump, reporter):
    def _report(**kw):
        kw.setdefault("title", "Pump seized")
        return services.create_request(org_a, asset=pump, reporter=reporter, actor=reporter.user, **kw)
    return _report


def audit_actions(org):
    return set(AuditLog.objects.filter(organization=org).values_list("action", flat=True))


# --- services: report, severity, service impact, asset linkage -------------------------------------------------


def test_report_links_asset_and_site_numbers_and_audits(org_a, pump, report):
    sr = report(severity="CRITICAL", service_impact="FULL_OUTAGE", impact_notes="Line 2 stopped")
    assert sr.number.startswith("INC-") and sr.asset_id == pump.pk and sr.site_id == pump.site_id
    assert (sr.status, sr.severity, sr.service_impact) == ("NEW", "CRITICAL", "FULL_OUTAGE")
    assert sr.history.get().to_status == "NEW"
    assert {"incident.created"} <= audit_actions(org_a)


def test_service_request_gets_its_own_number_prefix(report):
    assert report(kind="SERVICE_REQUEST").number.startswith("SR-")


@pytest.mark.parametrize("field,value,code", [("severity", "EXTREME", "invalid_severity"),
                                              ("service_impact", "WHATEVER", "invalid_impact"),
                                              ("kind", "OTHER", "invalid_kind"), ("title", "ab", "title_required")])
def test_invalid_values_are_refused(report, field, value, code):
    with pytest.raises(ValidationFailed) as exc:
        report(**{field: value})
    assert exc.value.code == code


def test_retired_asset_cannot_get_a_report(org_a, pump, reporter):
    pump.status = "RETIRED"
    pump.save(update_fields=["status"])
    with pytest.raises(Conflict) as exc:
        services.create_request(org_a, asset=pump, reporter=reporter, actor=reporter.user, title="Too late")
    assert exc.value.code == "asset_terminal"


def test_cross_tenant_asset_is_refused(org_a, org_b, site_b1, make_asset, reporter):
    foreign = make_asset(org_b, site_b1, "B-PUMP")
    with pytest.raises(ValidationFailed) as exc:
        services.create_request(org_a, asset=foreign, reporter=reporter, actor=reporter.user, title="Cross tenant")
    assert exc.value.code == "cross_tenant_asset"


def test_update_only_before_approval_and_audits_changes(org_a, report, ops):
    sr = report()
    sr = services.update_request(sr, severity="HIGH", service_impact="DEGRADED", actor=ops.user)
    assert sr.severity == "HIGH" and "incident.updated" in audit_actions(org_a)
    services.transition(sr, action="triage", actor=ops.user)
    services.transition(sr, action="approve", actor=ops.user)
    with pytest.raises(Conflict) as exc:
        services.update_request(sr, severity="LOW", actor=ops.user)
    assert exc.value.code == "request_locked"


# --- downtime start / end ----------------------------------------------------------------------------------


def test_downtime_start_and_end_with_duration_and_audit(org_a, report, ops):
    start = timezone.now() - timedelta(hours=3)
    sr = report(downtime_started_at=start)
    dt = Downtime.objects.get(request=sr)
    assert dt.ended_at is None and dt.duration is None  # still down
    dt = services.set_downtime(sr, started_at=start, ended_at=start + timedelta(hours=2), actor=ops.user)
    assert dt.duration == timedelta(hours=2) and dt.end_source == "manual"
    assert {"incident.downtime_recorded", "incident.downtime_updated"} <= audit_actions(org_a)


def test_downtime_end_before_start_and_future_times_are_refused(report, ops):
    sr = report()
    now = timezone.now()
    with pytest.raises(ValidationFailed) as exc:
        services.set_downtime(sr, started_at=now - timedelta(hours=1), ended_at=now - timedelta(hours=2),
                              actor=ops.user)
    assert exc.value.code == "downtime_end_before_start"
    with pytest.raises(ValidationFailed) as exc:
        services.set_downtime(sr, started_at=now + timedelta(days=1), actor=ops.user)
    assert exc.value.code == "future_time"


def test_downtime_cannot_change_on_a_rejected_request(report, ops):
    sr = report()
    services.transition(sr, action="triage", actor=ops.user)
    services.transition(sr, action="reject", reason="Duplicate report", actor=ops.user)
    with pytest.raises(Conflict):
        services.set_downtime(sr, started_at=timezone.now() - timedelta(hours=1), actor=ops.user)


# --- evidence (photos / files) -----------------------------------------------------------------------------


def test_photo_evidence_is_validated_stored_and_audited(org_a, report, reporter):
    sr = report()
    att = services.add_evidence(sr, SimpleUploadedFile("seal.png", PNG, "image/png"), description="Seal",
                                actor=reporter.user)
    assert att.original_name == "seal.png" and att.organization_id == org_a.pk
    assert "incident.evidence_added" in audit_actions(org_a)


def test_disguised_executable_is_rejected(report, reporter):
    sr = report()
    with pytest.raises(Exception):  # noqa: B017 - upload validation error type belongs to core.uploads
        services.add_evidence(sr, SimpleUploadedFile("evil.png", b"MZ\x90\x00not-an-image", "image/png"),
                              actor=reporter.user)


# --- workflow ---------------------------------------------------------------------------------------------


def test_triage_approve_path_records_history_and_blocks_invalid_jumps(report, ops):
    sr = report()
    with pytest.raises(InvalidTransition):
        services.transition(sr, action="approve", actor=ops.user)  # NEW cannot be approved directly
    sr = services.transition(sr, action="triage", actor=ops.user)
    sr = services.transition(sr, action="approve", actor=ops.user)
    assert sr.status == "APPROVED" and sr.history.count() == 3


def test_reject_needs_a_reason_and_system_actions_are_refused(report, ops):
    sr = report()
    services.transition(sr, action="triage", actor=ops.user)
    with pytest.raises(ValidationFailed) as exc:
        services.transition(sr, action="reject", actor=ops.user)
    assert exc.value.code == "reason_required"
    with pytest.raises(ValidationFailed) as exc:
        services.transition(sr, action="start_service", actor=ops.user)
    assert exc.value.code == "system_action"


@pytest.mark.parametrize(("kind", "source_type"), [
    (ServiceRequest.Kind.INCIDENT, "INCIDENT"),
    (ServiceRequest.Kind.SERVICE_REQUEST, "REQUEST"),
])
def test_approved_incident_or_request_creates_one_linked_work_order(
        report, ops, make_member, kind, source_type):
    from datetime import timedelta

    from django.utils import timezone

    from apps.workorders import services as work_orders
    from apps.workorders.models import WorkOrder

    sr = report(kind=kind, severity=ServiceRequest.Severity.CRITICAL)
    services.transition(sr, action="triage", actor=ops.user)
    services.transition(sr, action="approve", actor=ops.user)
    planner = make_member(sr.organization, f"work-order-from-{kind.lower()}@alpha.test", "maintenance_planner")

    wo = services.create_work_order_for_request(sr, actor=planner.user, membership=planner)
    sr.refresh_from_db()
    assert wo.status == WorkOrder.Status.DRAFT
    assert wo.source_type == source_type
    assert wo.source_reference == sr.number
    assert wo.asset_reference == sr.asset.asset_tag
    assert wo.site_id == sr.site_id
    assert wo.priority == WorkOrder.Priority.CRITICAL
    assert sr.status == "WORK_ORDER_CREATED"

    technician = make_member(sr.organization, f"work-order-tech-{kind.lower()}@alpha.test", "technician")
    start = timezone.now() + timedelta(days=3)
    wo = work_orders.plan_work_order(wo, membership=planner, actor=planner.user,
                                     planned_start=start, planned_end=start + timedelta(hours=1),
                                     due_at=start + timedelta(hours=1), estimated_hours=1,
                                     work_type=WorkOrder.WorkType.CORRECTIVE,
                                     priority=WorkOrder.Priority.CRITICAL)
    wo = work_orders.assign_work_order(wo, membership=planner, actor=planner.user,
                                      assignee_id=technician.pk)
    wo = work_orders.dispatch_work_order(wo, membership=planner, actor=planner.user)
    work_orders.start_work_order(wo, membership=technician, actor=technician.user)
    sr.refresh_from_db()
    assert sr.status == "IN_SERVICE"
    with pytest.raises(InvalidTransition):
        services.create_work_order_for_request(sr, actor=planner.user, membership=planner)


def test_approved_request_detail_exposes_create_work_order_action(report, ops, make_member):
    from django.test import Client
    from django.urls import reverse

    from apps.workorders.models import WorkOrder

    sr = report(kind=ServiceRequest.Kind.SERVICE_REQUEST, severity=ServiceRequest.Severity.HIGH)
    services.transition(sr, action="triage", actor=ops.user)
    services.transition(sr, action="approve", actor=ops.user)
    planner = make_member(sr.organization, "work-order-from-request-ui@alpha.test", "maintenance_planner")
    ui = Client()
    ui.force_login(planner.user)
    detail = ui.get(reverse("incidents:detail", args=[sr.pk]))
    assert detail.status_code == 200
    assert b"Create work order" in detail.content

    response = ui.post(reverse("incidents:create_work_order", args=[sr.pk]))
    assert response.status_code == 302
    wo = WorkOrder.objects.get(source_reference=sr.number)
    assert response.url == reverse("workorders:detail", args=[wo.pk])
    sr.refresh_from_db()
    assert sr.status == "WORK_ORDER_CREATED"


# --- API --------------------------------------------------------------------------------------------------


def test_api_report_list_retrieve_and_downtime(as_user, org_a, pump, reporter, ops):
    c = as_user(reporter.user, org_a)
    body = {"asset": str(pump.pk), "title": "Motor overheating", "severity": "HIGH", "service_impact": "PARTIAL_OUTAGE",
            "downtime_started_at": (timezone.now() - timedelta(hours=1)).isoformat()}
    res = c.post("/api/v1/service-requests/", body, format="json")
    assert res.status_code == 201, res.content
    pk = res.json()["id"]
    assert res.json()["status"] == "NEW" and res.json()["downtime"]["ended_at"] is None
    assert c.get(f"/api/v1/service-requests/{pk}/").status_code == 200
    ops_c = as_user(ops.user, org_a)
    assert ops_c.get("/api/v1/service-requests/").json()["count"] == 1
    end = ops_c.put(f"/api/v1/service-requests/{pk}/downtime/", {
        "started_at": (timezone.now() - timedelta(hours=1)).isoformat(),
        "ended_at": timezone.now().isoformat()}, format="json")
    assert end.status_code == 200 and end.json()["duration_seconds"] >= 0


def test_api_evidence_upload_and_list(as_user, org_a, pump, report, reporter):
    sr = report()
    c = as_user(reporter.user, org_a)
    res = c.post(f"/api/v1/service-requests/{sr.pk}/evidence/",
                 {"file": SimpleUploadedFile("photo.png", PNG, "image/png"), "description": "photo"},
                 format="multipart")
    assert res.status_code == 201, res.content
    assert c.get(f"/api/v1/service-requests/{sr.pk}/evidence/").json()["count"] == 1


def test_api_transition_requires_the_action_permission(as_user, org_a, report, reporter, ops):
    sr = report()
    assert as_user(reporter.user, org_a).post(f"/api/v1/service-requests/{sr.pk}/transition/",
                                              {"action": "triage"}, format="json").status_code == 403
    assert as_user(ops.user, org_a).post(f"/api/v1/service-requests/{sr.pk}/transition/",
                                         {"action": "triage"}, format="json").status_code == 200


def test_api_requires_authentication(api):
    assert api.get("/api/v1/service-requests/").status_code in (401, 403)


# --- tenant isolation and site scope -----------------------------------------------------------------------


def test_other_organization_sees_and_touches_nothing(as_user, org_a, org_b, report, owner_b):
    sr = report()
    c = as_user(owner_b, org_b)
    assert c.get("/api/v1/service-requests/").json()["count"] == 0
    assert c.get(f"/api/v1/service-requests/{sr.pk}/").status_code == 404
    assert c.post(f"/api/v1/service-requests/{sr.pk}/transition/", {"action": "triage"},
                  format="json").status_code == 404
    assert c.put(f"/api/v1/service-requests/{sr.pk}/downtime/", {"started_at": timezone.now().isoformat()},
                 format="json").status_code == 404


def test_cannot_report_against_a_foreign_asset_via_api(as_user, org_a, org_b, site_b1, make_asset, reporter):
    foreign = make_asset(org_b, site_b1, "B-PUMP")
    res = as_user(reporter.user, org_a).post("/api/v1/service-requests/", {"asset": str(foreign.pk), "title": "Nope"},
                                             format="json")
    assert res.status_code == 404


def test_site_scoped_member_only_sees_their_site(as_user, org_a, site_a1, site_a2, make_asset, make_scoped_member,
                                                 reporter):
    other = make_asset(org_a, site_a2, "FAN-2")
    services.create_request(org_a, asset=other, reporter=reporter, actor=reporter.user, title="Fan down")
    scoped = make_scoped_member(org_a, "scoped@alpha.test", "operations_manager", [site_a1])
    assert as_user(scoped.user, org_a).get("/api/v1/service-requests/").json()["count"] == 0


def test_auditor_reads_but_cannot_report(as_user, org_a, pump, make_member):
    auditor = make_member(org_a, "aud@alpha.test", "auditor")
    c = as_user(auditor.user, org_a)
    assert c.get("/api/v1/service-requests/").status_code == 200
    assert c.post("/api/v1/service-requests/", {"asset": str(pump.pk), "title": "Not allowed"},
                  format="json").status_code == 403


# --- UI -------------------------------------------------------------------------------------------------------


def test_incident_pages_render_and_report_form_creates_a_request(client, owner_a, org_a, pump, report):
    sr = report(severity="HIGH", service_impact="DEGRADED")
    client.force_login(owner_a)
    assert client.get("/app/incidents/").status_code == 200
    for tab in ("overview", "evidence", "history"):
        assert client.get(f"/app/incidents/{sr.pk}/?tab={tab}").status_code == 200
    assert client.get("/app/incidents/new/").status_code == 200
    res = client.post("/app/incidents/new/", {"asset": str(pump.pk), "kind": "INCIDENT", "title": "UI reported fault",
                                              "severity": "HIGH", "service_impact": "NONE"})
    assert res.status_code == 302
    assert ServiceRequest.objects.for_organization(org_a).filter(title="UI reported fault").exists()


def test_ui_evidence_upload_and_downtime_buttons_persist(client, owner_a, org_a, report):
    sr = report()
    client.force_login(owner_a)
    up = client.post(f"/app/incidents/{sr.pk}/evidence/",
                     {"file": SimpleUploadedFile("p.png", PNG, "image/png"), "description": "x"})
    assert up.status_code == 302
    assert "incident.evidence_added" in audit_actions(org_a)
    dt = client.post(f"/app/incidents/{sr.pk}/downtime/", {"started_at": "2026-01-01T08:00"})
    assert dt.status_code == 302 and Downtime.objects.filter(request=sr).exists()


def test_other_tenant_cannot_open_incident_pages(client, owner_b, report):
    sr = report()
    client.force_login(owner_b)
    assert client.get(f"/app/incidents/{sr.pk}/").status_code == 404
    assert client.post(f"/app/incidents/{sr.pk}/transition/triage/").status_code == 404


# --- demo seed ----------------------------------------------------------------------------------------------


def test_m05_seed_is_idempotent_and_goes_through_services(settings, org_a, site_a1, make_member):
    settings.DEBUG = True
    org_a.name = "Demo Organization"
    org_a.save(update_fields=["name"])
    site_a1.code = "PUNE-1"
    site_a1.save(update_fields=["code"])
    make_member(org_a, "owner@demo.local", "owner")
    make_member(org_a, "technician@demo.local", "technician")
    Membership.objects.filter(organization=org_a).update(status="ACTIVE")
    call_command("seed_m05_demo")
    first = ServiceRequest.objects.for_organization(org_a).count()
    call_command("seed_m05_demo")
    assert first == 4 and ServiceRequest.objects.for_organization(org_a).count() == first
    assert Downtime.objects.for_organization(org_a).count() == 2
