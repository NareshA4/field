from django.conf.urls.static import static  # noqa: F401
from django.urls import include, path
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
from apps.incidents.api_views import ServiceRequestViewSet
<<<<<<< HEAD
=======
from apps.inventory.api_views import (
    PartReservationViewSet,
    PartViewSet,
    StockBalanceViewSet,
    StockMovementViewSet,
    WarehouseViewSet,
    WorkOrderPartViewSet,
)
>>>>>>> ba3f639 (feat(m08): implement Inspection & Checklist Engine with Work Order integration)
from apps.notifications.api_views import NotificationViewSet
from apps.platform_admin.api_views import PlatformOrganizationViewSet
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
router.register("meters", MeterViewSet, basename="meter")
router.register("service-requests", ServiceRequestViewSet, basename="service-request")
router.register("platform/organizations", PlatformOrganizationViewSet, basename="platform-organization")
router.register("assets", AssetViewSet, basename="asset")
router.register("asset-categories", AssetCategoryViewSet, basename="asset-category")
router.register("asset-components", ComponentViewSet, basename="asset-component")
router.register("meters", MeterViewSet, basename="meter")
router.register("service-requests", ServiceRequestViewSet, basename="service-request")
router.register("work-orders", WorkOrderViewSet, basename="work-order")
router.register("checklist-templates", ChecklistTemplateViewSet, basename="checklist-template")
router.register("inspections", InspectionViewSet, basename="inspection")
router.register("findings", FindingViewSet, basename="finding")
router.register("parts", PartViewSet, basename="part")
router.register("warehouses", WarehouseViewSet, basename="warehouse")
router.register("stock-balances", StockBalanceViewSet, basename="stock-balance")
router.register("stock-movements", StockMovementViewSet, basename="stock-movement")
router.register("part-reservations", PartReservationViewSet, basename="part-reservation")
router.register("work-order-parts", WorkOrderPartViewSet, basename="work-order-part")
router.register("contract-providers", ProviderViewSet, basename="contract-provider")
router.register("coverage-agreements", AgreementViewSet, basename="coverage-agreement")
router.register("coverage-checks", CoverageCheckViewSet, basename="coverage-check")
router.register("coverage", CoverageViewSet, basename="coverage")
router.register("sla-profiles", SLAProfileViewSet, basename="sla-profile")
router.register("sla-trackings", SLATrackingViewSet, basename="sla-tracking")
router.register("sla-breaches", SLABreachViewSet, basename="sla-breach")

api_v1 = [
    path("auth/", include("apps.accounts.api_urls")),
    path("", include("apps.workorders.urls")),
    path("organization/", OrganizationViewSet.as_view({"get": "retrieve", "patch": "partial_update"}),
         name="organization"),
    path("", include(router.urls)),
    path("sla-metrics/", SLAMetricsView.as_view(), name="sla-metrics"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]

urlpatterns = [
    path("health/live/", health.live, name="health_live"),
    path("health/ready/", health.ready, name="health_ready"),
    path("api/v1/", include(api_v1)),
    path("accounts/", include("apps.accounts.urls")),
    path("platform/", include("apps.platform_admin.urls")),
    path("app/", include("apps.tenancy.urls")),
    path("app/", include("apps.rbac.urls")),
    path("app/", include("apps.audit.urls")),
    path("app/", include("apps.notifications.urls")),
    path("app/", include("apps.files.urls")),
    path("app/", include("apps.sites.urls")),
    path("app/", include("apps.assets.urls")),
    path("app/", include("apps.incidents.urls")),
<<<<<<< HEAD
    path("app/", include("apps.workorders.urls_ui")),
    # Module teams: add path("app/", include("apps.<module>.urls")) for your module here.
=======
    path("app/", include("apps.workorders.urls")),
    path("app/", include("apps.inventory.urls")),
    path("app/", include("apps.sla.urls")),
    path("app/", include("apps.contracts.urls")),
    path("app/", include("apps.checklists.urls")),
>>>>>>> ba3f639 (feat(m08): implement Inspection & Checklist Engine with Work Order integration)
    path("", include("apps.ui.urls")),
]

handler403 = "apps.ui.views.forbidden"
handler404 = "apps.ui.views.not_found"
handler500 = "apps.ui.views.server_error"
