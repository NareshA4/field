from django.urls import path

from apps.incidents.dashboard_views import platform_dashboard

from . import platform_views

app_name = "platform_admin"
urlpatterns = [
    path("incidents/", platform_dashboard, name="incident_dashboard"),
    path("organizations/", platform_views.organizations, name="organizations"),
    path("audit/", platform_views.audit, name="audit"),
]
