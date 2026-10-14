from django.urls import path

from . import api_views

urlpatterns = [
    path("dashboards/", api_views.DashboardCatalogAPIView.as_view(), name="api_dashboards_catalog"),
    path("dashboards/<str:section>/", api_views.DashboardSectionAPIView.as_view(), name="api_dashboards_section"),
]
