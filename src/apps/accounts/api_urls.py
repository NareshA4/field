from django.urls import path

from . import api

urlpatterns = [
    path("token/", api.TokenObtain.as_view(), name="token"),
    path("token/refresh/", api.TokenRefresh.as_view(), name="token_refresh"),
    path("me/", api.MeView.as_view(), name="me"),
]
