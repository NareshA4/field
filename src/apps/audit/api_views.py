import uuid

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action

from apps.core.apiutils import query_param
from apps.core.exceptions import ValidationFailed
from apps.tenancy.api import TenantAPIMixin

from . import exports, selectors, services
from .models import AuditLog
from .views import clean_params, export_logs

FILTER_PARAMS = [
    query_param("action", "action prefix, e.g. work_order."), query_param("q", "actor / entity / action text"),
    query_param("actor", "actor e-mail contains"), query_param("entity_type", "e.g. workorders.workorder"),
    query_param("entity_id", "id of the audited record"), query_param("request_id", "request id"),
    query_param("site", "site id", OpenApiTypes.UUID),
    query_param("category", "security | state | assignment | approvals | closures | inventory | sla | evidence | "
                            "coverage | client | exports"),
    query_param("asset", "history of one asset and its work orders / requests", OpenApiTypes.UUID),
    query_param("work_order", "lifecycle of one work order", OpenApiTypes.UUID),
    query_param("from", "first day YYYY-MM-DD", OpenApiTypes.DATE), query_param("to", "last day YYYY-MM-DD",
                                                                                   OpenApiTypes.DATE)]


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = ["id", "occurred_at", "action", "actor_email", "target_type", "target_id", "target_repr", "site_id",
                  "before", "after", "metadata", "request_id"]
        read_only_fields = fields


class AuditLogViewSet(TenantAPIMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only by construction: no create / update / delete route exists. Rows are written only by the services
    through ``audit.record`` and are immutable at the database level."""

    serializer_class = AuditLogSerializer
    permission_map = {"list": "audit.view", "retrieve": "audit.view", "export": "audit.export",
                      "evidence": "audit.export"}
    filterset_fields: list = []

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return AuditLog.objects.none()
        qs = selectors.visible_logs(self.request.membership, self.request.organization)
        return selectors.search(qs, self.request.query_params, org=self.request.organization)

    @extend_schema(parameters=FILTER_PARAMS + [query_param("export_format", "csv | xlsx | pdf (default csv)")],
                   responses=OpenApiTypes.BINARY)
    @action(detail=False, methods=["get"])
    def export(self, request):
        fmt = (request.query_params.get("export_format") or "csv").lower()
        if fmt not in exports.FORMATS:
            raise ValidationFailed("Unknown export format (csv, xlsx or pdf).", code="invalid_format")
        from django.http import HttpResponse

        qs = selectors.search(export_logs(request.membership, request.organization), request.query_params,
                              org=request.organization)
        params = clean_params(request.query_params)
        result = exports.build(fmt, qs, request.organization, actor=request.user,
                               filters_text=", ".join(f"{k}={v}" for k, v in params.items()))
        services.record("audit.exported", actor=request.user, organization=request.organization, request=request,
                        metadata={"format": fmt, "rows": result.rows, "truncated": result.truncated,
                                  "filters": params})
        resp = HttpResponse(result.body, content_type=result.content_type)
        resp["Content-Disposition"] = f'attachment; filename="{result.filename}"'
        resp["Cache-Control"] = "private, no-store"
        return resp

    @extend_schema(parameters=[query_param("work_order", "work order id", OpenApiTypes.UUID)],
                   responses=OpenApiTypes.BINARY)
    @action(detail=False, methods=["get"])
    def evidence(self, request):
        """Per-work-order evidence package (ZIP); same scope rules as the UI download."""
        from django.http import HttpResponse

        from . import evidence as evidence_package

        pk = (request.query_params.get("work_order") or "").strip()
        if not pk:
            raise ValidationFailed("work_order is required.", code="work_order_required")
        try:
            uuid.UUID(pk)
        except ValueError as exc:
            raise ValidationFailed("work_order must be a valid id.", code="invalid_work_order") from exc
        pkg = evidence_package.build(request.membership, request.organization, pk, actor=request.user,
                                     request=request)
        resp = HttpResponse(pkg.body, content_type="application/zip")
        resp["Content-Disposition"] = f'attachment; filename="{pkg.filename}"'
        resp["Cache-Control"] = "private, no-store"
        return resp

    @extend_schema(parameters=FILTER_PARAMS)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
