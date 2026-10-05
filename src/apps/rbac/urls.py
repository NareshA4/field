from django.urls import path

from . import views

app_name = "rbac"
urlpatterns = [
    path("roles/", views.RoleListView.as_view(), name="roles"),
    path("roles/new/", views.RoleCreateView.as_view(), name="role_create"),
    path("roles/<uuid:pk>/", views.RoleDetailView.as_view(), name="role_detail"),
]
