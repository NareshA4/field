"""M10 -> M11: an agreement's internal SLA profile is the SLA that governs the covered assets while it is in force."""
import datetime

import pytest
from rest_framework.test import APIClient

from apps.contracts import services as ct
from apps.core.exceptions import ValidationFailed
from apps.incidents import services as incidents
from apps.sla import services as sla
from apps.sla.models import SLATracking
from apps.workorders import services as wos

pytestmark = pytest.mark.django_db
TODAY = datetime.datetime.now(datetime.UTC).date()


def d(days):
    return TODAY + datetime.timedelta(days=days)


@pytest.fixture
def cov(sla_, make_member):
    org = sla_["org"]
    am = make_member(org, "am@alpha.test", "asset_manager")
    prov = ct.create_provider(org, name="Acme Service", actor=am.user)
    premium = sla.create_profile(org, name="Premium AMC SLA", applies_to="REQUEST", coverage_only=True,
                                 actor=sla_["svc"].user)
    sla.set_target(premium, priority="HIGH", response_minutes=10, resolution_minutes=60, actor=sla_["svc"].user)
    return {**sla_, "am": am, "prov": prov, "premium": premium}


def agreement(c, *, start=-10, end=100, profile="premium", asset="asset", excluded=(), ref="AMC-1"):
    return ct.create_agreement(
        c["org"], kind="AMC", reference=ref, title="AMC", provider=c["prov"], site=c["site"], start_date=d(start),
        end_date=d(end), assets=[c[asset]], sla_profile=c[profile] if profile else None, excluded_work_types=excluded,
        actor=c["am"].user)


def report(c, asset="asset"):
    sr = incidents.create_request(c["org"], asset=c[asset], reporter=c["ops"], title="Pump down", severity="HIGH",
                                  actor=c["ops"].user)
    return SLATracking.objects.get(request=sr)


def test_covered_asset_uses_the_agreement_sla(cov):
    assert report(cov).profile == cov["profile"]  # no agreement yet: the organization profile
    agreement(cov)
    t = report(cov)
    assert t.profile == cov["premium"] and (t.response_minutes, t.resolution_minutes) == (10, 60)


def test_other_assets_keep_the_organization_sla(cov):
    agreement(cov)
    assert report(cov, "asset2").profile == cov["profile"]


def test_expired_or_future_or_inactive_agreement_is_ignored(cov):
    ag = agreement(cov, start=-100, end=-1)
    assert report(cov).profile == cov["profile"]
    ct.set_agreement_active(ag, False, reason="ended", actor=cov["am"].user)
    agreement(cov, start=5, end=50, ref="AMC-2")
    assert report(cov).profile == cov["profile"]


def test_missing_priority_target_falls_back(cov):
    sla.set_target(cov["profile"], priority="MEDIUM", response_minutes=60, resolution_minutes=240,
                   actor=cov["svc"].user)
    agreement(cov)  # premium only has a HIGH target
    sr = incidents.create_request(cov["org"], asset=cov["asset"], reporter=cov["ops"], title="Noise",
                                  severity="MEDIUM", actor=cov["ops"].user)
    assert SLATracking.objects.get(request=sr).profile == cov["profile"]


def test_work_order_profile_respects_excluded_work_types(cov):
    wo_profile = sla.create_profile(cov["org"], name="AMC work SLA", applies_to="WORK_ORDER",
                                    coverage_only=True, actor=cov["svc"].user)
    sla.set_target(wo_profile, priority="HIGH", response_minutes=15, resolution_minutes=90, actor=cov["svc"].user)
    cov["wo_profile"] = wo_profile
    agreement(cov, profile="wo_profile", excluded=("PREVENTIVE",))
    corrective = wos.create_work_order(cov["org"], asset=cov["asset"], actor=cov["planner"].user, title="Fix",
                                       work_type="CORRECTIVE", priority="HIGH")
    preventive = wos.create_work_order(cov["org"], asset=cov["asset"], actor=cov["planner"].user, title="Service",
                                       work_type="PREVENTIVE", priority="HIGH")
    assert SLATracking.objects.get(work_order=corrective).profile == wo_profile
    assert not SLATracking.objects.filter(work_order=preventive).exists()


def test_profile_validation_and_tenant_scope(cov, org_b, make_member):
    ct_org_b = make_member(org_b, "svc@beta.test", "service_manager")
    foreign = sla.create_profile(org_b, name="Beta SLA", applies_to="REQUEST", actor=ct_org_b.user)
    cov["foreign"] = foreign
    with pytest.raises(ValidationFailed):
        agreement(cov, profile="foreign")
    sla.set_profile_active(cov["premium"], False, actor=cov["svc"].user)
    with pytest.raises(ValidationFailed):
        agreement(cov)


def test_api_sets_and_clears_the_profile_and_refuses_foreign_ids(cov, org_b, make_member):
    ag = agreement(cov)
    api = APIClient()
    api.force_authenticate(user=cov["am"].user)
    api.credentials(HTTP_X_ORGANIZATION=cov["org"].slug)
    r = api.patch(f"/api/v1/coverage-agreements/{ag.pk}/", {"sla_profile": None}, format="json")
    assert r.status_code == 200 and r.json()["sla_profile"] is None
    r = api.patch(f"/api/v1/coverage-agreements/{ag.pk}/", {"sla_profile": str(cov["premium"].pk)}, format="json")
    assert r.status_code == 200 and r.json()["sla_profile"] == str(cov["premium"].pk)
    other = sla.create_profile(org_b, name="Beta SLA", applies_to="REQUEST",
                               actor=make_member(org_b, "svc@beta.test", "service_manager").user)
    r = api.patch(f"/api/v1/coverage-agreements/{ag.pk}/", {"sla_profile": str(other.pk)}, format="json")
    assert r.status_code == 404


def test_coverage_only_profiles_never_resolve_by_scope_and_may_coexist(cov):
    # a second ORGANIZATION-wide REQUEST profile is allowed because the premium one is coverage-only
    assert cov["premium"].coverage_only
    other = sla.create_profile(cov["org"], name="Second premium", applies_to="REQUEST", coverage_only=True,
                               actor=cov["svc"].user)
    assert other.pk != cov["premium"].pk
    assert report(cov).profile == cov["profile"]  # no agreement: scope resolution ignores coverage-only profiles
