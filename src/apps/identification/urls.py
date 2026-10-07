from django.urls import path
from . import views

app_name = "identification"

urlpatterns = [
    path("assets/<uuid:pk>/labels/panel/", views.LabelPanelView.as_view(), name="panel"),
    path("assets/<uuid:pk>/labels/<str:kind>/<str:fmt>/", views.LabelImageView.as_view(), name="image"),
    path("assets/<uuid:pk>/labels/<str:action>/", views.PanelActionView.as_view(), name="panel_action"),
    path("assets/<uuid:pk>/label/", views.LabelPrintView.as_view(), name="label"),
    path("scan/", views.ScanView.as_view(), name="scan"),
    path("s/<str:token>/", views.ResolveView.as_view(), name="resolve"),
]
