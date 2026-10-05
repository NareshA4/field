from django.urls import path
from django.views.generic import TemplateView

from . import views

app_name = "accounts"
urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("activate/<uidb64>/<token>/", views.ActivateView.as_view(), name="activate"),
    path("password-reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/done/", TemplateView.as_view(template_name="accounts/password_reset_done.html"),
         name="password_reset_done"),
    path("password-reset/<uidb64>/<token>/", views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("password-change/", views.PasswordChangeView.as_view(), name="password_change"),
    path("profile/", views.profile, name="profile"),
]
