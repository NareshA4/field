from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views import View

from apps.core.exceptions import DomainError, NotFound, PermissionDenied
from apps.rbac import services as rbac
from apps.sites.models import Site
from apps.ui.mixins import TenantPermissionMixin

from . import evidence, exports, selectors, services

FILTER_KEYS = ("action", "q", "actor", "entity_type", "entity_id", "request_id", "site", "category", "asset",
               "work_order", "from", "to")


def export_logs(membership, org):
    """Visible rows restricted again to the sites where the caller may EXPORT (audit.export)."""
    qs = selectors.visible_logs(membership, org)
    scope = rbac.site_scope(membership, "audit.export")
    return qs if scope.all_sites else qs.filter(site_id__in=scope.site_ids)


def clean_params(params):
    return {k: params.get(k) for k in FILTER_KEYS if params.get(k)}


class AuditListView(TenantPermissionMixin, View):
    required_permission = "audit.view"

    def get(self, request):
        m, org = request.membership, request.organization
        base = selectors.visible_logs(m, org)
        error = None
        try:
            qs = selectors.search(base, request.GET, org=org)
        except DomainError as exc:
            messages.error(request, exc.message)
            error, qs = exc.message, base.none()
        page = Paginator(qs, 25).get_page(request.GET.get("page"))
        sites = {s.pk: s.code for s in Site.objects.for_organization(org)}
        for e in page:
            e.site_code = sites.get(e.site_id, "")
        return render(request, "audit/list.html", {
            "page": page, "filters": request.GET, "categories": [(k, v[0]) for k, v in selectors.CATEGORIES.items()],
            "entity_types": selectors.entity_types(base), "sites": sorted(sites.items(), key=lambda kv: kv[1]),
            "can_export": rbac.has_permission_anywhere(m, "audit.export"), "error": error,
            "query": request.GET.urlencode(), "total_hint": exports.EXPORT_LIMIT})


class AuditDetailView(TenantPermissionMixin, View):
    required_permission = "audit.view"

    def get(self, request, pk):
        entry = selectors.visible_logs(request.membership, request.organization).filter(pk=pk).first()
        if entry is None:
            raise Http404
        site = Site.objects.for_organization(request.organization).filter(pk=entry.site_id).first() \
            if entry.site_id else None
        return render(request, "audit/detail.html", {"entry": entry, "site": site})


class ReportsView(TenantPermissionMixin, View):
    """Compliance reports = ready-made views over the audit trail, so they can never disagree with it."""

    required_permission = "audit.view"

    def get(self, request):
        return render(request, "audit/reports.html", {
            "categories": [(k, v[0], v[1]) for k, v in selectors.CATEGORIES.items()]})


class EvidencePackageView(TenantPermissionMixin, View):
    """ZIP evidence package for one work order (summary, timeline, approvals, checklists, parts, SLA, coverage,
    audit trail, the evidence files and a checksum manifest). Scope rules live in ``audit.evidence.build``."""

    required_permission = "audit.export"

    def get(self, request, pk):
        try:
            pkg = evidence.build(request.membership, request.organization, pk, actor=request.user, request=request)
        except NotFound as exc:
            raise Http404 from exc
        except PermissionDenied:
            return HttpResponse("You may not export evidence for this site.", status=403)
        resp = HttpResponse(pkg.body, content_type="application/zip")
        resp["Content-Disposition"] = f'attachment; filename="{pkg.filename}"'
        resp["Cache-Control"] = "private, no-store"
        resp["X-Content-Type-Options"] = "nosniff"
        return resp


class ExportView(TenantPermissionMixin, View):
    required_permission = "audit.export"

    def get(self, request):
        m, org = request.membership, request.organization
        fmt = (request.GET.get("format") or "csv").lower()
        if fmt not in exports.FORMATS:
            return HttpResponse("Unknown export format.", status=400)
        try:
            qs = selectors.search(export_logs(m, org), request.GET, org=org)
        except DomainError as exc:
            return HttpResponse(exc.message, status=400)
        params = clean_params(request.GET)
        result = exports.build(fmt, qs, org, actor=request.user,
                               filters_text=", ".join(f"{k}={v}" for k, v in params.items()))
        services.record("audit.exported", actor=request.user, organization=org, request=request,
                        metadata={"format": fmt, "rows": result.rows, "truncated": result.truncated,
                                  "filters": params})
        resp = HttpResponse(result.body, content_type=result.content_type)
        resp["Content-Disposition"] = f'attachment; filename="{result.filename}"'
        resp["Cache-Control"] = "private, no-store"
        resp["X-Content-Type-Options"] = "nosniff"
        return resp
