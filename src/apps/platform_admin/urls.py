from django.urls import path

from . import views

app_name = "platform_admin"
urlpatterns = [
    path("organizations/", views.OrganizationListView.as_view(), name="organizations"),
    path("organizations/new/", views.OrganizationCreateView.as_view(), name="organization_create"),
    path("organizations/<uuid:pk>/", views.OrganizationDetailView.as_view(), name="organization_detail"),
    path("audit/", views.PlatformAuditView.as_view(), name="audit"),
]
