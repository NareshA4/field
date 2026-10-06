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
from apps.core import health
from apps.incidents.api_views import ServiceRequestViewSet
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
from apps.tenancy.api_views import MemberViewSet, OrganizationViewSet

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

api_v1 = [
    path("auth/", include("apps.accounts.api_urls")),
    path("organization/", OrganizationViewSet.as_view({"get": "retrieve", "patch": "partial_update"}),
         name="organization"),
    path("", include(router.urls)),
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
    # Module teams: add path("app/", include("apps.<module>.urls")) for your module here.
    path("", include("apps.ui.urls")),
]

handler403 = "apps.ui.views.forbidden"
handler404 = "apps.ui.views.not_found"
handler500 = "apps.ui.views.server_error"
