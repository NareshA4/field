"""M04 REST API: RBAC, tenant isolation, site scope, IDOR, validation envelope, generation over HTTP and the
PM source on the work order."""
import uuid
from datetime import date

import pytest
from rest_framework.test import APIClient

from apps.assets import services as asset_services
from apps.maintenance import services as pm
from apps.maintenance.models import MaintenanceCycle, MaintenancePlan
from apps.tenancy.models import Membership
from tests.pm_support import T0, freeze, make_plan, time_schedule

pytestmark = pytest.mark.django_db


@pytest.fixture
def ctx(pm_, as_user, org_b, make_member, make_site, make_asset, monkeypatch):
    freeze(monkeypatch, T0)
    p = pm_
    b_planner = make_member(org_b, "planner@beta.test", "maintenance_planner")
    b_site = make_site(org_b, "B1")
    b_asset = make_asset(org_b, b_site, "B-PUMP")
    b_meter = asset_services.create_meter(b_asset, name="Hours", unit="h", actor=None)
    b_plan = pm.create_plan(org_b, asset=b_asset, name="Beta service", actor=b_planner.user)
    return {**p, "b_planner": b_planner, "b_asset": b_asset, "b_meter": b_meter, "b_plan": b_plan,
            "c": {k: as_user(p[k].user, p["org"]) for k in ("planner", "ops", "sup", "tech", "reader")},
            "cb": as_user(b_planner.user, org_b)}


def err(r):
    return r.json()["error"]["code"]


def plan_body(ctx, **kw):
    return {"asset": str(ctx["asset"].pk), "name": "Pump service", "priority": "HIGH", **kw}


SCHEDULE = {"trigger_type": "TIME", "frequency": "MONTHLY", "interval_count": 1, "start_date": "2026-03-10"}


# --- plans -----------------------------------------------------------------------------------------------------------


def test_plan_permissions_and_validation(ctx):
    c = ctx["c"]
    for who in ("tech", "sup", "reader"):
        assert c[who].post("/api/v1/maintenance-plans/", plan_body(ctx)).status_code == 403, who
    r = c["planner"].post("/api/v1/maintenance-plans/", plan_body(ctx))
    assert r.status_code == 201 and r.json()["site"] == str(ctx["site"].pk) and r.json()["is_active"] is True
    pid = r.json()["id"]
    assert c["planner"].post("/api/v1/maintenance-plans/", plan_body(ctx)).status_code == 409
    assert err(c["planner"].post("/api/v1/maintenance-plans/", plan_body(ctx, name="x", priority="SUPER"))) == (
        "validation_failed")
    assert c["ops"].patch(f"/api/v1/maintenance-plans/{pid}/", {"name": "Renamed plan"}).json()["name"] == "Renamed plan"
    for who in ("tech", "sup", "reader"):
        assert c[who].patch(f"/api/v1/maintenance-plans/{pid}/", {"name": "Hacked"}).status_code == 403
        assert c[who].post(f"/api/v1/maintenance-plans/{pid}/disable/").status_code == 403
    assert c["planner"].post(f"/api/v1/maintenance-plans/{pid}/disable/").json()["is_active"] is False
    assert c["planner"].post(f"/api/v1/maintenance-plans/{pid}/enable/").json()["is_active"] is True
    for who in ("planner", "ops", "sup", "reader"):  # everyone with maintenance.view reads
        assert c[who].get("/api/v1/maintenance-plans/").json()["count"] == 1, who
    assert c["tech"].get("/api/v1/maintenance-plans/").status_code == 403
    r = c["planner"].post("/api/v1/maintenance-plans/", plan_body(ctx, name="Other", asset=str(uuid.uuid4())))
    assert r.status_code == 404
    assert c["planner"].get("/api/v1/maintenance-plans/?active=0").json()["count"] == 0
    assert c["planner"].get("/api/v1/maintenance-plans/?q=Renamed").json()["count"] == 1
    assert c["planner"].get("/api/v1/maintenance-plans/?site=garbage").json()["count"] == 0


def test_plan_tenant_isolation_idor_and_site_scope(ctx, api, make_scoped_member, as_user):
    cb = ctx["cb"]
    plan = make_plan(ctx)
    url = f"/api/v1/maintenance-plans/{plan.pk}"
    assert api.get("/api/v1/maintenance-plans/").status_code in (401, 403)
    assert cb.get(f"{url}/").status_code == 404
    assert cb.patch(f"{url}/", {"name": "stolen"}).status_code == 404
    assert cb.post(f"{url}/disable/").status_code == 404
    assert cb.get(f"{url}/schedules/").status_code == 404
    assert cb.post(f"{url}/schedules/", SCHEDULE).status_code == 404
    assert cb.get(f"/api/v1/maintenance-plans/{uuid.uuid4()}/").status_code == 404
    assert [x["name"] for x in cb.get("/api/v1/maintenance-plans/").json()["results"]] == ["Beta service"]
    assert cb.post("/api/v1/maintenance-plans/", plan_body(ctx)).status_code == 404  # Alpha's asset id
    # a planner scoped to site 2 sees nothing of site 1 and cannot create there
    scoped = make_scoped_member(ctx["org"], "planner2@alpha.test", "maintenance_planner", [ctx["site2"]])
    sc = as_user(scoped.user, ctx["org"])
    assert sc.get("/api/v1/maintenance-plans/").json()["count"] == 0
    assert sc.get(f"{url}/").status_code == 404
    assert sc.patch(f"{url}/", {"name": "x"}).status_code == 404
    assert sc.post("/api/v1/maintenance-plans/", plan_body(ctx)).status_code == 404
    r = sc.post("/api/v1/maintenance-plans/", plan_body(ctx, asset=str(ctx["asset2"].pk), name="Fan service"))
    assert r.status_code == 201
    plan.refresh_from_db()
    assert plan.name == "Pump service" and plan.is_active


def test_suspended_member_cannot_use_the_api(ctx):
    m = ctx["planner"]
    m.status = Membership.Status.SUSPENDED
    m.save(update_fields=["status"])
    assert ctx["c"]["planner"].get("/api/v1/maintenance-plans/").status_code in (401, 403)
    assert ctx["c"]["planner"].post("/api/v1/maintenance-plans/", plan_body(ctx)).status_code in (401, 403)


# --- schedules ---------------------------------------------------------------------------------------------------------


def test_schedule_lifecycle_over_the_api(ctx):
    c = ctx["c"]
    plan = make_plan(ctx)
    base = f"/api/v1/maintenance-plans/{plan.pk}/schedules/"
    for who in ("tech", "sup", "reader"):
        assert c[who].post(base, SCHEDULE).status_code == 403, who
    r = c["planner"].post(base, SCHEDULE)
    assert r.status_code == 201
    body = r.json()
    assert (body["next_sequence"], body["next_due_date"], body["state"], body["description_text"]) == (
        0, "2026-03-10", "DUE", "every 1 month")
    sid = body["id"]
    assert c["planner"].post(base, SCHEDULE).status_code == 409  # duplicate
    for bad in ({**SCHEDULE, "frequency": "HOURLY"}, {**SCHEDULE, "interval_count": 0}, {"trigger_type": "TIME"},
                {**SCHEDULE, "lead_days": 90}, {"trigger_type": "METER"}, {"trigger_type": "NOPE"}):
        assert c["planner"].post(base, bad).status_code == 400, bad
    assert c["sup"].get(base).json()["count"] == 1
    assert c["tech"].get(base).status_code == 403
    one = f"/api/v1/maintenance-schedules/{sid}/"
    assert c["planner"].patch(one, {"lead_days": 5, "window_hours": 4}).json()["lead_days"] == 5
    assert c["planner"].patch(one, {"trigger_type": "METER"}).status_code == 400
    assert c["sup"].patch(one, {"lead_days": 1}).status_code == 403
    assert c["planner"].post(f"{one}disable/").json()["is_active"] is False
    assert c["planner"].get(f"{one}").json()["state"] == "DISABLED"
    assert c["tech"].post(f"{one}enable/").status_code == 403
    assert c["planner"].post(f"{one}enable/").json()["is_active"] is True
    assert c["planner"].get("/api/v1/maintenance-schedules/?state=DISABLED").json()["count"] == 0
    assert c["planner"].get("/api/v1/maintenance-schedules/?state=DUE").json()["count"] == 1


def test_meter_schedule_over_the_api_and_cross_tenant_meter(ctx):
    c, cb = ctx["c"], ctx["cb"]
    plan = make_plan(ctx)
    meter = asset_services.create_meter(ctx["asset"], name="Hours", unit="h", actor=None)
    base = f"/api/v1/maintenance-plans/{plan.pk}/schedules/"
    body = {"trigger_type": "METER", "meter": str(meter.pk), "interval_value": "500"}
    r = c["planner"].post(base, body)
    assert r.status_code == 201 and r.json()["next_due_value"] == "500.000" and r.json()["state"] == "SCHEDULED"
    other = asset_services.create_meter(ctx["asset2"], name="Hours", unit="h", actor=None)
    r = c["planner"].post(base, {**body, "meter": str(other.pk), "interval_value": "100"})
    assert r.status_code == 400 and err(r) == "meter_asset_mismatch"
    assert c["planner"].post(base, {**body, "meter": str(ctx["b_meter"].pk), "interval_value": "100"}).status_code == 404
    assert c["planner"].post(base, {**body, "meter": str(uuid.uuid4()), "interval_value": "100"}).status_code == 404
    assert cb.post(f"/api/v1/maintenance-plans/{ctx['b_plan'].pk}/schedules/", body).status_code == 404  # Alpha meter


# --- generation --------------------------------------------------------------------------------------------------------


def test_generate_over_the_api_links_the_source_and_blocks_a_second_early_order(ctx):
    c, cb = ctx["c"], ctx["cb"]
    plan = make_plan(ctx)
    sch = time_schedule(plan, start_date=date(2026, 6, 1))
    url = f"/api/v1/maintenance-schedules/{sch.pk}/generate/"
    for who in ("tech", "sup", "reader"):
        assert c[who].post(url).status_code == 403, who
    assert cb.post(url).status_code == 404
    r = c["planner"].post(url)
    assert r.status_code == 201
    cycle = r.json()
    assert cycle["sequence"] == 0 and cycle["state"] == "GENERATED" and cycle["trigger"] == "manual"
    assert cycle["work_order_status"] == "PLANNED" and cycle["work_order_number"].startswith("WO-")
    r2 = c["planner"].post(url)
    assert r2.status_code == 409 and err(r2) == "previous_cycle_open"
    wo = c["planner"].get(f"/api/v1/work-orders/{cycle['work_order']}/").json()
    assert (wo["source_type"], wo["source_id"], wo["work_type"]) == ("PREVENTIVE_MAINTENANCE", cycle["id"],
                                                                        "PREVENTIVE")
    assert MaintenanceCycle.objects.count() == 1
    # reads
    assert c["sup"].get("/api/v1/maintenance-cycles/").json()["count"] == 1
    assert c["sup"].get(f"/api/v1/maintenance-cycles/{cycle['id']}/").json()["plan_name"] == "Pump service"
    assert c["sup"].get(f"/api/v1/maintenance-schedules/{sch.pk}/cycles/").json()["count"] == 1
    assert c["planner"].get(f"/api/v1/maintenance-cycles/?plan={plan.pk}").json()["count"] == 1
    assert c["planner"].get("/api/v1/maintenance-cycles/?plan=garbage").json()["count"] == 0
    assert c["tech"].get("/api/v1/maintenance-cycles/").status_code == 403
    assert cb.get("/api/v1/maintenance-cycles/").json()["count"] == 0
    assert cb.get(f"/api/v1/maintenance-cycles/{cycle['id']}/").status_code == 404
    assert cb.get(f"/api/v1/maintenance-schedules/{sch.pk}/").status_code == 404


def test_generate_refused_for_disabled_plan_and_state_follows_the_work_order(ctx):
    c = ctx["c"]
    plan = make_plan(ctx)
    sch = time_schedule(plan, start_date=date(2026, 3, 1))  # next occurrence is 2026-04-01
    url = f"/api/v1/maintenance-schedules/{sch.pk}/generate/"
    c["planner"].post(f"/api/v1/maintenance-plans/{plan.pk}/disable/")
    r = c["planner"].post(url)
    assert r.status_code == 409 and err(r) == "plan_disabled"
    assert MaintenanceCycle.objects.count() == 0
    c["planner"].post(f"/api/v1/maintenance-plans/{plan.pk}/enable/")
    assert c["planner"].get(f"/api/v1/maintenance-schedules/{sch.pk}/").json()["next_due_date"] == "2026-04-01"
    r = c["planner"].post(url)
    assert r.status_code == 201 and r.json()["sequence"] == 1  # occurrence 0 (2026-03-01) was not replayed
    from apps.workorders import services as wos

    wos.transition(MaintenanceCycle.objects.get().work_order, action="assign", actor=ctx["planner"].user,
                   membership=ctx["planner"], technician=ctx["tech"])
    cycle_id = MaintenanceCycle.objects.get().pk
    assert c["planner"].get(f"/api/v1/maintenance-cycles/{cycle_id}/").json()["state"] == "ASSIGNED"
    assert MaintenancePlan.objects.filter(organization=ctx["org"]).count() == 1
    assert APIClient().get("/api/v1/maintenance-cycles/").status_code in (401, 403)
