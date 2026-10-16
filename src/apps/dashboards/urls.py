from django.urls import path

from . import views

app_name = "dashboards"

urlpatterns = [
    path("dashboards/", views.DashboardHubView.as_view(), name="hub"),
    path("dashboards/my-work/", views.MyWorkDashboardView.as_view(), name="my_work"),
]
