from django.urls import path
from .api_views import (
    PortalAssetListView,
    PortalMeView,
    PortalRequestConfirmView,
    PortalRequestDetailView,
    PortalRequestListCreateView,
    PortalRequestReopenView,
)

app_name = "portal_api"

urlpatterns = [
    path("me/", PortalMeView.as_view(), name="me"),
    path("assets/", PortalAssetListView.as_view(), name="assets"),
    path("assets", PortalAssetListView.as_view()),
    path("requests/", PortalRequestListCreateView.as_view(), name="requests"),
    path("requests", PortalRequestListCreateView.as_view()),
    path("requests/<str:pk>/confirm/", PortalRequestConfirmView.as_view(), name="request_confirm"),
    path("requests/<str:pk>/confirm", PortalRequestConfirmView.as_view()),
    path("requests/<str:pk>/reopen/", PortalRequestReopenView.as_view(), name="request_reopen"),
    path("requests/<str:pk>/reopen", PortalRequestReopenView.as_view()),
    path("requests/<str:pk>/", PortalRequestDetailView.as_view(), name="request_detail"),
    path("requests/<str:pk>", PortalRequestDetailView.as_view()),
]


