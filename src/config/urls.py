from django.conf.urls.static import static  # noqa: F401
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import SimpleRouter

from apps.assets.api_views import (
    AssetCategoryViewSet,
    AssetViewSet,
    ComponentViewSet,
    MeterViewSet,
)
from apps.audit.api_views import AuditLogViewSet
from apps.checklists.api_views import (
    ChecklistTemplateViewSet,
    FindingViewSet,
    InspectionViewSet,
)
from apps.contracts.api_views import (
    AgreementViewSet,
    CoverageCheckViewSet,
    CoverageViewSet,
    ProviderViewSet,
)
from apps.core import health
from apps.identification.api_views import IdentifierViewSet
from apps.incidents.api_views import ServiceRequestViewSet
from apps.inventory.api_views import (
    PartReservationViewSet,
    PartViewSet,
    StockBalanceViewSet,
    StockMovementViewSet,
    WarehouseViewSet,
    WorkOrderPartViewSet,
)
from apps.maintenance.api_views import (
    MaintenanceCycleViewSet,
    MaintenancePlanViewSet,
    MaintenanceScheduleViewSet,
)
from apps.notifications.api_views import NotificationViewSet
from apps.portal.api_views import PortalAccountViewSet
from apps.rbac.api_views import PermissionViewSet, RoleViewSet
from apps.sites.api_views import (
    CalendarViewSet,
    ContactViewSet,
    HolidayViewSet,
    SiteViewSet,
    ZoneViewSet,
)
from apps.sla.api_views import (
    SLABreachViewSet,
    SLAMetricsView,
    SLAProfileViewSet,
    SLATrackingViewSet,
)
from apps.tenancy.api_views import MemberViewSet, OrganizationViewSet
from apps.workorders.api_views import WorkOrderViewSet

# Module teams: import your viewsets here and register them on the router.
router = SimpleRouter()
router.register("members", MemberViewSet, basename="member")
router.register("roles", RoleViewSet, basename="role")
router.register("permissions", PermissionViewSet, basename="permission")
router.register("audit-logs", AuditLogViewSet, basename="auditlog")
router.register("notifications", NotificationViewSet, basename="notification")
router.register("sites", SiteViewSet, basename="site")
router.register("zones", ZoneViewSet, basename="zone")
router.register("calendars", CalendarViewSet, basename="calendar")
router.register("calendar-holidays", HolidayViewSet, basename="calendar-holiday")
router.register("site-contacts", ContactViewSet, basename="site-contact")
router.register("assets", AssetViewSet, basename="asset")
router.register("asset-categories", AssetCategoryViewSet, basename="asset-category")
router.register("asset-components", ComponentViewSet, basename="asset-component")
router.register("asset-meters", MeterViewSet, basename="asset-meter")
router.register("asset-identifiers", IdentifierViewSet, basename="asset-identifier")
router.register("service-requests", ServiceRequestViewSet, basename="service-request")
router.register("requests", ServiceRequestViewSet, basename="request")
router.register("work-orders", WorkOrderViewSet, basename="work-order")
router.register("workorders", WorkOrderViewSet, basename="workorder")
router.register("checklist-templates", ChecklistTemplateViewSet, basename="checklist-template")
router.register("inspections", InspectionViewSet, basename="inspection")
router.register("findings", FindingViewSet, basename="finding")
router.register("parts", PartViewSet, basename="part")
router.register("warehouses", WarehouseViewSet, basename="warehouse")
router.register("stock-balances", StockBalanceViewSet, basename="stock-balance")
router.register("stock-movements", StockMovementViewSet, basename="stock-movement")
router.register("part-reservations", PartReservationViewSet, basename="part-reservation")
router.register("work-order-parts", WorkOrderPartViewSet, basename="work-order-part")
router.register("maintenance-plans", MaintenancePlanViewSet, basename="maintenance-plan")
router.register("maintenance-schedules", MaintenanceScheduleViewSet, basename="maintenance-schedule")
router.register("maintenance-cycles", MaintenanceCycleViewSet, basename="maintenance-cycle")
router.register("contract-providers", ProviderViewSet, basename="contract-provider")
router.register("coverage-agreements", AgreementViewSet, basename="coverage-agreement")
router.register("coverage-checks", CoverageCheckViewSet, basename="coverage-check")
router.register("coverage", CoverageViewSet, basename="coverage")
router.register("sla-profiles", SLAProfileViewSet, basename="sla-profile")
router.register("sla-trackings", SLATrackingViewSet, basename="sla-tracking")
router.register("sla-breaches", SLABreachViewSet, basename="sla-breach")
router.register("portal-accounts", PortalAccountViewSet, basename="portal-account")

api_v1 = [
    path("auth/", include("apps.accounts.api_urls")),
    path("organization/", OrganizationViewSet.as_view({"get": "retrieve", "patch": "partial_update"}),
         name="organization"),
    path("portal/", include("apps.portal.api_urls")),
    path("", include(router.urls)),
    path("", include("apps.workspace.api_urls")),
    path("", include("apps.dashboards.api_urls")),
    path("sla-metrics/", SLAMetricsView.as_view(), name="sla-metrics"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]

urlpatterns = [
    path("docs/", RedirectView.as_view(url="/api/v1/docs/", permanent=False)),
    path("swagger/", RedirectView.as_view(url="/api/v1/docs/", permanent=False)),
    path("health/live/", health.live, name="health_live"),
    path("health/ready/", health.ready, name="health_ready"),
    path("platform/", include("apps.tenancy.platform_urls")),
    path("api/v1/", include(api_v1)),
    path("accounts/", include("apps.accounts.urls")),
    path("app/portal/", include("apps.portal.urls")),
    path("app/", include("apps.tenancy.urls")),
    path("app/", include("apps.rbac.urls")),
    path("app/", include("apps.audit.urls")),
    path("app/", include("apps.notifications.urls")),
    path("app/", include("apps.files.urls")),
    path("app/", include("apps.sites.urls")),
    path("app/", include("apps.assets.urls")),
    path("app/", include("apps.identification.urls")),
    path("app/", include("apps.incidents.urls")),
    path("app/", include("apps.workorders.urls_ui")),
    path("app/", include("apps.inventory.urls")),
    path("app/", include("apps.maintenance.urls")),
    path("app/", include("apps.sla.urls")),
    path("app/", include("apps.contracts.urls")),
    path("app/", include("apps.checklists.urls")),
    path("app/", include("apps.workspace.urls")),
    path("app/", include("apps.dashboards.urls")),
    path("", include("apps.ui.urls")),
]

handler403 = "apps.ui.views.forbidden"
handler404 = "apps.ui.views.not_found"
handler500 = "apps.ui.views.server_error"
