"""M09 REST API: RBAC (allowed / wrong role / unauthenticated), tenant isolation, site scope, IDOR (guessed ids),
validation envelope and the full request -> reserve -> issue -> consume -> return flow over HTTP."""
import uuid

import pytest
from rest_framework.test import APIClient

from apps.inventory import services as inv
from apps.inventory.models import StockBalance, StockMovement, WorkOrderPart
from apps.tenancy.models import Membership
from tests.phase3_support import wo_in_progress

pytestmark = pytest.mark.django_db


@pytest.fixture
def ctx(inv_, as_user, org_b, make_member, make_site):
    p = inv_
    b_stores = make_member(org_b, "stores@beta.test", "stores_manager")
    b_site = make_site(org_b, "B1")
    b_wh = inv.create_warehouse(org_b, site=b_site, code="BMAIN", name="Beta store", actor=b_stores.user)
    b_part = inv.create_part(org_b, part_number="B-PART", name="Beta part", actor=b_stores.user)
    inv.receive(b_wh, b_part, 9, actor=b_stores.user)
    return {**p, "org_b": org_b, "b_stores": b_stores, "b_wh": b_wh, "b_part": b_part,
            "c": {k: as_user(p[k].user, p["org"]) for k in ("stores", "tech", "tech2", "planner", "sup", "reader",
                                                            "ops")},
            "cb": as_user(b_stores.user, org_b)}


def err(r):
    return r.json()["error"]["code"]


# --- parts --------------------------------------------------------------------------------------------------------


def test_part_crud_permissions(ctx):
    c = ctx["c"]
    body = {"part_number": "NEW-1", "name": "New part", "min_stock": "1", "max_stock": "9"}
    assert c["tech"].post("/api/v1/parts/", body).status_code == 403
    assert c["planner"].post("/api/v1/parts/", body).status_code == 403
    assert c["reader"].post("/api/v1/parts/", body).status_code == 403
    r = c["stores"].post("/api/v1/parts/", body)
    assert r.status_code == 201 and r.json()["part_number"] == "NEW-1"
    pid = r.json()["id"]
    assert c["stores"].post("/api/v1/parts/", body).status_code == 409  # duplicate part number
    assert c["stores"].patch(f"/api/v1/parts/{pid}/", {"name": "Renamed"}).json()["name"] == "Renamed"
    assert c["tech"].patch(f"/api/v1/parts/{pid}/", {"name": "Hacked"}).status_code == 403
    assert c["tech"].post(f"/api/v1/parts/{pid}/deactivate/").status_code == 403
    assert c["stores"].post(f"/api/v1/parts/{pid}/deactivate/").json()["is_active"] is False
    assert c["stores"].post(f"/api/v1/parts/{pid}/activate/").json()["is_active"] is True
    # everyone with part.view reads the catalogue (a technician needs it to request parts)
    for who in ("tech", "planner", "reader", "stores"):
        assert c[who].get("/api/v1/parts/").status_code == 200
    r = c["stores"].patch(f"/api/v1/parts/{pid}/", {"min_stock": "9", "max_stock": "1"})
    assert r.status_code == 400 and err(r) == "levels_invalid"
    assert c["stores"].post("/api/v1/parts/", {"part_number": "", "name": "x"}).status_code == 400


def test_parts_unauthenticated_and_cross_tenant_and_guessed_ids(ctx, api):
    assert api.get("/api/v1/parts/").status_code in (401, 403)
    cb, c = ctx["cb"], ctx["c"]
    pid = str(ctx["part"].pk)
    assert c["stores"].get(f"/api/v1/parts/{pid}/").status_code == 200
    assert cb.get(f"/api/v1/parts/{pid}/").status_code == 404
    assert cb.patch(f"/api/v1/parts/{pid}/", {"name": "x"}).status_code == 404
    assert cb.post(f"/api/v1/parts/{pid}/deactivate/").status_code == 404
    assert cb.get(f"/api/v1/parts/{uuid.uuid4()}/").status_code == 404
    assert cb.get("/api/v1/parts/not-a-uuid/").status_code == 404
    numbers = [x["part_number"] for x in cb.get("/api/v1/parts/").json()["results"]]
    assert numbers == ["B-PART"]
    ctx["part"].refresh_from_db()
    assert ctx["part"].is_active and ctx["part"].name == "Mechanical seal"


def test_suspended_member_is_locked_out(ctx):
    m = ctx["stores"]
    m.status = Membership.Status.SUSPENDED
    m.save(update_fields=["status"])
    assert ctx["c"]["stores"].get("/api/v1/parts/").status_code in (401, 403)
    assert ctx["c"]["stores"].post("/api/v1/stock-balances/receive/", {}).status_code in (401, 403)


# --- warehouses ---------------------------------------------------------------------------------------------------


def test_warehouse_permissions_site_scope_and_tenancy(ctx, make_scoped_member, as_user):
    c, cb = ctx["c"], ctx["cb"]
    body = {"site": str(ctx["site"].pk), "code": "NEW-WH", "name": "New warehouse"}
    assert c["tech"].post("/api/v1/warehouses/", body).status_code == 403
    assert c["planner"].post("/api/v1/warehouses/", body).status_code == 403  # can view, cannot manage
    r = c["stores"].post("/api/v1/warehouses/", body)
    assert r.status_code == 201
    wid = r.json()["id"]
    assert c["stores"].post("/api/v1/warehouses/", body).status_code == 409
    assert cb.post("/api/v1/warehouses/", body).status_code == 404  # Alpha's site id is not visible to Beta
    assert cb.get(f"/api/v1/warehouses/{wid}/").status_code == 404
    assert cb.patch(f"/api/v1/warehouses/{wid}/", {"name": "x"}).status_code == 404
    assert cb.post(f"/api/v1/warehouses/{wid}/deactivate/").status_code == 404
    assert c["stores"].patch(f"/api/v1/warehouses/{wid}/", {"name": "Renamed"}).json()["name"] == "Renamed"
    assert c["stores"].post(f"/api/v1/warehouses/{wid}/deactivate/").json()["is_active"] is False
    # a stores member scoped to site 2 sees only site 2 and cannot touch site 1
    scoped = make_scoped_member(ctx["org"], "stores2@alpha.test", "stores_manager", [ctx["site2"]])
    sc = as_user(scoped.user, ctx["org"])
    codes = {w["code"] for w in sc.get("/api/v1/warehouses/").json()["results"]}
    assert codes == {"REMOTE"}
    assert sc.get(f"/api/v1/warehouses/{ctx['wh'].pk}/").status_code == 404
    assert sc.post("/api/v1/warehouses/", body).status_code == 404
    assert sc.patch(f"/api/v1/warehouses/{ctx['wh'].pk}/", {"name": "x"}).status_code == 404
    assert sc.post("/api/v1/warehouses/", {**body, "code": "S2-NEW", "site": str(ctx["site2"].pk)}).status_code == 201


# --- stock --------------------------------------------------------------------------------------------------------


def test_stock_endpoints_rbac_and_validation(ctx):
    c = ctx["c"]
    body = {"warehouse": str(ctx["wh"].pk), "part": str(ctx["part"].pk), "quantity": "10"}
    for who in ("tech", "planner", "reader"):
        assert c[who].post("/api/v1/stock-balances/receive/", body).status_code == 403, who
    assert c["tech"].get("/api/v1/stock-balances/").status_code == 403  # a technician cannot browse stock levels
    r = c["stores"].post("/api/v1/stock-balances/receive/", body)
    assert r.status_code == 201 and r.json()["movement_type"] == "RECEIPT" and r.json()["on_hand_after"] == "10.000"
    for bad in ("0", "-5", "abc", "1.2345"):
        r = c["stores"].post("/api/v1/stock-balances/receive/", {**body, "quantity": bad})
        assert r.status_code == 400, bad
    assert c["stores"].post("/api/v1/stock-balances/receive/", {**body, "part": str(uuid.uuid4())}).status_code == 404
    rows = c["planner"].get("/api/v1/stock-balances/").json()["results"]  # planners may view
    assert len(rows) == 1 and rows[0]["on_hand"] == "10.000" and rows[0]["available"] == "10.000"
    assert c["stores"].get("/api/v1/stock-balances/?low=1").json()["count"] == 0
    assert c["stores"].get("/api/v1/stock-balances/?warehouse=garbage").json()["count"] == 0
    # adjust / levels / transfer need their own permissions
    adj = {"warehouse": str(ctx["wh"].pk), "part": str(ctx["part"].pk), "delta": "-4", "reason": "damaged"}
    assert c["planner"].post("/api/v1/stock-balances/adjust/", adj).status_code == 403
    assert c["stores"].post("/api/v1/stock-balances/adjust/", adj).status_code == 201
    r = c["stores"].post("/api/v1/stock-balances/adjust/", {**adj, "delta": "-50"})
    assert r.status_code == 409 and err(r) == "insufficient_available"
    assert c["stores"].post("/api/v1/stock-balances/adjust/", {**adj, "reason": ""}).status_code == 400
    lv = {"warehouse": str(ctx["wh"].pk), "part": str(ctx["part"].pk), "min_level": "3", "max_level": "30"}
    assert c["stores"].post("/api/v1/stock-balances/levels/", lv).json()["effective_min"] == "3.000"
    tr = {"source": str(ctx["wh"].pk), "target": str(ctx["wh2"].pk), "part": str(ctx["part"].pk), "quantity": "2"}
    assert c["planner"].post("/api/v1/stock-balances/transfer/", tr).status_code == 403
    r = c["stores"].post("/api/v1/stock-balances/transfer/", tr)
    assert r.status_code == 201 and [m["movement_type"] for m in r.json()] == ["TRANSFER_OUT", "TRANSFER_IN"]
    assert err(c["stores"].post("/api/v1/stock-balances/transfer/", {**tr, "target": tr["source"]})) == "same_warehouse"
    assert StockBalance.objects.get(warehouse=ctx["wh"], part=ctx["part"]).on_hand == 4


def test_stock_is_tenant_isolated_and_idor_safe(ctx):
    c, cb = ctx["c"], ctx["cb"]
    inv.receive(ctx["wh"], ctx["part"], 5, actor=ctx["stores"].user)
    bal = StockBalance.objects.get(warehouse=ctx["wh"], part=ctx["part"])
    assert cb.get(f"/api/v1/stock-balances/{bal.pk}/").status_code == 404
    assert cb.get(f"/api/v1/stock-balances/{bal.pk}/movements/").status_code == 404
    assert [b["part_number"] for b in cb.get("/api/v1/stock-balances/").json()["results"]] == ["B-PART"]
    # Beta cannot receive into / adjust / transfer Alpha's warehouse or use Alpha's part
    for path, body in (
        ("receive", {"warehouse": str(ctx["wh"].pk), "part": str(ctx["b_part"].pk), "quantity": "1"}),
        ("receive", {"warehouse": str(ctx["b_wh"].pk), "part": str(ctx["part"].pk), "quantity": "1"}),
        ("adjust", {"warehouse": str(ctx["wh"].pk), "part": str(ctx["b_part"].pk), "delta": "-1", "reason": "steal"}),
        ("transfer", {"source": str(ctx["wh"].pk), "target": str(ctx["b_wh"].pk), "part": str(ctx["b_part"].pk),
                      "quantity": "1"}),
        ("transfer", {"source": str(ctx["b_wh"].pk), "target": str(ctx["wh"].pk), "part": str(ctx["b_part"].pk),
                      "quantity": "1"}),
    ):
        assert cb.post(f"/api/v1/stock-balances/{path}/", body).status_code == 404, (path, body)
    assert StockBalance.objects.get(pk=bal.pk).on_hand == 5
    assert StockBalance.objects.get(warehouse=ctx["b_wh"], part=ctx["b_part"]).on_hand == 9
    # a site-2-only reader cannot see the site-1 balance
    assert c["stores"].get(f"/api/v1/stock-balances/{bal.pk}/").status_code == 200


def test_site_scoped_user_cannot_operate_on_other_sites_stock(ctx, make_scoped_member, as_user):
    inv.receive(ctx["wh"], ctx["part"], 5, actor=ctx["stores"].user)
    scoped = make_scoped_member(ctx["org"], "stores2@alpha.test", "stores_manager", [ctx["site2"]])
    sc = as_user(scoped.user, ctx["org"])
    body = {"warehouse": str(ctx["wh"].pk), "part": str(ctx["part"].pk), "quantity": "1"}
    assert sc.post("/api/v1/stock-balances/receive/", body).status_code == 404
    assert sc.get("/api/v1/stock-balances/").json()["count"] == 0
    assert sc.get("/api/v1/stock-movements/").json()["count"] == 0
    assert sc.post("/api/v1/stock-balances/receive/", {**body, "warehouse": str(ctx["whs2"].pk)}).status_code == 201
    assert StockBalance.objects.get(warehouse=ctx["wh"], part=ctx["part"]).on_hand == 5


# --- work-order part flow over HTTP ---------------------------------------------------------------------------------


def test_full_part_flow_over_the_api(ctx):
    c = ctx["c"]
    inv.receive(ctx["wh"], ctx["part"], 10, actor=ctx["stores"].user)
    wo = wo_in_progress(ctx)
    r = c["tech"].post("/api/v1/work-order-parts/", {"work_order": str(wo.pk), "part": str(ctx["part"].pk),
                                                     "quantity": "5"})
    assert r.status_code == 201 and r.json()["status"] == "REQUESTED"
    lid = r.json()["id"]
    url = f"/api/v1/work-order-parts/{lid}"
    wh = str(ctx["wh"].pk)
    # the technician may request and consume, never reserve / issue / return / reconcile
    for action, body in (("reserve", {"warehouse": wh}), ("issue", {"warehouse": wh, "quantity": "1"}),
                         ("return", {"quantity": "1"}), ("reconcile", {}), ("release", {})):
        assert c["tech"].post(f"{url}/{action}/", body).status_code == 403, action
    r = c["stores"].post(f"{url}/reserve/", {"warehouse": wh, "quantity": "3"})
    assert r.status_code == 200 and r.json()["status"] == "RESERVED" and r.json()["reserved"] == "3.000"
    r = c["stores"].post(f"{url}/issue/", {"warehouse": wh, "quantity": "4"})
    assert r.status_code == 200 and r.json()["quantity_issued"] == "4.000" and r.json()["status"] == "ISSUED"
    bal = StockBalance.objects.get(warehouse=ctx["wh"], part=ctx["part"])
    assert (bal.on_hand, bal.reserved) == (6, 0)
    assert c["stores"].post(f"{url}/issue/", {"warehouse": wh, "quantity": "5"}).status_code == 409  # > requirement
    assert c["tech2"].post(f"{url}/consume/", {"quantity": "1"}).status_code == 404  # not their job: invisible
    r = c["tech"].post(f"{url}/consume/", {"quantity": "3"})
    assert r.status_code == 200 and r.json()["quantity_consumed"] == "3.000"
    r = c["tech"].get(f"/api/v1/work-orders/{wo.pk}/materials/").json()
    assert r["results"][0]["stock_backed"] is True and r["results"][0]["quantity"] == "3.000"
    r = c["stores"].post(f"{url}/return/", {"quantity": "5"})
    assert r.status_code == 409 and err(r) == "exceeds_outstanding"
    assert c["stores"].post(f"{url}/return/", {"quantity": "1", "reason": "unused"}).status_code == 200
    assert c["stores"].post(f"{url}/reconcile/").json()["status"] == "RECONCILED"
    assert StockBalance.objects.get(warehouse=ctx["wh"], part=ctx["part"]).on_hand == 7
    mv = c["stores"].get(f"{url}/movements/").json()
    assert [m["movement_type"] for m in reversed(mv["results"])] == ["RESERVE", "ISSUE", "RETURN"]
    assert c["tech"].get(f"{url}/movements/").status_code == 403  # stock levels are not a technician's business
    assert c["tech"].get(f"{url}/").json()["status"] == "RECONCILED"
    assert c["stores"].get(f"/api/v1/work-order-parts/?work_order={wo.pk}").json()["count"] == 1


def test_part_line_idor_and_tenancy(ctx):
    inv.receive(ctx["wh"], ctx["part"], 10, actor=ctx["stores"].user)
    wo = wo_in_progress(ctx)
    line = inv.request_part(wo, ctx["part"], 2, actor=ctx["tech"].user, membership=ctx["tech"])
    cb, c = ctx["cb"], ctx["c"]
    url = f"/api/v1/work-order-parts/{line.pk}"
    assert cb.get(f"{url}/").status_code == 404
    assert cb.post(f"{url}/issue/", {"warehouse": str(ctx["b_wh"].pk), "quantity": "1"}).status_code == 404
    assert cb.post(f"{url}/cancel/").status_code == 404
    assert cb.post("/api/v1/work-order-parts/", {"work_order": str(wo.pk), "part": str(ctx["b_part"].pk),
                                                 "quantity": "1"}).status_code == 404
    assert cb.get("/api/v1/work-order-parts/").json()["count"] == 0
    # Alpha stores cannot issue from Beta's warehouse (not even visible), nor Beta's part on Alpha's order
    assert c["stores"].post(f"{url}/issue/", {"warehouse": str(ctx["b_wh"].pk), "quantity": "1"}).status_code == 404
    assert c["stores"].post("/api/v1/work-order-parts/", {"work_order": str(wo.pk), "part": str(ctx["b_part"].pk),
                                                          "quantity": "1"}).status_code == 404
    assert c["stores"].post(f"{url}/issue/", {"warehouse": str(ctx["whs2"].pk), "quantity": "1"}).status_code == 400
    assert c["stores"].post(f"{url}/issue/", {"warehouse": str(uuid.uuid4()), "quantity": "1"}).status_code == 404
    assert c["stores"].post(f"{url}/bogus/").status_code == 404
    assert StockBalance.objects.get(warehouse=ctx["wh"], part=ctx["part"]).on_hand == 10
    assert not StockMovement.objects.filter(movement_type="ISSUE").exists()
    assert WorkOrderPart.objects.count() == 1


def test_site_scoped_stores_cannot_touch_another_sites_work_order(ctx, make_scoped_member, as_user):
    inv.receive(ctx["wh"], ctx["part"], 10, actor=ctx["stores"].user)
    wo = wo_in_progress(ctx)
    line = inv.request_part(wo, ctx["part"], 2, actor=ctx["tech"].user, membership=ctx["tech"])
    scoped = make_scoped_member(ctx["org"], "stores2@alpha.test", "stores_manager", [ctx["site2"]])
    sc = as_user(scoped.user, ctx["org"])
    assert sc.get(f"/api/v1/work-order-parts/{line.pk}/").status_code == 404
    assert sc.post(f"/api/v1/work-order-parts/{line.pk}/issue/", {"warehouse": str(ctx["whs2"].pk),
                                                                  "quantity": "1"}).status_code == 404
    assert sc.get("/api/v1/work-order-parts/").json()["count"] == 0


def test_reader_and_planner_roles_on_part_lines(ctx):
    inv.receive(ctx["wh"], ctx["part"], 10, actor=ctx["stores"].user)
    wo = wo_in_progress(ctx)
    c = ctx["c"]
    # the planner may request parts for any order of the site, the auditor may only look
    r = c["planner"].post("/api/v1/work-order-parts/", {"work_order": str(wo.pk), "part": str(ctx["part"].pk),
                                                        "quantity": "1"})
    assert r.status_code == 201
    assert c["reader"].post("/api/v1/work-order-parts/", {"work_order": str(wo.pk), "part": str(ctx["part"].pk),
                                                          "quantity": "1"}).status_code == 403
    assert c["tech2"].post("/api/v1/work-order-parts/", {"work_order": str(wo.pk), "part": str(ctx["part"].pk),
                                                         "quantity": "1"}).status_code == 404  # order not visible
    lid = r.json()["id"]
    assert c["planner"].post(f"/api/v1/work-order-parts/{lid}/issue/", {"warehouse": str(ctx["wh"].pk),
                                                                        "quantity": "1"}).status_code == 403
    assert c["planner"].post(f"/api/v1/work-order-parts/{lid}/cancel/").status_code == 200  # untouched line
    assert c["reader"].get("/api/v1/work-order-parts/").status_code == 200


def test_movements_and_reservations_read_endpoints(ctx):
    c = ctx["c"]
    inv.receive(ctx["wh"], ctx["part"], 10, actor=ctx["stores"].user)
    wo = wo_in_progress(ctx)
    line = inv.request_part(wo, ctx["part"], 4, actor=ctx["tech"].user, membership=ctx["tech"])
    inv.reserve(line, ctx["wh"], actor=ctx["stores"].user)
    assert c["tech"].get("/api/v1/stock-movements/").status_code == 403
    assert c["tech"].get("/api/v1/part-reservations/").status_code == 403
    page = c["stores"].get(f"/api/v1/stock-movements/?work_order={wo.pk}&type=RESERVE").json()
    assert page["count"] == 1
    res = c["stores"].get("/api/v1/part-reservations/?status=ACTIVE").json()
    assert res["count"] == 1 and res["results"][0]["quantity"] == "4.000"
    assert c["stores"].get("/api/v1/stock-movements/?type=BOGUS").json()["count"] == 0
    assert ctx["cb"].get("/api/v1/stock-movements/").json()["count"] == 1  # Beta sees only its own receipt
    assert ctx["cb"].get(f"/api/v1/stock-movements/{page['results'][0]['id']}/").status_code == 404
    assert APIClient().get("/api/v1/stock-movements/").status_code in (401, 403)
