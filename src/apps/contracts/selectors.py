"""M10 read side: organization first, then the sites where the caller holds ``contract.view``."""
from __future__ import annotations

import datetime
import uuid

from django.db.models import Q

from apps.rbac import services as rbac
from apps.sites.selectors import scoped_get

from .models import CoverageAgreement, CoverageCheck, CoveredAsset, ServiceProvider


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def agreements_for(membership, org):
    scope = rbac.site_scope(membership, "contract.view")
    qs = CoverageAgreement.objects.for_organization(org).select_related("provider", "site")
    return scope.filter(qs, "site_id")


def get_agreement(membership, org, pk) -> CoverageAgreement:
    return scoped_get(agreements_for(membership, org), pk, "Agreement")


def filter_agreements(qs, params, today=None):
    today = today or datetime.date.today()
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(title__icontains=q) | Q(provider__name__icontains=q)
                       | Q(covered_assets__asset__asset_tag__icontains=q)).distinct()
    kind = (params.get("kind") or "").strip()
    if kind:
        qs = qs.filter(kind=kind)
    for param, field in (("site", "site_id"), ("provider", "provider_id"), ("asset", "covered_assets__asset_id")):
        value = (params.get(param) or "").strip()
        if value:
            parsed = _uuid_or_none(value)
            qs = qs.filter(**{field: parsed}) if parsed else qs.none()
    state = (params.get("state") or "").strip()
    if state == "ACTIVE":
        qs = qs.filter(is_active=True, start_date__lte=today, end_date__gte=today)
    elif state == "UPCOMING":
        qs = qs.filter(is_active=True, start_date__gt=today)
    elif state == "EXPIRED":
        qs = qs.filter(is_active=True, end_date__lt=today)
    elif state == "INACTIVE":
        qs = qs.filter(is_active=False)
    return qs.order_by("end_date", "reference")


def expiring(membership, org, days=60, today=None):
    today = today or datetime.date.today()
    return agreements_for(membership, org).filter(
        is_active=True, end_date__gte=today, end_date__lte=today + datetime.timedelta(days=days)).order_by("end_date")


def providers_for(org):
    """Providers are organization-wide reference data (no site)."""
    return ServiceProvider.objects.for_organization(org)


def get_provider(org, pk) -> ServiceProvider:
    return scoped_get(providers_for(org), pk, "Provider")


def covered_assets_for(agreement):
    return CoveredAsset.objects.for_organization(agreement.organization).filter(agreement=agreement).select_related(
        "asset__site").order_by("asset__asset_tag")


def checks_for(membership, org):
    scope = rbac.site_scope(membership, "contract.view")
    qs = CoverageCheck.objects.for_organization(org).select_related("work_order", "asset", "agreement__provider")
    return scope.filter(qs, "work_order__site_id")
