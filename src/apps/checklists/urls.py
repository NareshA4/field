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
    path("inspections/<uuid:pk>/run/", views.InspectionRunView.as_view(), name="inspection_run"),
    path("inspections/<uuid:pk>/modal/", views.InspectionModalView.as_view(), name="inspection_modal"),
    path("inspections/<uuid:pk>/items/<uuid:item_id>/answer/", views.InspectionSaveAnswerView.as_view(),
         name="inspection_save_answer"),
    path("inspections/<uuid:pk>/items/<uuid:item_id>/evidence/", views.InspectionUploadEvidenceView.as_view(),
         name="inspection_upload_evidence"),
    path("inspections/<uuid:pk>/items/<uuid:item_id>/finding/", views.InspectionAddFindingView.as_view(),
         name="inspection_add_finding"),
    path("inspections/<uuid:pk>/complete/", views.InspectionCompleteView.as_view(), name="inspection_complete"),
    path("findings/<uuid:pk>/resolve/", views.FindingResolveView.as_view(), name="finding_resolve"),
]

