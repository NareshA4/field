from django.urls import path

from . import views

app_name = "notifications"
urlpatterns = [
    path("notifications/", views.NotificationListView.as_view(), name="list"),
    path("notifications/bell/", views.BellView.as_view(), name="bell"),
    path("notifications/read-all/", views.MarkAllReadView.as_view(), name="read_all"),
    path("notifications/<uuid:pk>/read/", views.MarkReadView.as_view(), name="read"),
]
