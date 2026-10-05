from django.urls import path

from . import views

app_name = "audit"
urlpatterns = [
    path("audit/", views.AuditListView.as_view(), name="list"),
    path("audit/reports/", views.ReportsView.as_view(), name="reports"),
    path("audit/evidence/work-order/<uuid:pk>/", views.EvidencePackageView.as_view(), name="evidence_package"),
    path("audit/export/", views.ExportView.as_view(), name="export"),
    path("audit/<uuid:pk>/", views.AuditDetailView.as_view(), name="detail"),
]
