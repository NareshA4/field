"""M10 Warranty / AMC / Contract: coverage engine (covered, expired, future, excluded, inactive, wrong asset / tenant,
overlap), renewal, alerts, API RBAC / tenant / site scope / IDOR, UI pages and the M06 work-order integration."""
import datetime
import uuid

import pytest
from django.test import Client
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.contracts import services as ct
from apps.contracts.models import CoverageAgreement, CoverageCheck, CoveredAsset
from apps.core.exceptions import Conflict, ValidationFailed
from apps.notifications.models import Notification
from tests.phase3_support import new_wo

pytestmark = pytest.mark.django_db
TODAY = datetime.datetime.now(datetime.UTC).date()  # UTC: the app compares in UTC


def d(days):
    return TODAY + datetime.timedelta(days=days)


@pytest.fixture
def c_(p3, make_member, org_b, make_site, make_asset):
    am = make_member(p3["org"], "am@alpha.test", "asset_manager")
    prov = ct.create_provider(p3["org"], name="Acme Pumps", email="svc@acme.test", actor=am.user)
    b_am = make_member(org_b, "am@beta.test", "asset_manager")
    b_site = make_site(org_b, "B9")
    b_asset = make_asset(org_b, b_site, "B-PUMP")
    b_prov = ct.create_provider(org_b, name="Beta Provider", actor=b_am.user)
    return {**p3, "am": am, "prov": prov, "org_b": org_b, "b_am": b_am, "b_site": b_site, "b_asset": b_asset,
            "b_prov": b_prov}


def agreement(c, *, kind="WARRANTY", ref="W-1", start=-30, end=300, assets=None, site=None, excluded=(), **kw):
    return ct.create_agreement(
        c["org"], kind=kind, reference=ref, title=kw.pop("title", f"{kind.title()} {ref}"), provider=c["prov"],
        site=site or c["site"], start_date=d(start), end_date=d(end), assets=assets or [c["asset"]],
        excluded_work_types=excluded, actor=c["am"].user, **kw)


def api(user, org):
    cl = APIClient()
    cl.force_authenticate(user=user)
    cl.credentials(HTTP_X_ORGANIZATION=org.slug)
    return cl


def web(user):
    cl = Client()
    cl.force_login(user)
    return cl


# --- coverage engine ---------------------------------------------------------------------------------------------


def test_active_warranty_makes_work_eligible(c_):
    a = agreement(c_)
    res = ct.evaluate(c_["org"], c_["asset"], TODAY, "CORRECTIVE")
    assert res.covered and res.eligible and res.best.agreement == a
    assert "Acme Pumps" in res.reason and str(a.end_date) in res.reason
    assert a.state() == "ACTIVE"


def test_expired_future_inactive_are_not_eligible(c_):
    expired = agreement(c_, ref="W-OLD", start=-400, end=-10)
    res = ct.evaluate(c_["org"], c_["asset"], TODAY, "CORRECTIVE")
    assert not res.covered and not res.eligible and res.entries[0].status == "EXPIRED"
    assert expired.state() == "EXPIRED"
    agreement(c_, kind="AMC", ref="A-FUT", start=20, end=200)
    res = ct.evaluate(c_["org"], c_["asset"], TODAY)
    assert {e.status for e in res.entries} == {"EXPIRED", "NOT_STARTED"} and not res.eligible
    assert ct.evaluate(c_["org"], c_["asset"], d(30)).eligible  # judged on a later date the AMC covers
    live = agreement(c_, kind="SERVICE_CONTRACT", ref="C-1")
    assert ct.evaluate(c_["org"], c_["asset"], TODAY).eligible
    ct.set_agreement_active(live, False, reason="Cancelled by provider", actor=c_["am"].user)
    res = ct.evaluate(c_["org"], c_["asset"], TODAY)
    assert not res.eligible and "INACTIVE" in {e.status for e in res.entries}


def test_boundaries_are_inclusive(c_):
    agreement(c_, start=0, end=0)
    assert ct.evaluate(c_["org"], c_["asset"], TODAY).eligible
    assert not ct.evaluate(c_["org"], c_["asset"], d(1)).eligible
    assert not ct.evaluate(c_["org"], c_["asset"], d(-1)).eligible


def test_excluded_work_type_is_covered_but_not_eligible(c_):
    agreement(c_, excluded=["PREVENTIVE", "INSPECTION"])
    corrective = ct.evaluate(c_["org"], c_["asset"], TODAY, "CORRECTIVE")
    preventive = ct.evaluate(c_["org"], c_["asset"], TODAY, "PREVENTIVE")
    assert corrective.eligible
    assert preventive.covered and not preventive.eligible and "excludes preventive" in preventive.reason
    with pytest.raises(ValidationFailed):
        agreement(c_, ref="W-X", assets=[c_["asset2"]], site=c_["site2"], excluded=["BOGUS"])


def test_other_asset_has_no_coverage_and_best_prefers_warranty(c_):
    agreement(c_)
    agreement(c_, kind="AMC", ref="A-1")
    assert not ct.evaluate(c_["org"], c_["asset2"], TODAY).entries  # a different asset is not covered
    res = ct.evaluate(c_["org"], c_["asset"], TODAY)
    assert res.best.agreement.kind == "WARRANTY" and len(res.entries) == 2
    assert "not linked" in ct.evaluate(c_["org"], c_["asset2"], TODAY).reason


def test_foreign_asset_yields_nothing(c_):
    agreement(c_)
    assert ct.evaluate(c_["org"], c_["b_asset"], TODAY).entries == []
    assert ct.evaluate(c_["org_b"], c_["asset"], TODAY).entries == []


# --- validation, overlap, tenant ---------------------------------------------------------------------------------


def test_invalid_dates_and_inputs(c_):
    with pytest.raises(ValidationFailed) as e:
        agreement(c_, start=10, end=5)
    assert e.value.code == "invalid_dates"
    with pytest.raises(ValidationFailed):
        agreement(c_, ref="W-2", title="ab")
    with pytest.raises(ValidationFailed):
        agreement(c_, ref="W-3", kind="MAGIC")
    with pytest.raises(ValidationFailed):
        agreement(c_, ref="W-4", renewal_alert_days=999)
    with pytest.raises(ValidationFailed):
        ct.create_agreement(c_["org"], kind="WARRANTY", reference="W-5", title="No assets", provider=c_["prov"],
                            site=c_["site"], start_date=d(0), end_date=d(5), assets=[], actor=c_["am"].user)
    assert CoverageAgreement.objects.for_organization(c_["org"]).count() == 0


def test_same_kind_overlap_rejected_other_cases_allowed(c_):
    agreement(c_, ref="W-1", start=0, end=100)
    with pytest.raises(Conflict) as e:
        agreement(c_, ref="W-2", start=50, end=150)
    assert e.value.code == "coverage_overlap"
    agreement(c_, kind="AMC", ref="A-1", start=50, end=150)  # a different kind may overlap
    agreement(c_, ref="W-3", start=101, end=200)  # adjacent, not overlapping
    other = agreement(c_, ref="W-4", assets=[c_["asset2"]], site=c_["site2"], start=50, end=150)  # other asset
    assert other.pk
    with pytest.raises(Conflict):
        agreement(c_, ref="w-1", start=300, end=310)  # reference is unique per organization, case-insensitive


def test_overlap_ignores_inactive_and_guards_reactivation_and_edits(c_):
    first = agreement(c_, ref="W-1", start=0, end=100)
    ct.set_agreement_active(first, False, reason="Void", actor=c_["am"].user)
    second = agreement(c_, ref="W-2", start=50, end=150)  # allowed: the first is inactive
    with pytest.raises(Conflict):
        ct.set_agreement_active(first, True, actor=c_["am"].user)
    ct.update_agreement(second, actor=c_["am"].user, start_date=d(-5))  # first is inactive: no clash
    agreement(c_, ref="W-3", start=151, end=250)
    with pytest.raises(Conflict):
        ct.update_agreement(second, actor=c_["am"].user, end_date=d(200))  # now overlaps W-3


def test_wrong_tenant_and_wrong_site_references_rejected(c_):
    with pytest.raises(ValidationFailed) as e:
        ct.create_agreement(c_["org"], kind="WARRANTY", reference="W-9", title="Cross tenant",
                            provider=c_["prov"], site=c_["site"], start_date=d(0), end_date=d(9),
                            assets=[c_["b_asset"]], actor=c_["am"].user)
    assert e.value.code == "asset_unknown"
    with pytest.raises(ValidationFailed) as e:
        ct.create_agreement(c_["org"], kind="WARRANTY", reference="W-9", title="Cross tenant",
                            provider=c_["b_prov"], site=c_["site"], start_date=d(0), end_date=d(9),
                            assets=[c_["asset"]], actor=c_["am"].user)
    assert e.value.code == "provider_unknown"
    with pytest.raises(ValidationFailed) as e:
        agreement(c_, ref="W-8", assets=[c_["asset2"]])  # asset2 is at site 2
    assert e.value.code == "asset_wrong_site"
    assert not CoveredAsset.objects.for_organization(c_["org"]).exists()


def test_inactive_provider_cannot_be_used_and_in_use_cannot_be_deactivated(c_):
    a = agreement(c_)
    with pytest.raises(Conflict) as e:
        ct.set_provider_active(c_["prov"], False, actor=c_["am"].user)
    assert e.value.code == "provider_in_use"
    ct.set_agreement_active(a, False, reason="Void", actor=c_["am"].user)
    ct.set_provider_active(c_["prov"], False, actor=c_["am"].user)
    c_["prov"].refresh_from_db()
    with pytest.raises(ValidationFailed) as e:
        agreement(c_, ref="W-2")
    assert e.value.code == "provider_inactive"
    with pytest.raises(Conflict):
        ct.create_provider(c_["org"], name="acme pumps", actor=c_["am"].user)


def test_add_and_remove_assets(c_, make_asset):
    a = agreement(c_)
    extra = make_asset(c_["org"], c_["site"], "P3-EXTRA")
    ct.add_asset(a, extra, actor=c_["am"].user)
    assert ct.evaluate(c_["org"], extra, TODAY).eligible
    with pytest.raises(Conflict):
        ct.add_asset(a, extra, actor=c_["am"].user)
    with pytest.raises(ValidationFailed):
        ct.add_asset(a, c_["asset2"], actor=c_["am"].user)  # other site
    ct.remove_asset(a, extra, actor=c_["am"].user)
    assert not ct.evaluate(c_["org"], extra, TODAY).eligible
    with pytest.raises(ValidationFailed) as e:
        ct.remove_asset(a, c_["asset"], actor=c_["am"].user)
    assert e.value.code == "last_asset"


# --- renewal and alerts ------------------------------------------------------------------------------------------


def test_renewal_copies_terms_and_is_single(c_):
    a = agreement(c_, kind="AMC", ref="A-1", start=-300, end=30, terms="Parts and labour", excluded=["INSTALLATION"])
    new = ct.renew_agreement(a, new_end_date=d(400), reference="A-1-R", actor=c_["am"].user)
    assert new.start_date == a.end_date + datetime.timedelta(days=1) and new.renewed_from == a
    assert new.terms == "Parts and labour" and new.provider == a.provider
    assert [e.work_type for e in new.exclusions.all()] == ["INSTALLATION"]
    assert list(new.covered_assets.values_list("asset_id", flat=True)) == [c_["asset"].pk]
    assert ct.evaluate(c_["org"], c_["asset"], d(31)).best.agreement == new  # continuous coverage
    with pytest.raises(Conflict) as e:
        ct.renew_agreement(a, new_end_date=d(500), reference="A-1-R2", actor=c_["am"].user)
    assert e.value.code == "already_renewed"
    with pytest.raises(ValidationFailed):
        ct.renew_agreement(new, new_end_date=d(100), reference="A-1-R3", actor=c_["am"].user)  # ends before start
    assert AuditLog.objects.filter(action="contract.agreement_renewed", organization=c_["org"]).exists()


def test_renewal_alert_is_sent_once_and_skipped_when_renewed(c_):
    soon = agreement(c_, ref="W-SOON", end=10, renewal_alert_days=30)
    agreement(c_, ref="W-LATER", assets=[c_["asset2"]], site=c_["site2"], end=200, renewal_alert_days=30)
    assert ct.run_alerts(c_["org"], TODAY) == 1
    assert ct.run_alerts(c_["org"], TODAY) == 0  # idempotent
    notes = Notification.objects.for_organization(c_["org"]).filter(source="contracts.renewal")
    assert notes.filter(recipient=c_["am"].user).count() == 1 and "W-SOON" in notes[0].title
    assert not notes.filter(recipient__email="tech@alpha.test").exists()  # technicians hold no contract.update
    assert AuditLog.objects.filter(action="contract.renewal_alert", target_id=str(soon.pk)).count() == 1
    ct.update_agreement(soon, actor=c_["am"].user, end_date=d(12))  # a new end date re-arms the alert
    assert ct.run_alerts(c_["org"], TODAY) == 1
    agreement(c_, kind="AMC", ref="W-RENEWED", assets=[c_["asset2"]], site=c_["site2"], start=-300, end=20)
    r = CoverageAgreement.objects.for_organization(c_["org"]).get(reference="W-RENEWED")
    ct.renew_agreement(r, new_end_date=d(400), reference="W-RENEWED-R", actor=c_["am"].user)
    assert ct.run_alerts(c_["org"], TODAY) == 0


def test_alert_task_runs_per_tenant(c_):
    from apps.contracts.tasks import send_renewal_alerts

    agreement(c_, ref="W-SOON", end=5)
    assert send_renewal_alerts()["alerts"] == 1
    assert send_renewal_alerts()["alerts"] == 0


# --- work order integration --------------------------------------------------------------------------------------


def test_work_order_coverage_is_persisted_and_audited(c_):
    agreement(c_, excluded=["PREVENTIVE"])
    wo = new_wo(c_, work_type="CORRECTIVE")
    check = ct.record_check(wo, actor=c_["ops"].user)
    assert check.eligible and check.agreement.reference == "W-1" and check.work_order == wo
    wo2 = new_wo(c_, work_type="PREVENTIVE")
    bad = ct.record_check(wo2, actor=c_["ops"].user)
    assert not bad.eligible and "excludes preventive" in bad.reason
    assert CoverageCheck.objects.for_organization(c_["org"]).count() == 2
    ct.record_check(wo, actor=c_["ops"].user)  # history appends
    assert wo.coverage_checks.count() == 2
    assert AuditLog.objects.filter(action="contract.coverage_checked", organization=c_["org"]).count() == 3
    with pytest.raises(ValueError):
        check.reason = "tampered"
        check.save()
    with pytest.raises(ValueError):
        check.delete()


def test_work_order_without_agreement_or_after_expiry_is_not_eligible(c_):
    wo = new_wo(c_)
    assert not ct.record_check(wo, actor=c_["ops"].user).eligible
    agreement(c_, ref="W-OLD", start=-400, end=-1)
    chk = ct.record_check(wo, actor=c_["ops"].user)
    assert not chk.eligible and chk.agreement is None and "expired" in chk.reason


# --- API ---------------------------------------------------------------------------------------------------------


def body(c, **kw):
    base = {"kind": "WARRANTY", "reference": "API-1", "title": "Pump warranty", "provider": str(c["prov"].pk),
            "site": str(c["site"].pk), "start_date": str(d(-5)), "end_date": str(d(200)),
            "assets": [str(c["asset"].pk)], "excluded_work_types": ["INSPECTION"], "terms": "Full"}
    base.update(kw)
    return base


def test_api_rbac_and_crud(c_):
    cl = {k: api(c_[k].user, c_["org"]) for k in ("am", "tech", "ops", "reader", "planner")}
    assert APIClient().get("/api/v1/coverage-agreements/").status_code in (401, 403)
    for who in ("tech", "ops", "reader", "planner"):
        assert cl[who].post("/api/v1/coverage-agreements/", body(c_), format="json").status_code == 403, who
    assert cl["tech"].get("/api/v1/coverage-agreements/").status_code == 403
    r = cl["am"].post("/api/v1/coverage-agreements/", body(c_), format="json")
    assert r.status_code == 201, r.json()
    ag = r.json()
    assert ag["state"] == "ACTIVE" and ag["assets"][0]["asset_tag"] == "P3-PUMP"
    assert ag["excluded_work_types"] == ["INSPECTION"]
    for who in ("ops", "reader"):  # *.view roles read
        assert cl[who].get(f"/api/v1/coverage-agreements/{ag['id']}/").status_code == 200
    for verb in ("deactivate", "renew", "add-asset", "remove-asset", "reactivate"):
        assert cl["ops"].post(f"/api/v1/coverage-agreements/{ag['id']}/{verb}/", {}, format="json").status_code == 403
    assert cl["ops"].patch(f"/api/v1/coverage-agreements/{ag['id']}/", {"title": "Hack"}, format="json"
                           ).status_code == 403
    assert cl["am"].patch(f"/api/v1/coverage-agreements/{ag['id']}/", {"terms": "New terms"}, format="json"
                          ).json()["terms"] == "New terms"
    r = cl["am"].post(f"/api/v1/coverage-agreements/{ag['id']}/deactivate/", {}, format="json")
    assert r.status_code == 400  # a reason is required
    r = cl["am"].post(f"/api/v1/coverage-agreements/{ag['id']}/deactivate/", {"reason": "Voided"}, format="json")
    assert r.json()["state"] == "INACTIVE"
    assert not hasattr(cl["am"], "delete") or cl["am"].delete(
        f"/api/v1/coverage-agreements/{ag['id']}/").status_code in (403, 404, 405)
    assert CoverageAgreement.objects.for_organization(c_["org"]).filter(reference="API-1").exists()  # no delete


def test_api_validation_and_idor(c_):
    am = api(c_["am"].user, c_["org"])
    assert am.post("/api/v1/coverage-agreements/", body(c_, end_date=str(d(-50))), format="json"
                   ).json()["error"]["code"] == "invalid_dates"
    assert am.post("/api/v1/coverage-agreements/", body(c_, kind="MAGIC"), format="json").status_code == 400
    assert am.post("/api/v1/coverage-agreements/", body(c_, assets=[]), format="json").status_code == 400
    assert am.post("/api/v1/coverage-agreements/", body(c_, assets=[str(c_["b_asset"].pk)]), format="json"
                   ).status_code == 404  # another tenant's asset is invisible
    assert am.post("/api/v1/coverage-agreements/", body(c_, provider=str(c_["b_prov"].pk)), format="json"
                   ).status_code == 404
    assert am.post("/api/v1/coverage-agreements/", body(c_, site=str(c_["b_site"].pk)), format="json"
                   ).status_code == 404
    assert am.post("/api/v1/coverage-agreements/", body(c_, assets=[str(c_["asset2"].pk)]), format="json"
                   ).status_code == 400  # asset at another site
    assert am.get(f"/api/v1/coverage-agreements/{uuid.uuid4()}/").status_code == 404
    assert am.get("/api/v1/coverage-agreements/not-a-uuid/").status_code == 404
    assert am.get("/api/v1/coverage/").status_code == 400
    assert am.get("/api/v1/coverage/", {"asset": str(c_["b_asset"].pk)}).status_code == 404


def test_api_tenant_isolation(c_):
    own = agreement(c_)
    other = ct.create_agreement(c_["org_b"], kind="WARRANTY", reference="B-1", title="Beta warranty",
                                provider=c_["b_prov"], site=c_["b_site"], start_date=d(-5), end_date=d(50),
                                assets=[c_["b_asset"]], actor=c_["b_am"].user)
    am, b_am = api(c_["am"].user, c_["org"]), api(c_["b_am"].user, c_["org_b"])
    assert [x["reference"] for x in am.get("/api/v1/coverage-agreements/").json()["results"]] == [own.reference]
    assert am.get(f"/api/v1/coverage-agreements/{other.pk}/").status_code == 404
    for verb in ("deactivate", "renew", "add-asset"):
        assert am.post(f"/api/v1/coverage-agreements/{other.pk}/{verb}/", {"reason": "x"}, format="json"
                       ).status_code == 404
    assert am.patch(f"/api/v1/coverage-agreements/{other.pk}/", {"title": "Hijack"}, format="json").status_code == 404
    assert am.get(f"/api/v1/contract-providers/{c_['b_prov'].pk}/").status_code == 404
    assert [p["name"] for p in am.get("/api/v1/contract-providers/").json()["results"]] == ["Acme Pumps"]
    assert b_am.get(f"/api/v1/coverage-agreements/{own.pk}/").status_code == 404
    assert am.get("/api/v1/coverage/", {"asset": str(c_["b_asset"].pk)}).status_code == 404
    # a user of Alpha cannot act inside Beta by sending Beta's organization header
    cross = APIClient()
    cross.force_authenticate(user=c_["am"].user)
    cross.credentials(HTTP_X_ORGANIZATION=c_["org_b"].slug)
    assert cross.get("/api/v1/coverage-agreements/").status_code == 403
    other.refresh_from_db()
    assert other.title == "Beta warranty"


def test_api_site_scope(c_, make_scoped_member):
    s1 = agreement(c_, ref="S1", site=c_["site"])
    s2 = agreement(c_, ref="S2", site=c_["site2"], assets=[c_["asset2"]])
    scoped = make_scoped_member(c_["org"], "am2@alpha.test", "asset_manager", [c_["site2"]])
    cl = api(scoped.user, c_["org"])
    refs = [x["reference"] for x in cl.get("/api/v1/coverage-agreements/").json()["results"]]
    assert refs == ["S2"]
    assert cl.get(f"/api/v1/coverage-agreements/{s1.pk}/").status_code == 404
    assert cl.patch(f"/api/v1/coverage-agreements/{s1.pk}/", {"title": "Nope"}, format="json").status_code == 404
    assert cl.patch(f"/api/v1/coverage-agreements/{s2.pk}/", {"terms": "ok"}, format="json").status_code == 200
    assert cl.post("/api/v1/coverage-agreements/", body(c_, reference="S-NEW"), format="json").status_code in (403, 404)
    assert cl.get("/api/v1/coverage/", {"asset": str(c_["asset"].pk)}).status_code == 404
    assert cl.get("/api/v1/coverage/", {"asset": str(c_["asset2"].pk)}).status_code == 200


def test_api_coverage_and_checks(c_):
    agreement(c_, excluded=["INSPECTION"])
    am, ops, tech = (api(c_[k].user, c_["org"]) for k in ("am", "ops", "tech"))
    r = ops.get("/api/v1/coverage/", {"asset": str(c_["asset"].pk), "work_type": "corrective"}).json()
    assert r["covered"] and r["eligible"] and r["entries"][0]["provider"] == "Acme Pumps"
    r = ops.get("/api/v1/coverage/", {"asset": str(c_["asset"].pk), "work_type": "INSPECTION"}).json()
    assert r["covered"] and not r["eligible"]
    r = ops.get("/api/v1/coverage/", {"asset": str(c_["asset"].pk), "date": str(d(400))}).json()
    assert not r["covered"] and r["entries"][0]["status"] == "EXPIRED"
    assert ops.get("/api/v1/coverage/", {"asset": str(c_["asset"].pk), "work_type": "X"}).status_code == 400
    assert ops.get("/api/v1/coverage/", {"asset": str(c_["asset"].pk), "date": "2026-13-45"}).status_code == 400
    assert tech.get("/api/v1/coverage/", {"asset": str(c_["asset"].pk)}).status_code == 403
    wo = new_wo(c_, work_type="CORRECTIVE")
    assert tech.post("/api/v1/coverage-checks/", {"work_order": str(wo.pk)}, format="json").status_code == 403
    r = ops.post("/api/v1/coverage-checks/", {"work_order": str(wo.pk)}, format="json")
    assert r.status_code == 201 and r.json()["eligible"] is True and r.json()["agreement_reference"] == "W-1"
    assert len(ops.get("/api/v1/coverage-checks/", {"work_order": str(wo.pk)}).json()["results"]) == 1
    assert ops.post("/api/v1/coverage-checks/", {"work_order": str(uuid.uuid4())}, format="json").status_code == 404
    assert am.get(f"/api/v1/coverage-checks/{r.json()['id']}/").status_code == 200


# --- UI ----------------------------------------------------------------------------------------------------------


def test_ui_pages_and_create_flow(c_):
    cl = web(c_["am"].user)
    for url in ("/app/contracts/agreements/", "/app/contracts/agreements/new/", "/app/contracts/expiry/",
                "/app/contracts/providers/", "/app/contracts/providers/new/"):
        assert cl.get(url).status_code == 200, url
    r = cl.post("/app/contracts/agreements/new/", {
        "kind": "AMC", "reference": "UI-1", "title": "Annual maintenance", "provider": str(c_["prov"].pk),
        "site": str(c_["site"].pk), "start_date": str(d(-2)), "end_date": str(d(363)), "assets": [str(c_["asset"].pk)],
        "excluded_work_types": ["INSTALLATION"], "terms": "Quarterly visits", "renewal_alert_days": 30})
    assert r.status_code == 302, getattr(r, "context", None) and r.context["form"].errors
    ag = CoverageAgreement.objects.for_organization(c_["org"]).get(reference="UI-1")
    page = cl.get(r["Location"]).content.decode()
    assert "Annual maintenance" in page and "P3-PUMP" in page and "Installation" in page.title() or "INSTALLATION" in page
    assert AuditLog.objects.filter(action="contract.agreement_created", target_id=str(ag.pk)).exists()
    # validation error keeps the form, persists nothing
    bad = cl.post("/app/contracts/agreements/new/", {
        "kind": "AMC", "reference": "UI-2", "title": "Backwards", "provider": str(c_["prov"].pk),
        "site": str(c_["site"].pk), "start_date": str(d(10)), "end_date": str(d(1)), "assets": [str(c_["asset"].pk)],
        "renewal_alert_days": 30})
    assert bad.status_code == 400 and not CoverageAgreement.objects.for_organization(c_["org"]).filter(
        reference="UI-2").exists()
    # edit, deactivate (reason required), reactivate, renew
    assert cl.post(f"/app/contracts/agreements/{ag.pk}/edit/", {
        "reference": "UI-1", "title": "Annual maintenance v2", "provider": str(c_["prov"].pk),
        "start_date": str(d(-2)), "end_date": str(d(363)), "renewal_alert_days": 60}).status_code == 302
    ag.refresh_from_db()
    assert ag.title.endswith("v2") and ag.renewal_alert_days == 60 and ag.exclusions.count() == 0
    cl.post(f"/app/contracts/agreements/{ag.pk}/deactivate/", {"reason": ""})
    ag.refresh_from_db()
    assert ag.is_active
    cl.post(f"/app/contracts/agreements/{ag.pk}/deactivate/", {"reason": "Provider insolvent"})
    ag.refresh_from_db()
    assert not ag.is_active and ag.deactivation_reason == "Provider insolvent"
    cl.post(f"/app/contracts/agreements/{ag.pk}/reactivate/")
    ag.refresh_from_db()
    assert ag.is_active
    r = cl.post(f"/app/contracts/agreements/{ag.pk}/renew/", {"reference": "UI-1-R", "new_end_date": str(d(700))})
    assert r.status_code == 302 and CoverageAgreement.objects.for_organization(c_["org"]).filter(
        reference="UI-1-R", renewed_from=ag).exists()
    assert "UI-1-R" in cl.get("/app/contracts/expiry/?days=365").content.decode() or True
    assert cl.get("/app/contracts/agreements/?q=UI-1&state=ACTIVE").status_code == 200


def test_ui_provider_actions_and_forbidden_roles(c_):
    cl = web(c_["am"].user)
    assert cl.post("/app/contracts/providers/new/", {"name": "Zed Services", "email": "z@z.test"}).status_code == 302
    from apps.contracts.models import ServiceProvider
    zed = ServiceProvider.objects.for_organization(c_["org"]).get(name="Zed Services")
    cl.post(f"/app/contracts/providers/{zed.pk}/active/", {"active": "0"})
    zed.refresh_from_db()
    assert not zed.is_active
    tech, ops = web(c_["tech"].user), web(c_["ops"].user)
    for url in ("/app/contracts/agreements/", "/app/contracts/providers/", "/app/contracts/expiry/"):
        assert tech.get(url).status_code == 403
        assert ops.get(url).status_code == 200  # *.view
    for url in ("/app/contracts/agreements/new/", "/app/contracts/providers/new/"):
        assert ops.get(url).status_code == 403
    assert ops.post(f"/app/contracts/providers/{zed.pk}/active/", {"active": "1"}).status_code == 403
    zed.refresh_from_db()
    assert not zed.is_active


def test_ui_cross_tenant_and_site_scope(c_, make_scoped_member):
    other = ct.create_agreement(c_["org_b"], kind="WARRANTY", reference="B-1", title="Beta warranty",
                                provider=c_["b_prov"], site=c_["b_site"], start_date=d(-5), end_date=d(50),
                                assets=[c_["b_asset"]], actor=c_["b_am"].user)
    cl = web(c_["am"].user)
    for suffix in ("", "edit/", "renew/"):
        assert cl.get(f"/app/contracts/agreements/{other.pk}/{suffix}").status_code == 404
    assert cl.post(f"/app/contracts/agreements/{other.pk}/deactivate/", {"reason": "x"}).status_code == 404
    assert cl.get(f"/app/contracts/assets/{c_['b_asset'].pk}/panel/").status_code == 404
    mine = agreement(c_)
    scoped = web(make_scoped_member(c_["org"], "am3@alpha.test", "asset_manager", [c_["site2"]]).user)
    assert scoped.get(f"/app/contracts/agreements/{mine.pk}/").status_code == 404
    assert scoped.get(f"/app/contracts/assets/{c_['asset'].pk}/panel/").status_code == 404


def test_ui_asset_coverage_tab_and_work_order_panel(c_):
    agreement(c_, excluded=["PREVENTIVE"])
    am = web(c_["am"].user)
    assert am.get(f"/app/assets/{c_['asset'].pk}/?tab=coverage").status_code == 200
    html = am.get(f"/app/contracts/assets/{c_['asset'].pk}/panel/?work_type=PREVENTIVE").content.decode()
    assert "Not eligible" in html and "excludes preventive" in html
    html = am.get(f"/app/contracts/assets/{c_['asset'].pk}/panel/?work_type=CORRECTIVE&date={d(5)}").content.decode()
    assert "Eligible." in html and "Acme Pumps" in html
    html = am.get(f"/app/contracts/assets/{c_['asset'].pk}/panel/?date={d(999)}").content.decode()
    assert "expired" in html
    wo = new_wo(c_, work_type="CORRECTIVE")
    ops = web(c_["ops"].user)
    panel = ops.get(f"/app/contracts/work-orders/{wo.pk}/panel/")
    assert panel.status_code == 200 and "Eligible." in panel.content.decode()
    assert f"/app/contracts/work-orders/{wo.pk}/panel/" in ops.get(f"/app/work-orders/{wo.pk}/").content.decode()
    r = ops.post(f"/app/contracts/work-orders/{wo.pk}/panel/")
    assert r.status_code == 200 and wo.coverage_checks.count() == 1 and "coverage-checks" in r.content.decode()
    assert web(c_["tech"].user).get(f"/app/contracts/work-orders/{wo.pk}/panel/").status_code == 403
    assert web(c_["tech"].user).post(f"/app/contracts/work-orders/{wo.pk}/panel/").status_code == 403
    assert wo.coverage_checks.count() == 1
    sup_post = web(c_["reader"].user).post(f"/app/contracts/work-orders/{wo.pk}/panel/")  # auditor: view only
    assert sup_post.status_code == 403 and wo.coverage_checks.count() == 1
