"""M04 UI: pages render real data, every control posts to a real endpoint that persists / audits, forbidden roles and
foreign objects are refused, and a plan -> schedule -> due -> generate -> work order journey runs through the HTML."""
from datetime import date

import pytest
from django.test import Client

from apps.assets import services as asset_services
from apps.audit.models import AuditLog
from apps.maintenance import services as pm
from apps.maintenance.models import MaintenanceCycle, MaintenancePlan, MaintenanceSchedule
from apps.workorders.models import WorkOrder
from tests.pm_support import T0, freeze, make_plan, time_schedule

pytestmark = pytest.mark.django_db


def client_for(user):
    c = Client()
    c.force_login(user)
    return c


def flash(client, response):
    return " ".join(str(m) for m in client.get(response["Location"]).context["messages"])


@pytest.fixture
def ui(pm_, monkeypatch):
    freeze(monkeypatch, T0)
    p = pm_
    p["cl"] = {k: client_for(p[k].user) for k in ("planner", "ops", "sup", "tech", "reader")}
    return p


def test_pages_render_with_real_data(ui):
    c = ui["cl"]["planner"]
    plan = make_plan(ui)
    sch = time_schedule(plan)
    cycle = pm.generate_cycle(sch)
    pages = {
        "/app/maintenance/plans/": plan.name, "/app/maintenance/plans/new/": "Required checklist",
        f"/app/maintenance/plans/{plan.pk}/": "every 1 month", f"/app/maintenance/plans/{plan.pk}/edit/": plan.name,
        f"/app/maintenance/plans/{plan.pk}/schedules/new/": "First due date",
        f"/app/maintenance/schedules/{sch.pk}/": "Recurrence", f"/app/maintenance/schedules/{sch.pk}/edit/": "Edit schedule",
        "/app/maintenance/due/": plan.name, "/app/maintenance/history/": cycle.work_order.number,
    }
    for url, needle in pages.items():
        r = c.get(url)
        assert r.status_code == 200 and needle in r.content.decode(), url
    for url in ("/app/maintenance/plans/?q=pump&active=1&site=garbage", "/app/maintenance/due/?state=DUE&site=nope",
                "/app/maintenance/history/?plan=garbage", "/app/maintenance/plans/?page=99"):
        assert c.get(url).status_code == 200, url
    assert "Maintenance plans" in c.get("/app/").content.decode()


def test_role_gating(ui):
    plan = make_plan(ui)
    sch = time_schedule(plan)
    listing = ["/app/maintenance/plans/", "/app/maintenance/due/", "/app/maintenance/history/",
               f"/app/maintenance/plans/{plan.pk}/", f"/app/maintenance/schedules/{sch.pk}/"]
    writers = ["/app/maintenance/plans/new/", f"/app/maintenance/plans/{plan.pk}/edit/",
               f"/app/maintenance/plans/{plan.pk}/schedules/new/", f"/app/maintenance/schedules/{sch.pk}/edit/"]
    posts = [f"/app/maintenance/plans/{plan.pk}/active/", f"/app/maintenance/schedules/{sch.pk}/generate/",
             f"/app/maintenance/schedules/{sch.pk}/disable/", "/app/maintenance/plans/new/"]
    tech = ui["cl"]["tech"]
    for url in listing + writers:
        assert tech.get(url).status_code == 403, url
    for who in ("sup", "reader"):  # may look, may not write
        cl = ui["cl"][who]
        for url in listing:
            assert cl.get(url).status_code == 200, (who, url)
        for url in writers:
            assert cl.get(url).status_code == 403, (who, url)
        for url in posts:
            assert cl.post(url, {"active": "0"}).status_code == 403, (who, url)
    for url in posts[:3]:
        assert tech.post(url, {"active": "0"}).status_code == 403
    assert Client().get("/app/maintenance/plans/").status_code == 302
    assert Client().post(posts[1]).status_code == 302
    plan.refresh_from_db()
    assert plan.is_active and not MaintenanceCycle.objects.exists()


def test_journey_plan_schedule_due_generate_work_order(ui):
    c = ui["cl"]["planner"]
    asset = ui["asset"]
    r = c.post("/app/maintenance/plans/new/", {"asset": str(asset.pk), "name": "Monthly pump service",
                                                "priority": "HIGH", "description": "Grease the bearings.",
                                                "estimated_hours": "2"})
    assert r.status_code == 302
    plan = MaintenancePlan.objects.get(name="Monthly pump service")
    assert (plan.asset_id, plan.site_id, plan.priority) == (asset.pk, ui["site"].pk, "HIGH")
    assert AuditLog.objects.filter(action="maintenance.plan_created", target_id=str(plan.pk)).exists()
    r = c.post(f"/app/maintenance/plans/{plan.pk}/schedules/new/", {
        "trigger_type": "TIME", "frequency": "MONTHLY", "interval_count": "1", "start_date": "2026-03-10",
        "lead_days": "0", "window_start_time": "08:00", "window_hours": "8", "reminder_days": "0"})
    assert r.status_code == 302
    sch = MaintenanceSchedule.objects.get()
    assert (sch.next_sequence, sch.next_due_date) == (0, date(2026, 3, 10))
    due = c.get("/app/maintenance/due/").content.decode()
    assert "Monthly pump service" in due and "Due" in due and "Generate now" in due
    # generate from the due page: a real work order appears, linked to its source
    r = c.post(f"/app/maintenance/schedules/{sch.pk}/generate/", {"next": "/app/maintenance/due/"})
    assert r["Location"] == "/app/maintenance/due/"
    assert "generated" in flash(c, r)
    cycle = MaintenanceCycle.objects.get()
    wo = cycle.work_order
    assert (wo.status, wo.work_type, wo.source_type, wo.source_id) == ("PLANNED", "PREVENTIVE",
                                                                      "PREVENTIVE_MAINTENANCE", cycle.pk)
    detail = c.get(f"/app/work-orders/{wo.pk}/").content.decode()
    assert "Preventive maintenance: Monthly pump service" in detail and "cycle #0" in detail
    assert wo.number in c.get("/app/maintenance/history/").content.decode()
    assert wo.number in c.get(f"/app/maintenance/plans/{plan.pk}/").content.decode()
    # a second press: next occurrence is a month away and the first order is still open -> refused, nothing new
    r = c.post(f"/app/maintenance/schedules/{sch.pk}/generate/")
    assert "still open" in flash(c, r)
    assert WorkOrder.objects.filter(source_type="PREVENTIVE_MAINTENANCE").count() == 1
    # the due page no longer lists it as DUE
    assert plan.name not in c.get("/app/maintenance/due/?state=DUE").content.decode()
    assert plan.name in c.get("/app/maintenance/due/?state=SCHEDULED").content.decode()
    assert MaintenanceSchedule.objects.get().next_sequence == 1


def test_enable_disable_through_the_ui(ui):
    c = ui["cl"]["planner"]
    plan = make_plan(ui)
    sch = time_schedule(plan)
    assert c.post(f"/app/maintenance/plans/{plan.pk}/active/", {"active": "0"}).status_code == 302
    plan.refresh_from_db()
    assert plan.is_active is False
    page = c.get(f"/app/maintenance/plans/{plan.pk}/").content.decode()
    assert "disabled" in page.lower() and "Enable plan" in page
    r = c.post(f"/app/maintenance/schedules/{sch.pk}/generate/")
    assert "plan disabled" in flash(c, r) and not MaintenanceCycle.objects.exists()
    assert c.post(f"/app/maintenance/plans/{plan.pk}/active/", {"active": "1"}).status_code == 302
    assert c.post(f"/app/maintenance/schedules/{sch.pk}/disable/").status_code == 302
    sch.refresh_from_db()
    assert sch.is_active is False
    assert c.get("/app/maintenance/due/").content.decode().count(plan.name) == 0  # disabled schedules leave the list
    assert c.post(f"/app/maintenance/schedules/{sch.pk}/enable/").status_code == 302
    sch.refresh_from_db()
    assert sch.is_active is True
    assert c.post(f"/app/maintenance/schedules/{sch.pk}/explode/").status_code == 302  # unknown action: message only


def test_forms_reject_bad_input_and_edit_persists(ui):
    c = ui["cl"]["planner"]
    r = c.post("/app/maintenance/plans/new/", {"asset": str(ui["asset"].pk), "name": "x", "priority": "HIGH"})
    assert r.status_code == 400 and not MaintenancePlan.objects.exists()
    plan = make_plan(ui)
    base = f"/app/maintenance/plans/{plan.pk}/schedules/new/"
    good = {"trigger_type": "TIME", "frequency": "WEEKLY", "interval_count": "2", "start_date": "2026-03-10",
            "lead_days": "0", "window_start_time": "08:00", "window_hours": "8", "reminder_days": "0"}
    for patch in ({"frequency": ""}, {"interval_count": "0"}, {"start_date": ""}, {"window_hours": "100"},
                  {"trigger_type": "METER"}):
        r = c.post(base, {**good, **patch})
        assert r.status_code == 400, patch
    assert not MaintenanceSchedule.objects.exists()
    assert c.post(base, good).status_code == 302
    r = c.post(base, good)
    assert r.status_code == 400 and "identical schedule" in r.content.decode()
    sch = MaintenanceSchedule.objects.get()
    r = c.post(f"/app/maintenance/schedules/{sch.pk}/edit/", {"frequency": "WEEKLY", "interval_count": "2",
                                                              "start_date": "2026-03-10", "lead_days": "3",
                                                              "window_start_time": "09:30", "window_hours": "4",
                                                              "reminder_days": "2"})
    assert r.status_code == 302
    sch.refresh_from_db()
    assert (sch.lead_days, sch.window_hours, sch.reminder_days, str(sch.window_start_time)) == (3, 4, 2, "09:30:00")
    assert c.post(f"/app/maintenance/plans/{plan.pk}/edit/", {"name": "Renamed plan", "priority": "LOW"}).status_code == 302
    plan.refresh_from_db()
    assert (plan.name, plan.priority) == ("Renamed plan", "LOW")


def test_meter_schedule_through_the_ui(ui):
    c = ui["cl"]["planner"]
    plan = make_plan(ui)
    asset_services.record_reading(ui["meter"], value=1200, actor=None)
    r = c.post(f"/app/maintenance/plans/{plan.pk}/schedules/new/", {
        "trigger_type": "METER", "meter": str(ui["meter"].pk), "interval_value": "500", "start_value": "0",
        "lead_days": "0", "window_start_time": "08:00", "window_hours": "8", "reminder_days": "0"})
    assert r.status_code == 302
    sch = MaintenanceSchedule.objects.get()
    assert (sch.next_sequence, str(sch.next_due_value)) == (3, "1500.000")
    page = c.get(f"/app/maintenance/schedules/{sch.pk}/").content.decode()
    assert "1500" in page and "Run hours" in page
    other = asset_services.create_meter(ui["asset2"], name="Other hours", unit="h", actor=None)
    r = c.post(f"/app/maintenance/plans/{plan.pk}/schedules/new/", {
        "trigger_type": "METER", "meter": str(other.pk), "interval_value": "100", "lead_days": "0",
        "window_start_time": "08:00", "window_hours": "8", "reminder_days": "0"})
    assert r.status_code == 400  # not even offered by the form: the meter belongs to another asset


def test_cross_tenant_ui_access(ui, org_b, make_member, make_site, make_asset):
    plan = make_plan(ui)
    sch = time_schedule(plan)
    b = make_member(org_b, "planner@beta.test", "maintenance_planner")
    cb = client_for(b.user)
    for url in (f"/app/maintenance/plans/{plan.pk}/", f"/app/maintenance/plans/{plan.pk}/edit/",
                f"/app/maintenance/plans/{plan.pk}/schedules/new/", f"/app/maintenance/schedules/{sch.pk}/",
                f"/app/maintenance/schedules/{sch.pk}/edit/"):
        assert cb.get(url).status_code == 404, url
    for url in (f"/app/maintenance/plans/{plan.pk}/active/", f"/app/maintenance/schedules/{sch.pk}/generate/",
                f"/app/maintenance/schedules/{sch.pk}/disable/", f"/app/maintenance/plans/{plan.pk}/schedules/new/"):
        assert cb.post(url, {"active": "0"}).status_code == 404, url
    assert plan.name not in cb.get("/app/maintenance/plans/").content.decode()
    assert not MaintenanceCycle.objects.exists()
    plan.refresh_from_db()
    assert plan.is_active


def test_site_scoped_planner_cannot_reach_other_sites(ui, make_scoped_member):
    plan = make_plan(ui)  # site 1
    sch = time_schedule(plan)
    scoped = make_scoped_member(ui["org"], "planner2@alpha.test", "maintenance_planner", [ui["site2"]])
    c = client_for(scoped.user)
    assert plan.name not in c.get("/app/maintenance/plans/").content.decode()
    assert c.get(f"/app/maintenance/plans/{plan.pk}/").status_code == 404
    assert c.post(f"/app/maintenance/schedules/{sch.pk}/generate/").status_code == 404
    assert c.get(f"/app/maintenance/plans/new/?asset={ui['asset'].pk}").status_code == 200
    r = c.post("/app/maintenance/plans/new/", {"asset": str(ui["asset"].pk), "name": "Sneaky plan",
                                               "priority": "HIGH"})
    assert r.status_code == 400  # site-1 asset is not even a valid choice for this user
    assert not MaintenancePlan.objects.filter(name="Sneaky plan").exists()


def test_csrf_is_enforced(ui):
    plan = make_plan(ui)
    c = Client(enforce_csrf_checks=True)
    c.force_login(ui["planner"].user)
    assert c.post(f"/app/maintenance/plans/{plan.pk}/active/", {"active": "0"}).status_code == 403
    plan.refresh_from_db()
    assert plan.is_active
