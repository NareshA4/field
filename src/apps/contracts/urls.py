from django.urls import path

from . import views

app_name = "contracts"
urlpatterns = [
    path("contracts/agreements/", views.AgreementListView.as_view(), name="agreements"),
    path("contracts/agreements/new/", views.AgreementCreateView.as_view(), name="agreement_new"),
    path("contracts/agreements/<uuid:pk>/", views.AgreementDetailView.as_view(), name="agreement"),
    path("contracts/agreements/<uuid:pk>/edit/", views.AgreementEditView.as_view(), name="agreement_edit"),
    path("contracts/agreements/<uuid:pk>/renew/", views.AgreementRenewView.as_view(), name="agreement_renew"),
    path("contracts/agreements/<uuid:pk>/<str:action>/", views.AgreementActionView.as_view(),
         name="agreement_action"),
    path("contracts/expiry/", views.ExpiryView.as_view(), name="expiry"),
    path("contracts/providers/", views.ProviderListView.as_view(), name="providers"),
    path("contracts/providers/new/", views.ProviderCreateView.as_view(), name="provider_new"),
    path("contracts/providers/<uuid:pk>/edit/", views.ProviderEditView.as_view(), name="provider_edit"),
    path("contracts/providers/<uuid:pk>/active/", views.ProviderActiveView.as_view(), name="provider_active"),
    path("contracts/assets/<uuid:asset_id>/panel/", views.AssetCoveragePanelView.as_view(), name="asset_panel"),
    path("contracts/work-orders/<uuid:wo_id>/panel/", views.WorkOrderCoveragePanelView.as_view(),
         name="wo_panel"),
]
