"""M05 read side: organization + site-scope restricted querysets (see ``sites.selectors``)."""
from __future__ import annotations

import uuid

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q

from apps.files.models import Attachment
from apps.rbac import services as rbac
from apps.sites.selectors import scoped_get

from .models import Downtime, ServiceRequest, ServiceRequestHistory

ORDERINGS = {"-created_at": "-created_at", "created_at": "created_at", "number": "number", "-number": "-number",
             "severity": "severity", "status": "status"}


def requests_for(membership, org, code: str = "incident.view"):
    qs = ServiceRequest.objects.for_organization(org).select_related("asset", "site", "reported_by__user")
    if code == "incident.view_assigned":
        cond = Q(pk__isnull=True)  # matches nothing
        full = rbac.site_scope(membership, "incident.view")
        if full.all_sites:
            return qs
        if full.site_ids:
            cond |= Q(site_id__in=full.site_ids)
        mine = rbac.site_scope(membership, "incident.view_assigned")
        linked = Q(reported_by=membership)
        if apps.is_installed("apps.workorders"):  # M06: the source of work assigned to oneself
            from apps.workorders.models import WorkOrder

            assigned_source_numbers = WorkOrder.objects.for_organization(org).filter(
                assigned_to=membership,
                source_type__in=(WorkOrder.SourceType.REQUEST, WorkOrder.SourceType.INCIDENT),
            ).values("source_reference")
            linked |= Q(number__in=assigned_source_numbers)
        if mine.all_sites:
            cond |= linked
        elif mine.site_ids:
            cond |= linked & Q(site_id__in=mine.site_ids)
        return qs.filter(cond).distinct()
    return rbac.site_scope(membership, code).filter(qs, "site_id")


def get_request(membership, org, pk, code: str = "incident.view") -> ServiceRequest:
    return scoped_get(requests_for(membership, org, code), pk, "Request")


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def filter_requests(qs, params):
    for param, field in (("site", "site_id"), ("asset", "asset_id"), ("reported_by", "reported_by_id")):
        value = (params.get(param) or "").strip()
        if value:
            parsed = _uuid_or_none(value)
            qs = qs.filter(**{field: parsed}) if parsed else qs.none()
    for param, field, choices in (("status", "status", ServiceRequest.Status.values),
                                  ("severity", "severity", ServiceRequest.Severity.values),
                                  ("kind", "kind", ServiceRequest.Kind.values)):
        value = (params.get(param) or "").strip()
        if value:
            qs = qs.filter(**{field: value}) if value in choices else qs.none()
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(title__icontains=q) | Q(asset__asset_tag__icontains=q)
                       | Q(asset__name__icontains=q))
    return qs.order_by(ORDERINGS.get(params.get("ordering") or "", "-created_at"), "-id")


def history_for(org, sr: ServiceRequest):
    return ServiceRequestHistory.objects.for_organization(org).filter(request=sr).select_related("changed_by")


def downtime_for(org, sr: ServiceRequest) -> Downtime | None:
    return Downtime.objects.for_organization(org).filter(request=sr).first()


def evidence_for(org, sr: ServiceRequest):
    return Attachment.objects.for_organization(org).filter(
        content_type=ContentType.objects.get_for_model(ServiceRequest), object_id=str(sr.pk)).select_related(
        "uploaded_by")
