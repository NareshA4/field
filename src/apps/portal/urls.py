from django.urls import path
from . import views

app_name = "portal"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("requests/", views.RequestListView.as_view(), name="requests_list"),
    path("requests/new/", views.RequestCreateView.as_view(), name="request_create"),
    path("requests/<str:pk>/", views.RequestDetailView.as_view(), name="request_detail"),
    path("requests/<str:pk>/confirm/", views.RequestConfirmView.as_view(), name="request_confirm"),
    path("requests/<str:pk>/reopen/", views.RequestReopenView.as_view(), name="request_reopen"),
    path("accounts/", views.AccountsListView.as_view(), name="accounts_list"),
    path("accounts/<str:pk>/toggle/", views.AccountToggleView.as_view(), name="account_toggle"),
]

