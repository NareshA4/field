from django.urls import path

from . import views

app_name = "workorders"
urlpatterns = [
    path("work-orders/", views.WorkOrderListView.as_view(), name="list"),
    path("work-orders/new/", views.WorkOrderCreateView.as_view(), name="create"),
    path("work-orders/<uuid:pk>/", views.WorkOrderDetailView.as_view(), name="detail"),
    path("work-orders/<uuid:pk>/edit/", views.WorkOrderEditView.as_view(), name="edit"),
    path("work-orders/<uuid:pk>/transition/<str:action>/", views.WorkOrderTransitionView.as_view(), name="transition"),
    path("work-orders/<uuid:pk>/reassign/", views.WorkOrderReassignView.as_view(), name="reassign"),
    path("work-orders/<uuid:pk>/labor/", views.WorkOrderLaborView.as_view(), name="labor"),
    path("work-orders/<uuid:pk>/materials/", views.WorkOrderMaterialView.as_view(), name="materials"),
    path("work-orders/<uuid:pk>/evidence/", views.WorkOrderEvidenceView.as_view(), name="evidence"),
]
