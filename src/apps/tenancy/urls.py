from django.urls import path

from . import views

app_name = "tenancy"
urlpatterns = [
    path("organization/", views.OrganizationView.as_view(), name="organization"),
    path("users/", views.MemberListView.as_view(), name="members"),
    path("users/invite/", views.MemberInviteView.as_view(), name="member_invite"),
    path("users/<uuid:pk>/", views.MemberDetailView.as_view(), name="member_detail"),
]
