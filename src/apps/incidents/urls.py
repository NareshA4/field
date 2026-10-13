from django.urls import path

from . import dashboard_views, views

app_name = "incidents"
urlpatterns = [
    path("incidents/dashboard/", dashboard_views.dashboard, name="dashboard"),
    path("incidents/", views.RequestListView.as_view(), name="list"),
    path("incidents/new/", views.RequestCreateView.as_view(), name="create"),
    path("incidents/check-duplicate/", views.RequestCheckDuplicateView.as_view(), name="check_duplicate"),
    path("incidents/<uuid:pk>/create-work-order/", views.RequestWorkOrderView.as_view(), name="create_work_order"),
    path("incidents/<uuid:pk>/", views.RequestDetailView.as_view(), name="detail"),
    path("incidents/<uuid:pk>/edit/", views.RequestEditView.as_view(), name="edit"),
    path("incidents/<uuid:pk>/transition/<str:action>/", views.RequestTransitionView.as_view(), name="transition"),
    path("incidents/<uuid:pk>/downtime/", views.RequestDowntimeView.as_view(), name="downtime"),
    path("incidents/<uuid:pk>/evidence/", views.RequestEvidenceView.as_view(), name="evidence"),
]
