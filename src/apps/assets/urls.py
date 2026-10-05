from django.urls import path

from . import views

app_name = "assets"
urlpatterns = [
    path("assets/", views.AssetListView.as_view(), name="list"),
    path("assets/new/", views.AssetCreateView.as_view(), name="create"),
    path("assets/categories/", views.CategoryView.as_view(), name="categories"),
    path("assets/<uuid:pk>/", views.AssetDetailView.as_view(), name="detail"),
    path("assets/<uuid:pk>/edit/", views.AssetEditView.as_view(), name="edit"),
    path("assets/<uuid:pk>/tree/", views.AssetTreeView.as_view(), name="tree"),
    path("assets/<uuid:pk>/transition/<str:action>/", views.AssetStatusView.as_view(), name="transition"),
    path("assets/<uuid:pk>/documents/", views.DocumentUploadView.as_view(), name="document_upload"),
    path("assets/documents/<uuid:pk>/remove/", views.DocumentRemoveView.as_view(), name="document_remove"),
    path("assets/<uuid:pk>/meters/new/", views.MeterCreateView.as_view(), name="meter_create"),
    path("assets/<uuid:pk>/components/add/", views.ComponentAddView.as_view(), name="component_add"),
    path("meters/<uuid:pk>/reading/", views.MeterReadingView.as_view(), name="meter_reading"),
    path("meters/<uuid:pk>/toggle/", views.MeterToggleView.as_view(), name="meter_toggle"),
    path("components/<uuid:pk>/edit/", views.ComponentEditView.as_view(), name="component_edit"),
    path("components/<uuid:pk>/move/", views.ComponentMoveView.as_view(), name="component_move"),
    path("components/<uuid:pk>/remove/", views.ComponentRemoveView.as_view(), name="component_remove"),
]
