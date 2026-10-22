from django.urls import path

from . import views

app_name = "ui"
urlpatterns = [
    path("", views.root, name="root"),
    path("features/", views.public_features, name="public_features"),
    path("solutions/", views.public_solutions, name="public_solutions"),
    path("solutions/<slug:slug>/", views.public_solution, name="public_solution"),
    path("modules/", views.public_modules, name="public_modules"),
    path("modules/<str:module_id>/", views.public_module, name="public_module"),
    path("resources/", views.public_resources, name="public_resources"),
    path("about/", views.public_about, name="public_about"),
    path("register/", views.client_register, name="client_register"),
    path("client-register/", views.client_register, name="client_register_alias"),
    path("legal/<slug:slug>/", views.public_legal, name="public_legal"),
    path("app/", views.home, name="home"),
    path("app/choose-organization/", views.choose_org, name="choose_org"),
    path("app/switch-organization/", views.switch_org, name="switch_org"),
    path("app/search/", views.search, name="search"),
]
