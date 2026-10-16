"""M09 UI: pages render, every control posts to a real endpoint that persists, writes the ledger and audits;
forbidden roles / foreign objects are refused; the M07 workspace uses the M09 contract."""
import pytest
from django.test import Client

from apps.audit.models import AuditLog
from apps.inventory import services as inv
from apps.inventory.models import Part, StockBalance, StockMovement, Warehouse, WorkOrderPart
from apps.workorders.models import WorkOrderMaterial
from tests.phase3_support import wo_in_progress

pytestmark = pytest.mark.django_db


def client_for(user):
    c = Client()
    c.force_login(user)
    return c


def flash(response):
    return [str(m) for m in response.context["messages"]] if response.context else []


def follow_flash(client, response):
    return [str(m) for m in client.get(response["Location"]).context["messages"]]


@pytest.fixture
def ui(inv_):
    p = inv_
    p["cl"] = {k: client_for(p[k].user) for k in ("stores", "tech", "tech2", "planner", "reader", "sup")}
    return p


# --- pages -------------------------------------------------------------------------------------------------------


def test_stores_pages_render_with_real_data(ui):
    c = ui["cl"]["stores"]
    inv.receive(ui["wh"], ui["part"], 5, actor=ui["stores"].user)
    bal = StockBalance.objects.get()
    wo = wo_in_progress(ui)
    line = inv.request_part(wo, ui["part"], 2, actor=ui["tech"].user, membership=ui["tech"])
    inv.reserve(line, ui["wh"], actor=ui["stores"].user)
    pages = {
        "/app/inventory/parts/": "SEAL-100", f"/app/inventory/parts/{ui['part'].pk}/": "Mechanical seal",
        "/app/inventory/parts/new/": "Part number", f"/app/inventory/parts/{ui['part'].pk}/edit/": "SEAL-100",
        "/app/inventory/warehouses/": "MAIN", f"/app/inventory/warehouses/{ui['wh'].pk}/": "Main store",
        "/app/inventory/warehouses/new/": "Code", f"/app/inventory/warehouses/{ui['wh'].pk}/edit/": "Main store",
        "/app/inventory/stock/": "SEAL-100", f"/app/inventory/stock/{bal.pk}/": "Movement history",
        "/app/inventory/stock/receive/": "Receive stock", "/app/inventory/stock/transfer/": "Transfer stock",
        "/app/inventory/reservations/": wo.number, "/app/inventory/movements/": "RECEIPT",
        f"/app/work-orders/{wo.pk}/parts/": "Mechanical seal",
    }
    for url, needle in pages.items():
        r = c.get(url)
        assert r.status_code == 200, url
        assert needle in r.content.decode(), url
    assert "Stock" in c.get("/app/").content.decode()  # navigation entry


def test_filters_and_pagination_parameters_do_not_break_pages(ui):
    c = ui["cl"]["stores"]
    for url in ("/app/inventory/parts/?q=seal&active=1", "/app/inventory/parts/?active=0&page=99",
                "/app/inventory/warehouses/?site=garbage", "/app/inventory/stock/?low=1&warehouse=garbage",
                "/app/inventory/movements/?type=BOGUS&warehouse=nope", "/app/inventory/reservations/?status=BOGUS"):
        assert c.get(url).status_code == 200, url


def test_role_gating_of_pages(ui):
    tech, reader, planner = ui["cl"]["tech"], ui["cl"]["reader"], ui["cl"]["planner"]
    assert tech.get("/app/inventory/parts/").status_code == 200  # the catalogue is readable to request parts
    for url in ("/app/inventory/stock/", "/app/inventory/warehouses/", "/app/inventory/movements/",
                "/app/inventory/reservations/", "/app/inventory/parts/new/", "/app/inventory/warehouses/new/",
                "/app/inventory/stock/receive/", "/app/inventory/stock/transfer/"):
        assert tech.get(url).status_code == 403, url
    for url in ("/app/inventory/parts/new/", "/app/inventory/warehouses/new/", "/app/inventory/stock/receive/",
                "/app/inventory/stock/transfer/"):
        assert reader.get(url).status_code == 403, url
        assert planner.get(url).status_code == 403, url
    assert reader.get("/app/inventory/stock/").status_code == 200
    assert Client().get("/app/inventory/parts/").status_code == 302
    assert Client().post("/app/inventory/stock/receive/", {}).status_code == 302


# --- catalogue / warehouses -----------------------------------------------------------------------------------------


def test_create_edit_deactivate_part_through_the_ui(ui):
    c = ui["cl"]["stores"]
    r = c.post("/app/inventory/parts/new/", {"part_number": "FLT-9", "name": "Oil filter", "unit": "pcs",
                                              "min_stock": "2", "max_stock": "8", "reorder_quantity": "4"})
    assert r.status_code == 302
    part = Part.objects.get(part_number="FLT-9")
    assert (part.min_stock, part.max_stock) == (2, 8)
    assert AuditLog.objects.filter(action="part.created", target_id=str(part.pk)).exists()
    r = c.post("/app/inventory/parts/new/", {"part_number": "flt-9", "name": "dup", "unit": "pcs"})
    assert r.status_code == 400 and "already exists" in r.content.decode()
    r = c.post("/app/inventory/parts/new/", {"part_number": "X", "name": "Bad", "unit": "pcs", "min_stock": "9",
                                             "max_stock": "1"})
    assert r.status_code == 400 and not Part.objects.filter(part_number="X").exists()
    assert c.post(f"/app/inventory/parts/{part.pk}/edit/", {"part_number": "FLT-9", "name": "Oil filter XL",
                                                              "unit": "pcs"}).status_code == 302
    part.refresh_from_db()
    assert part.name == "Oil filter XL"
    assert c.post(f"/app/inventory/parts/{part.pk}/active/", {"active": "0"}).status_code == 302
    part.refresh_from_db()
    assert part.is_active is False
    assert c.post(f"/app/inventory/parts/{part.pk}/active/", {"active": "1"}).status_code == 302
    # forbidden for a technician
    t = ui["cl"]["tech"]
    assert t.post(f"/app/inventory/parts/{part.pk}/active/", {"active": "0"}).status_code == 403
    assert t.post("/app/inventory/parts/new/", {"part_number": "Z", "name": "Z", "unit": "pcs"}).status_code == 403
    part.refresh_from_db()
    assert part.is_active is True


def test_create_edit_deactivate_warehouse_through_the_ui(ui):
    c = ui["cl"]["stores"]
    r = c.post("/app/inventory/warehouses/new/", {"site": str(ui["site"].pk), "code": "YARD", "name": "Yard"})
    assert r.status_code == 302
    wh = Warehouse.objects.get(code="YARD")
    assert wh.site_id == ui["site"].pk
    assert c.post("/app/inventory/warehouses/new/", {"site": str(ui["site"].pk), "code": "yard",
                                                     "name": "dup"}).status_code == 400
    assert c.post(f"/app/inventory/warehouses/{wh.pk}/edit/", {"code": "YARD", "name": "Yard 2"}).status_code == 302
    wh.refresh_from_db()
    assert wh.name == "Yard 2"
    assert c.post(f"/app/inventory/warehouses/{wh.pk}/active/", {"active": "0"}).status_code == 302
    wh.refresh_from_db()
    assert wh.is_active is False
    # with stock inside it refuses
    inv.receive(ui["wh"], ui["part"], 1, actor=ui["stores"].user)
    r = c.post(f"/app/inventory/warehouses/{ui['wh'].pk}/active/", {"active": "0"})
    assert "Move or write off" in " ".join(follow_flash(c, r))
    ui["wh"].refresh_from_db()
    assert ui["wh"].is_active is True
    assert ui["cl"]["tech"].post(f"/app/inventory/warehouses/{ui['wh'].pk}/active/", {"active": "0"}).status_code == 403


# --- stock operations ------------------------------------------------------------------------------------------------


def test_receive_adjust_levels_transfer_through_the_ui(ui):
    c = ui["cl"]["stores"]
    base = {"warehouse": str(ui["wh"].pk), "part": str(ui["part"].pk)}
    r = c.post("/app/inventory/stock/receive/", {**base, "quantity": "10", "reference": "PO-77"})
    assert r.status_code == 302
    bal = StockBalance.objects.get()
    assert bal.on_hand == 10 and StockMovement.objects.get().reference == "PO-77"
    for bad in ("0", "-1", "x"):
        assert c.post("/app/inventory/stock/receive/", {**base, "quantity": bad}).status_code == 400
    assert StockBalance.objects.get().on_hand == 10
    assert c.post(f"/app/inventory/stock/{bal.pk}/adjust/", {"delta": "-3", "reason": "damaged"}).status_code == 302
    r = c.post(f"/app/inventory/stock/{bal.pk}/adjust/", {"delta": "-30", "reason": "too much"})
    assert "reserved stock or go below zero" in " ".join(follow_flash(c, r))
    assert c.post(f"/app/inventory/stock/{bal.pk}/levels/", {"min_level": "2", "max_level": "20",
                                                              "reorder_quantity": "5"}).status_code == 302
    bal.refresh_from_db()
    assert (bal.on_hand, bal.min_level, bal.max_level) == (7, 2, 20)
    r = c.post("/app/inventory/stock/transfer/", {"source": str(ui["wh"].pk), "target": str(ui["wh2"].pk),
                                                   "part": str(ui["part"].pk), "quantity": "3"})
    assert r.status_code == 302
    assert StockBalance.objects.get(warehouse=ui["wh2"]).on_hand == 3
    assert c.post("/app/inventory/stock/transfer/", {"source": str(ui["wh"].pk), "target": str(ui["wh"].pk),
                                                      "part": str(ui["part"].pk), "quantity": "1"}).status_code == 400
    assert c.get(f"/app/inventory/stock/{bal.pk}/").status_code == 200
    # forbidden roles change nothing
    for who in ("tech", "planner", "reader"):
        cl = ui["cl"][who]
        assert cl.post("/app/inventory/stock/receive/", {**base, "quantity": "99"}).status_code == 403
        assert cl.post(f"/app/inventory/stock/{bal.pk}/adjust/", {"delta": "50", "reason": "x"}).status_code in (403, 404)
        assert cl.post("/app/inventory/stock/transfer/", {"source": str(ui["wh"].pk), "target": str(ui["wh2"].pk),
                                                           "part": str(ui["part"].pk), "quantity": "1"}).status_code == 403
    assert StockBalance.objects.get(warehouse=ui["wh"]).on_hand == 4


def test_cross_tenant_ui_access(ui, org_b, make_member, make_site):
    b = make_member(org_b, "stores@beta.test", "stores_manager")
    cb = client_for(b.user)
    inv.receive(ui["wh"], ui["part"], 5, actor=ui["stores"].user)
    bal = StockBalance.objects.get()
    wo = wo_in_progress(ui)
    line = inv.request_part(wo, ui["part"], 2, actor=ui["tech"].user, membership=ui["tech"])
    for url in (f"/app/inventory/parts/{ui['part'].pk}/", f"/app/inventory/parts/{ui['part'].pk}/edit/",
                f"/app/inventory/warehouses/{ui['wh'].pk}/", f"/app/inventory/warehouses/{ui['wh'].pk}/edit/",
                f"/app/inventory/stock/{bal.pk}/", f"/app/work-orders/{wo.pk}/parts/"):
        assert cb.get(url).status_code == 404, url
    for url in (f"/app/inventory/parts/{ui['part'].pk}/active/", f"/app/inventory/warehouses/{ui['wh'].pk}/active/",
                f"/app/inventory/stock/{bal.pk}/adjust/", f"/app/inventory/stock/{bal.pk}/levels/",
                f"/app/work-orders/{wo.pk}/parts/request/", f"/app/inventory/part-lines/{line.pk}/issue/",
                f"/app/inventory/part-lines/{line.pk}/cancel/"):
        assert cb.post(url, {"active": "0", "delta": "-5", "reason": "steal"}).status_code == 404, url
    assert StockBalance.objects.get().on_hand == 5
    ui["part"].refresh_from_db()
    assert ui["part"].is_active


# --- work-order material view ---------------------------------------------------------------------------------------


def test_full_flow_through_ui_pages_and_workspace(ui):
    stores, tech = ui["cl"]["stores"], ui["cl"]["tech"]
    inv.receive(ui["wh"], ui["part"], 10, actor=ui["stores"].user)
    wo = wo_in_progress(ui)
    # 1. the technician requests through the workspace (M07 -> M09)
    job = tech.get(f"/app/workspace/{wo.pk}/")
    assert job.status_code == 200 and "Request part" in job.content.decode()
    r = tech.post(f"/app/workspace/{wo.pk}/parts/request/", {"part": str(ui["part"].pk), "quantity": "4"})
    assert r.status_code == 302
    line = WorkOrderPart.objects.get()
    assert (line.status, line.quantity_requested) == ("REQUESTED", 4)
    assert StockBalance.objects.get().on_hand == 10  # nothing moved
    # the technician cannot reserve / issue / return / reconcile through the inventory endpoint
    for act in ("reserve", "issue", "return", "reconcile", "release"):
        assert tech.post(f"/app/inventory/part-lines/{line.pk}/{act}/", {"warehouse": str(ui["wh"].pk),
                                                                          "quantity": "1"}).status_code == 403, act
    assert StockMovement.objects.filter(movement_type__in=["ISSUE", "RESERVE", "RETURN"]).count() == 0
    # 2. stores reserve and issue from the WO parts page
    page = stores.get(f"/app/work-orders/{wo.pk}/parts/").content.decode()
    assert "Reserve" in page and "Issue" in page and "Request a part" in page
    assert stores.post(f"/app/inventory/part-lines/{line.pk}/reserve/", {"warehouse": str(ui["wh"].pk),
                                                                          "quantity": "3"}).status_code == 302
    bal = StockBalance.objects.get()
    assert (bal.on_hand, bal.reserved) == (10, 3)
    assert stores.post(f"/app/inventory/part-lines/{line.pk}/issue/", {"warehouse": str(ui["wh"].pk),
                                                                        "quantity": "4"}).status_code == 302
    bal.refresh_from_db()
    assert (bal.on_hand, bal.reserved) == (6, 0)
    r = stores.post(f"/app/inventory/part-lines/{line.pk}/issue/", {"warehouse": str(ui["wh"].pk), "quantity": "1"})
    assert "more than the requirement" in " ".join(follow_flash(stores, r))
    # 3. the technician records the consumption in the workspace
    job = tech.get(f"/app/workspace/{wo.pk}/").content.decode()
    assert "Used" in job
    assert tech.post(f"/app/workspace/{wo.pk}/parts/{line.pk}/consume/", {"quantity": "3"}).status_code == 302
    line.refresh_from_db()
    assert line.quantity_consumed == 3 and WorkOrderMaterial.objects.get(part_line=line).quantity == 3
    r = tech.post(f"/app/workspace/{wo.pk}/parts/{line.pk}/consume/", {"quantity": "9"})
    assert "Cannot consume more" in " ".join(follow_flash(tech, r))
    assert tech.post(f"/app/workspace/{wo.pk}/parts/{line.pk}/consume/", {"quantity": "0"}).status_code == 302
    line.refresh_from_db()
    assert line.quantity_consumed == 3
    # 4. return the rest and reconcile
    assert stores.post(f"/app/inventory/part-lines/{line.pk}/return/", {"quantity": "1", "reason": "unused"}).status_code == 302
    assert StockBalance.objects.get().on_hand == 7
    assert stores.post(f"/app/inventory/part-lines/{line.pk}/reconcile/").status_code == 302
    line.refresh_from_db()
    assert line.status == "RECONCILED"
    # 5. the M06 detail shows the stock-backed line, the movement ledger lists everything with the work order
    detail = tech.get(f"/app/work-orders/{wo.pk}/?tab=work").content.decode()
    assert "Mechanical seal" in detail and "stock" in detail
    ledger = stores.get(f"/app/inventory/movements/?work_order={wo.pk}").content.decode()
    for kind in ("Reserve", "Issue", "Return"):
        assert kind in ledger
    actions = set(AuditLog.objects.filter(organization=ui["org"]).values_list("action", flat=True))
    assert {"stock.reserved", "stock.issued", "stock.returned", "part_line.consumed", "part_line.reconciled"} <= actions


def test_workspace_part_endpoints_are_scoped_to_my_jobs(ui):
    inv.receive(ui["wh"], ui["part"], 5, actor=ui["stores"].user)
    mine = wo_in_progress(ui, tech="tech")
    theirs = wo_in_progress(ui, tech="tech2")
    line = inv.request_part(theirs, ui["part"], 2, actor=ui["tech2"].user, membership=ui["tech2"])
    t = ui["cl"]["tech"]
    assert t.post(f"/app/workspace/{theirs.pk}/parts/request/", {"part": str(ui["part"].pk), "quantity": "1"}).status_code == 404
    assert t.post(f"/app/workspace/{theirs.pk}/parts/{line.pk}/consume/", {"quantity": "1"}).status_code == 404
    assert t.post(f"/app/work-orders/{theirs.pk}/parts/request/", {"part": str(ui["part"].pk), "quantity": "1"}).status_code == 404
    assert t.get(f"/app/work-orders/{theirs.pk}/parts/").status_code == 404
    # a line of the other job cannot be consumed through MY job's URL either
    assert t.post(f"/app/workspace/{mine.pk}/parts/{line.pk}/consume/", {"quantity": "1"}).status_code == 404
    assert WorkOrderPart.objects.count() == 1


def test_safe_next_redirect_and_unknown_actions(ui):
    inv.receive(ui["wh"], ui["part"], 5, actor=ui["stores"].user)
    wo = wo_in_progress(ui)
    line = inv.request_part(wo, ui["part"], 2, actor=ui["tech"].user, membership=ui["tech"])
    s = ui["cl"]["stores"]
    r = s.post(f"/app/inventory/part-lines/{line.pk}/issue/", {"warehouse": str(ui["wh"].pk), "quantity": "1",
                                                                "next": "https://evil.example/phish"})
    assert r.status_code == 302 and r["Location"] == f"/app/work-orders/{wo.pk}/parts/"
    r = s.post(f"/app/inventory/part-lines/{line.pk}/issue/", {"warehouse": str(ui["wh"].pk), "quantity": "1",
                                                                "next": "//evil.example"})
    assert r["Location"] == f"/app/work-orders/{wo.pk}/parts/"
    r = s.post(f"/app/inventory/part-lines/{line.pk}/reconcile/", {"next": f"/app/workspace/{wo.pk}/"})
    assert r["Location"] == f"/app/workspace/{wo.pk}/"
    assert s.post(f"/app/inventory/part-lines/{line.pk}/explode/").status_code == 302  # unknown action: message only


def test_csrf_is_enforced_on_inventory_posts(ui):
    c = Client(enforce_csrf_checks=True)
    c.force_login(ui["stores"].user)
    r = c.post("/app/inventory/stock/receive/", {"warehouse": str(ui["wh"].pk), "part": str(ui["part"].pk),
                                                 "quantity": "5"})
    assert r.status_code == 403
    assert not StockMovement.objects.exists()
