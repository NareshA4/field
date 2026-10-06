from django.urls import path

from . import views

app_name = "incidents"
urlpatterns = [
    path("incidents/", views.RequestListView.as_view(), name="list"),
    path("incidents/new/", views.RequestCreateView.as_view(), name="create"),
<<<<<<< HEAD
    path("incidents/<uuid:pk>/create-work-order/", views.RequestWorkOrderView.as_view(), name="create_work_order"),
    path("incidents/<uuid:pk>/", views.RequestDetailView.as_view(), name="detail"),
    path("incidents/<uuid:pk>/edit/", views.RequestEditView.as_view(), name="edit"),
    path("incidents/<uuid:pk>/transition/<str:action>/", views.RequestTransitionView.as_view(), name="transition"),
=======
    path("incidents/<uuid:pk>/", views.RequestDetailView.as_view(), name="detail"),
    path("incidents/<uuid:pk>/edit/", views.RequestEditView.as_view(), name="edit"),
    path("incidents/<uuid:pk>/transition/<str:action>/", views.RequestTransitionView.as_view(), name="transition"),
    path("incidents/<uuid:pk>/create-work-order/", views.RequestCreateWorkOrderView.as_view(),
         name="create_work_order"),
>>>>>>> ba3f639 (feat(m08): implement Inspection & Checklist Engine with Work Order integration)
    path("incidents/<uuid:pk>/downtime/", views.RequestDowntimeView.as_view(), name="downtime"),
    path("incidents/<uuid:pk>/evidence/", views.RequestEvidenceView.as_view(), name="evidence"),
]
