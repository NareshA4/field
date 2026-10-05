from django.urls import path

from . import views

app_name = "ui"
urlpatterns = [
    path("", views.root, name="root"),
    path("app/", views.home, name="home"),
    path("app/choose-organization/", views.choose_org, name="choose_org"),
    path("app/switch-organization/", views.switch_org, name="switch_org"),
    path("app/search/", views.search, name="search"),
]
