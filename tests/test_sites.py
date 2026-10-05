"""M01 Site & Location Master: services, API, RBAC, tenant isolation, constraints."""
import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.audit.models import AuditLog
from apps.core.exceptions import Conflict, InvalidTransition, ValidationFailed
from apps.sites import services
from apps.sites.models import Site, Zone

pytestmark = pytest.mark.django_db


def audit_actions(org):
    return list(AuditLog.objects.filter(organization=org).values_list("action", flat=True))


# --- services / constraints ----------------------------------------------------------------------------


def test_create_site_normalises_code_and_audits(org_a, owner_a):
    site = services.create_site(org_a, actor=owner_a, code=" plant-1 ", name="Main Plant", country="in")
    assert site.code == "PLANT-1" and site.country == "IN" and site.timezone == org_a.timezone
    assert "site.created" in audit_actions(org_a)


def test_duplicate_site_code_is_case_insensitive_per_org_but_allowed_across_orgs(org_a, org_b, make_site):
    make_site(org_a, "HQ")
    with pytest.raises(Conflict) as exc:
        services.create_site(org_a, actor=None, code="hq", name="Other")
    assert exc.value.code == "duplicate_site_code"
    assert make_site(org_b, "HQ").organization_id == org_b.pk  # same code, different tenant


def test_site_code_unique_constraint_in_database(org_a, make_site):
    make_site(org_a, "DB1")
    with pytest.raises(IntegrityError), transaction.atomic():
        Site(organization=org_a, code="db1", name="x").save()


@pytest.mark.parametrize("code", ["", "has space", "x" * 31, "-lead"])
def test_invalid_site_code_rejected(org_a, code):
    with pytest.raises(ValidationFailed):
        services.create_site(org_a, actor=None, code=code, name="X")


def test_invalid_timezone_rejected(org_a):
    with pytest.raises(ValidationFailed):
        services.create_site(org_a, actor=None, code="TZ", name="X", timezone="Mars/Olympus")


def test_deactivation_needs_reason_and_is_a_state_transition(org_a, site_a1, owner_a):
    with pytest.raises(ValidationFailed):
        services.deactivate_site(site_a1, reason=" ", actor=owner_a)
    services.deactivate_site(site_a1, reason="Closed", actor=owner_a)
    site_a1.refresh_from_db()
    assert site_a1.status == "INACTIVE" and site_a1.status_reason == "Closed"
    with pytest.raises(InvalidTransition):
        services.deactivate_site(site_a1, reason="again", actor=owner_a)
    services.reactivate_site(site_a1, actor=owner_a)
    site_a1.refresh_from_db()
    assert site_a1.status == "ACTIVE" and site_a1.status_reason == ""
    assert {"site.deactivated", "site.reactivated"} <= set(audit_actions(org_a))



def test_inactive_site_rejects_new_locations_and_calendars(org_a, site_a1, owner_a):
    services.deactivate_site(site_a1, reason="Closed", actor=owner_a)
    with pytest.raises(Conflict) as exc:
        services.create_zone(site_a1, actor=owner_a, name="Hall")
    assert exc.value.code == "site_inactive"
    with pytest.raises(Conflict):
        services.create_calendar(site_a1, actor=owner_a, name="Std", is_24x7=True)


def test_sites_are_never_hard_deleted_when_referenced(org_a, site_a1, make_zone):
    make_zone(site_a1, "Hall")
    with pytest.raises(ProtectedError):
        site_a1.delete()


# --- zones -----------------------------------------------------------------------------------------------


def test_zone_hierarchy_and_tree(org_a, site_a1, make_zone):
    from apps.sites import selectors

    b = make_zone(site_a1, "Building 1", zone_type="BUILDING")
    f = make_zone(site_a1, "Floor 1", parent=b)
    z = make_zone(site_a1, "Zone A", parent=f, zone_type="SERVICE_AREA")
    tree = selectors.zone_tree(site_a1)
    assert [n["zone"].pk for n in tree] == [b.pk]
    assert tree[0]["children"][0]["children"][0]["zone"].pk == z.pk
    assert tree[0]["children"][0]["children"][0]["depth"] == 2


def test_zone_cycle_is_rejected(site_a1, make_zone):
    a = make_zone(site_a1, "A")
    b = make_zone(site_a1, "B", parent=a)
    c = make_zone(site_a1, "C", parent=b)
    with pytest.raises(ValidationFailed) as exc:
        services.update_zone(a, actor=None, parent=c)
    assert exc.value.code == "zone_cycle"
    with pytest.raises(ValidationFailed):
        services.update_zone(a, actor=None, parent=a)


def test_zone_parent_must_be_same_site_and_org(org_a, org_b, site_a1, site_a2, site_b1, make_zone):
    other_site_zone = make_zone(site_a2, "Other")
    foreign = make_zone(site_b1, "Foreign")
    for parent in (other_site_zone, foreign):
        with pytest.raises(ValidationFailed) as exc:
            services.create_zone(site_a1, actor=None, name="X", parent=parent)
        assert exc.value.code == "invalid_parent"


def test_building_must_be_top_level_and_depth_is_limited(site_a1, make_zone):
    root = make_zone(site_a1, "Root")
    with pytest.raises(ValidationFailed):
        services.create_zone(site_a1, actor=None, name="B", zone_type="BUILDING", parent=root)
    node = root
    for i in range(services.MAX_ZONE_DEPTH - 1):
        node = make_zone(site_a1, f"L{i}", parent=node)
    with pytest.raises(ValidationFailed) as exc:
        services.create_zone(site_a1, actor=None, name="too deep", parent=node)
    assert exc.value.code == "zone_too_deep"


def test_zone_duplicate_names_and_codes(site_a1, make_zone):
    make_zone(site_a1, "Hall", code="H1")
    with pytest.raises(Conflict):
        services.create_zone(site_a1, actor=None, name="hall")
    with pytest.raises(Conflict):
        services.create_zone(site_a1, actor=None, name="Other", code="h1")
    with pytest.raises(IntegrityError), transaction.atomic():
        Zone(organization=site_a1.organization, site=site_a1, name="HALL").save()



def test_calendar_rules(org_a, site_a1, owner_a):
    from datetime import time

    c1 = services.create_calendar(site_a1, actor=owner_a, name="Weekdays", working_days=[1, 2, 3, 4, 5],
                                  start_time=time(8), end_time=time(17))
    assert c1.is_default  # first calendar becomes the default
    c2 = services.create_calendar(site_a1, actor=owner_a, name="Always", is_24x7=True, is_default=True)
    c1.refresh_from_db()
    assert c2.is_default and not c1.is_default and c2.working_days == [1, 2, 3, 4, 5, 6, 7]
    with pytest.raises(ValidationFailed):
        services.create_calendar(site_a1, actor=owner_a, name="Bad", working_days=[1], start_time=time(9),
                                 end_time=time(9))
    with pytest.raises(ValidationFailed):
        services.create_calendar(site_a1, actor=owner_a, name="Bad2", working_days=[8], start_time=time(9),
                                 end_time=time(10))
    with pytest.raises(Conflict):
        services.create_calendar(site_a1, actor=owner_a, name="weekdays", is_24x7=True)
    with pytest.raises(Conflict):
        services.delete_calendar(c2, actor=owner_a)  # default with siblings


def test_calendar_holidays(org_a, site_a1, owner_a):
    import datetime

    cal = services.create_calendar(site_a1, actor=owner_a, name="Std", is_24x7=True)
    h = services.add_holiday(cal, date=datetime.date(2026, 12, 25), name="Christmas", actor=owner_a)
    with pytest.raises(Conflict):
        services.add_holiday(cal, date=datetime.date(2026, 12, 25), name="Dup", actor=owner_a)
    services.remove_holiday(h, actor=owner_a)
    assert {"calendar.holiday_added", "calendar.holiday_removed"} <= set(audit_actions(org_a))
    services.delete_calendar(cal, actor=owner_a)
    assert "calendar.deleted" in audit_actions(org_a)


def test_contact_escalation_hierarchy(org_a, site_a1, owner_a):
    c1 = services.add_contact(site_a1, actor=owner_a, name="Site Manager", phone="+911")
    c2 = services.add_contact(site_a1, actor=owner_a, name="Regional Head", email="r@x.test")
    assert (c1.escalation_order, c2.escalation_order) == (1, 2)
    with pytest.raises(Conflict):
        services.add_contact(site_a1, actor=owner_a, name="Dup", phone="1", escalation_order=2)
    with pytest.raises(ValidationFailed):
        services.add_contact(site_a1, actor=owner_a, name="No method")
    services.update_contact(c2, actor=owner_a, escalation_order=3)
    services.remove_contact(c1, actor=owner_a)
    assert {"site.contact_added", "site.contact_updated", "site.contact_removed"} <= set(audit_actions(org_a))


# --- API --------------------------------------------------------------------------------------------------


def test_api_site_crud_persists_and_audits(as_user, owner_a, org_a):
    c = as_user(owner_a, org_a)
    r = c.post("/api/v1/sites/", {"code": "n1", "name": "North", "city": "Pune", "country": "IN",
                                  "timezone": "Asia/Kolkata"}, format="json")
    assert r.status_code == 201, r.content
    sid = r.json()["id"]
    assert Site.objects.unscoped().get(pk=sid).code == "N1"
    assert c.get(f"/api/v1/sites/{sid}/").json()["name"] == "North"
    r = c.patch(f"/api/v1/sites/{sid}/", {"name": "North Plant"}, format="json")
    assert r.status_code == 200 and Site.objects.unscoped().get(pk=sid).name == "North Plant"
    assert {"site.created", "site.updated"} <= set(audit_actions(org_a))


def test_api_site_validation_and_duplicates(as_user, owner_a, org_a, site_a1):
    c = as_user(owner_a, org_a)
    r = c.post("/api/v1/sites/", {"code": "X1"}, format="json")
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation_failed"
    r = c.post("/api/v1/sites/", {"code": "a1", "name": "dup"}, format="json")
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_site_code"


def test_api_site_list_filter_search_pagination(as_user, owner_a, org_a, make_site):
    for i in range(5):
        make_site(org_a, f"S{i}", city="Delhi" if i % 2 else "Mumbai")
    c = as_user(owner_a, org_a)
    body = c.get("/api/v1/sites/?page_size=2").json()
    assert body["count"] == 5 and len(body["results"]) == 2 and body["next"]
    assert c.get("/api/v1/sites/?q=delhi").json()["count"] == 2
    assert c.get("/api/v1/sites/?q=s3").json()["count"] == 1
    assert c.get("/api/v1/sites/?status=INACTIVE").json()["count"] == 0



def test_api_rbac_matrix_for_sites(as_user, api, org_a, owner_a, tech_a, site_a1):
    # unauthenticated
    assert api.get("/api/v1/sites/").status_code == 401
    tech = as_user(tech_a, org_a)  # technician: site.view only
    assert tech.get("/api/v1/sites/").status_code == 200
    assert tech.get(f"/api/v1/sites/{site_a1.pk}/").status_code == 200
    assert tech.post("/api/v1/sites/", {"code": "T", "name": "T"}, format="json").status_code == 403
    assert tech.patch(f"/api/v1/sites/{site_a1.pk}/", {"name": "x"}, format="json").status_code == 403
    assert tech.post(f"/api/v1/sites/{site_a1.pk}/deactivate/", {"reason": "x"}, format="json").status_code == 403
    assert tech.post("/api/v1/zones/", {"site": str(site_a1.pk), "name": "Z"}, format="json").status_code == 403
    assert tech.post("/api/v1/calendars/", {"site": str(site_a1.pk), "name": "C", "is_24x7": True},
                     format="json").status_code == 403
    assert tech.post("/api/v1/site-contacts/", {"site": str(site_a1.pk), "name": "C", "phone": "1"},
                     format="json").status_code == 403
    Site.objects.get(pk=site_a1.pk)  # untouched
    assert Site.objects.get(pk=site_a1.pk).name == site_a1.name


def test_api_header_for_non_member_org_is_rejected(as_user, owner_a, org_b):
    r = as_user(owner_a, org_b).get("/api/v1/sites/")
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_a_member"


def test_api_zone_flow_and_tree(as_user, owner_a, org_a, site_a1):
    c = as_user(owner_a, org_a)
    b = c.post("/api/v1/zones/", {"site": str(site_a1.pk), "name": "Block 1", "zone_type": "BUILDING"},
               format="json")
    assert b.status_code == 201, b.content
    z = c.post("/api/v1/zones/", {"site": str(site_a1.pk), "name": "Bay", "parent": b.json()["id"]}, format="json")
    assert z.status_code == 201 and z.json()["parent_name"] == "Block 1"
    tree = c.get(f"/api/v1/sites/{site_a1.pk}/tree/").json()
    assert tree[0]["name"] == "Block 1" and tree[0]["children"][0]["name"] == "Bay"
    # cycle via API
    r = c.patch(f"/api/v1/zones/{b.json()['id']}/", {"parent": z.json()["id"]}, format="json")
    assert r.status_code == 400
    # deactivate / reactivate
    zid = z.json()["id"]
    assert c.post(f"/api/v1/zones/{zid}/deactivate/", {"reason": "unused"}, format="json").status_code == 200
    assert c.post(f"/api/v1/zones/{zid}/deactivate/", {"reason": "again"}, format="json").status_code == 409
    assert c.post(f"/api/v1/zones/{zid}/reactivate/").status_code == 200
    assert c.get(f"/api/v1/zones/?site={site_a1.pk}&parent=root").json()["count"] == 1


def test_api_calendar_holiday_contact_flow(as_user, owner_a, org_a, site_a1):
    c = as_user(owner_a, org_a)
    r = c.post("/api/v1/calendars/", {"site": str(site_a1.pk), "name": "Std", "working_days": [1, 2, 3, 4, 5],
                                      "start_time": "08:00", "end_time": "17:00"}, format="json")
    assert r.status_code == 201, r.content
    cid = r.json()["id"]
    assert c.post("/api/v1/calendar-holidays/", {"calendar": cid, "date": "2026-12-25", "name": "Xmas"},
                  format="json").status_code == 201
    assert c.post("/api/v1/calendar-holidays/", {"calendar": cid, "date": "2026-12-25", "name": "Dup"},
                  format="json").status_code == 409
    assert len(c.get(f"/api/v1/calendars/{cid}/").json()["holidays"]) == 1
    r = c.post("/api/v1/site-contacts/", {"site": str(site_a1.pk), "name": "Boss", "phone": "1"}, format="json")
    assert r.status_code == 201 and r.json()["escalation_order"] == 1
    kid = r.json()["id"]
    assert c.patch(f"/api/v1/site-contacts/{kid}/", {"escalation_order": 2}, format="json").status_code == 200
    assert c.delete(f"/api/v1/site-contacts/{kid}/").status_code == 204
    assert c.delete(f"/api/v1/calendars/{cid}/").status_code == 204


# --- tenant isolation / IDOR ---------------------------------------------------------------------------------


def test_cross_tenant_idor_for_every_m01_route(as_user, owner_a, org_a, org_b, site_b1, make_zone, owner_b):
    zone_b = make_zone(site_b1, "Hall")
    cal_b = services.create_calendar(site_b1, actor=owner_b, name="Std", is_24x7=True)
    hol_b = services.add_holiday(cal_b, date="2026-01-01", name="NY", actor=owner_b)
    con_b = services.add_contact(site_b1, actor=owner_b, name="X", phone="1")
    c = as_user(owner_a, org_a)
    for url in (f"/api/v1/sites/{site_b1.pk}/", f"/api/v1/sites/{site_b1.pk}/tree/",
                f"/api/v1/zones/{zone_b.pk}/", f"/api/v1/calendars/{cal_b.pk}/",
                f"/api/v1/site-contacts/{con_b.pk}/"):
        assert c.get(url).status_code == 404, url
    assert c.patch(f"/api/v1/sites/{site_b1.pk}/", {"name": "pwn"}, format="json").status_code == 404
    assert c.post(f"/api/v1/sites/{site_b1.pk}/deactivate/", {"reason": "x"}, format="json").status_code == 404
    assert c.patch(f"/api/v1/zones/{zone_b.pk}/", {"name": "pwn"}, format="json").status_code == 404
    assert c.delete(f"/api/v1/calendars/{cal_b.pk}/").status_code == 404
    assert c.delete(f"/api/v1/calendar-holidays/{hol_b.pk}/").status_code == 404
    assert c.delete(f"/api/v1/site-contacts/{con_b.pk}/").status_code == 404
    # creating below foreign objects
    assert c.post("/api/v1/zones/", {"site": str(site_b1.pk), "name": "Z"}, format="json").status_code == 404
    assert c.post("/api/v1/calendars/", {"site": str(site_b1.pk), "name": "C", "is_24x7": True},
                  format="json").status_code == 404
    assert c.post("/api/v1/site-contacts/", {"site": str(site_b1.pk), "name": "C", "phone": "1"},
                  format="json").status_code == 404
    # nothing changed, and lists never include the foreign records
    site_b1.refresh_from_db()
    assert site_b1.status == "ACTIVE" and site_b1.name == "Site B1"
    for url in ("/api/v1/sites/", "/api/v1/zones/", "/api/v1/calendars/", "/api/v1/site-contacts/",
                "/api/v1/calendar-holidays/"):
        assert c.get(url).json()["count"] == 0, url


def test_zone_with_foreign_parent_via_api_is_not_found(as_user, owner_a, org_a, site_a1, site_b1, make_zone):
    foreign = make_zone(site_b1, "F")
    r = as_user(owner_a, org_a).post(
        "/api/v1/zones/", {"site": str(site_a1.pk), "name": "Z", "parent": str(foreign.pk)}, format="json")
    assert r.status_code == 404
    assert not Zone.objects.unscoped().filter(site=site_a1).exists()


def test_malformed_ids_do_not_crash(as_user, owner_a, org_a):
    c = as_user(owner_a, org_a)
    assert c.get("/api/v1/sites/not-a-uuid/").status_code == 404
    assert c.get("/api/v1/zones/?site=not-a-uuid").json()["count"] == 0


# --- BX-M01-01 regression: escalation order is validated, never a database error ----------------------------------------


def test_escalation_order_boundaries_in_the_service(org_a, site_a1, owner_a):
    from apps.sites.models import MAX_ESCALATION_ORDER, SiteContact

    assert MAX_ESCALATION_ORDER == 32767
    low = services.add_contact(site_a1, actor=owner_a, name="Low", phone="1", escalation_order=1)
    top = services.add_contact(site_a1, actor=owner_a, name="Top", phone="1", escalation_order=MAX_ESCALATION_ORDER)
    assert (low.escalation_order, top.escalation_order) == (1, 32767)
    for bad in (0, -3, MAX_ESCALATION_ORDER + 1, 99999999999):
        with pytest.raises(ValidationFailed) as exc:
            services.add_contact(site_a1, actor=owner_a, name=f"Bad{bad}", phone="1", escalation_order=bad)
        assert exc.value.code == "invalid_escalation_order"
    assert SiteContact.objects.filter(site=site_a1).count() == 2
    with pytest.raises(ValidationFailed):          # appending after the maximum would overflow the column
        services.add_contact(site_a1, actor=owner_a, name="Overflow", phone="1")
    with pytest.raises(ValidationFailed):
        services.update_contact(low, actor=owner_a, escalation_order=MAX_ESCALATION_ORDER + 1)
    low.refresh_from_db()
    assert low.escalation_order == 1


def test_escalation_order_api_returns_4xx_never_500(as_user, owner_a, org_a, site_a1):
    c = as_user(owner_a, org_a)
    body = {"site": str(site_a1.pk), "name": "Boss", "phone": "1"}
    ok = c.post("/api/v1/site-contacts/", {**body, "escalation_order": 32767}, format="json")
    assert ok.status_code == 201 and ok.json()["escalation_order"] == 32767
    for bad in (32768, 99999999999, 0, -1, "abc", 1.5):
        r = c.post("/api/v1/site-contacts/", {**body, "name": f"n{bad}", "escalation_order": bad}, format="json")
        assert r.status_code == 400, (bad, r.status_code, r.content)
        assert "escalation_order" in r.json()["error"]["details"] or r.json()["error"]["code"]
    kid = ok.json()["id"]
    r = c.patch(f"/api/v1/site-contacts/{kid}/", {"escalation_order": 99999999999}, format="json")
    assert r.status_code == 400
    assert c.get(f"/api/v1/site-contacts/{kid}/").json()["escalation_order"] == 32767


def test_escalation_order_ui_shows_a_validation_message(client, owner_a, site_a1):
    from apps.sites.models import SiteContact

    client.force_login(owner_a)
    url = f"/app/sites/{site_a1.pk}/contacts/new/"
    r = client.post(url, {"name": "Huge", "phone": "1", "escalation_order": "99999999999"})
    assert r.status_code == 400 and b"less than or equal to 32767" in r.content
    r = client.post(url, {"name": "Neg", "phone": "1", "escalation_order": "-3"})
    assert r.status_code == 400 and b"greater than or equal to 1" in r.content
    r = client.post(url, {"name": "Dec", "phone": "1", "escalation_order": "1.5"})
    assert r.status_code == 400
    assert not SiteContact.objects.filter(site=site_a1).exists()
    assert client.post(url, {"name": "Max", "phone": "1", "escalation_order": "32767"}).status_code == 302
    assert SiteContact.objects.get(site=site_a1).escalation_order == 32767
    contact = SiteContact.objects.get(site=site_a1)
    r = client.post(f"/app/contacts/{contact.pk}/edit/", {"name": "Max", "phone": "1", "escalation_order": "40000"})
    assert r.status_code == 400 and b"less than or equal to 32767" in r.content
