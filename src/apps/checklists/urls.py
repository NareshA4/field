from django.urls import path

from . import views

app_name = "checklists"
urlpatterns = [
    path("checklists/", views.TemplateListView.as_view(), name="list"),
    path("checklists/new/", views.TemplateCreateView.as_view(), name="create"),
    path("checklists/<uuid:pk>/", views.TemplateDetailView.as_view(), name="detail"),
    path("checklists/<uuid:pk>/edit/", views.TemplateEditView.as_view(), name="edit"),
    path("checklists/<uuid:pk>/items/add/", views.ItemAddView.as_view(), name="item_add"),
    path("checklists/<uuid:pk>/items/<uuid:item>/edit/", views.ItemEditView.as_view(), name="item_edit"),
    path("checklists/<uuid:pk>/items/<uuid:item>/remove/", views.ItemRemoveView.as_view(), name="item_remove"),
    path("checklists/<uuid:pk>/items/<uuid:item>/move/<str:direction>/", views.ItemMoveView.as_view(),
         name="item_move"),
    path("checklists/<uuid:pk>/<str:action>/", views.TemplateActionView.as_view(), name="template_action"),
    path("inspections/", views.InspectionListView.as_view(), name="inspections"),
    path("inspections/<uuid:pk>/", views.InspectionDetailView.as_view(), name="inspection"),
    path("findings/<uuid:pk>/resolve/", views.FindingResolveView.as_view(), name="finding_resolve"),
]
