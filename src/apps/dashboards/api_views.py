from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.apiutils import query_param
from apps.core.exceptions import PermissionDenied, ValidationFailed
from apps.dashboards import metrics
from apps.rbac import services as rbac
from apps.tenancy.api import MEMBER_ONLY, TenantAPIMixin


class DashboardCatalogAPIView(TenantAPIMixin, APIView):
    """Authoritative catalog of operational KPI definitions, formulas, and data sources."""

    permission_map = {"get": "report.view"}

    @extend_schema(
        summary="List KPI catalog definitions",
        description="Returns definitions, formulas, sources, and scope metadata for all operational KPIs.",
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        return Response({
            "status": "success",
            "organization_id": str(request.organization.pk),
            "sections": [
                "operations",
                "assets",
                "maintenance",
                "service",
                "inventory",
                "my-work",
            ],
            "kpis": metrics.KPI_DEFINITIONS,
        })


class DashboardSectionAPIView(TenantAPIMixin, APIView):
    """Provides aggregated metrics for a specific operational dashboard section."""

    permission_map = {"get": MEMBER_ONLY}

    SECTION_DATA_PERMISSIONS = {
        "operations": "work_order.view",
        "assets": "asset.view",
        "maintenance": "work_order.view",
        "service": "incident.view",
        "inventory": "inventory.part.view",
        "my_work": None,
    }

    @extend_schema(
        summary="Get operational dashboard section metrics",
        description="Returns real aggregated metrics for a section with date window and site filters.",
        parameters=[
            query_param("from", "Start date (YYYY-MM-DD), defaults to 30 days ago"),
            query_param("to", "End date (YYYY-MM-DD), defaults to today"),
            query_param("site", "Specific site UUID or code filter"),
        ],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, section: str):
        normalized_section = section.strip().lower().replace("-", "_")
        valid_sections = set(self.SECTION_DATA_PERMISSIONS.keys())
        if normalized_section not in valid_sections:
            valid_list = ", ".join(sorted(valid_sections | {"my-work"}))
            raise ValidationFailed(
                f"Unknown dashboard section '{section}'. Valid sections are: {valid_list}."
            )

        # 1. Section permission checks (D-052)
        if normalized_section != "my_work":
            if not rbac.has_permission_anywhere(request.membership, "report.view"):
                raise PermissionDenied("report.view permission required.")

            required_data_perm = self.SECTION_DATA_PERMISSIONS.get(normalized_section)
            if required_data_perm:
                if not rbac.catalog.is_registered(required_data_perm):
                    required_data_perm = "work_order.view"

                if not rbac.has_permission_anywhere(request.membership, required_data_perm):
                    raise PermissionDenied(
                        f"'{required_data_perm}' permission required for {section} dashboard."
                    )

        # 2. Date window bounds (UTC, default 30 days, max 400 days)
        from_str = request.query_params.get("from")
        to_str = request.query_params.get("to")
        start_dt, end_dt = metrics.parse_date_window(from_str, to_str)

        # 3. Permitted site resolution
        requested_site = request.query_params.get("site")
        permitted_sites = []
        if normalized_section != "my_work":
            permitted_sites = metrics.resolve_permitted_sites(
                membership=request.membership,
                organization=request.organization,
                section=normalized_section,
                requested_site_id=requested_site,
            )
            if not permitted_sites:
                raise PermissionDenied("No permitted sites available for this dashboard section.")

        # 4. Metric aggregation dispatch
        if normalized_section == "operations":
            data = metrics.get_operations_dashboard(request.organization, permitted_sites, start_dt, end_dt)
        elif normalized_section == "assets":
            data = metrics.get_assets_dashboard(request.organization, permitted_sites, start_dt, end_dt)
        elif normalized_section == "maintenance":
            data = metrics.get_maintenance_dashboard(request.organization, permitted_sites, start_dt, end_dt)
        elif normalized_section == "service":
            data = metrics.get_service_dashboard(request.organization, permitted_sites, start_dt, end_dt)
        elif normalized_section == "inventory":
            data = metrics.get_inventory_dashboard(request.organization, permitted_sites, start_dt, end_dt)
        elif normalized_section == "my_work":
            data = metrics.get_my_work_dashboard(request.membership, start_dt, end_dt)

        response_payload = {
            "status": "success",
            "organization_id": str(request.organization.pk),
            "section": section,
            "normalized_section": normalized_section,
            "date_window": {
                "from": start_dt.date().isoformat(),
                "to": end_dt.date().isoformat(),
                "days": (end_dt.date() - start_dt.date()).days + 1,
            },
            "sites": [
                {"id": str(s.pk), "code": s.code, "name": s.name}
                for s in permitted_sites
            ] if normalized_section != "my_work" else [],
            "data": data,
        }
        return Response(response_payload, status=status.HTTP_200_OK)
