from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse_lazy

from apps.audit import services as audit

from . import services
from .forms import EmailAuthenticationForm, ProfileForm


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    pass  # POST only (Django 5); fires user_logged_out -> audit


class ActivateView(auth_views.PasswordResetConfirmView):
    """Invitation link: the invited user chooses a password; pending memberships become ACTIVE."""

    template_name = "accounts/activate.html"
    success_url = reverse_lazy("accounts:login")

    def form_valid(self, form):
        response = super().form_valid(form)
        services.activate_pending_memberships(form.user, request=self.request)
        audit.record("auth.password_set", actor=form.user, target=form.user, target_repr=form.user.email,
                     request=self.request)
        messages.success(self.request, "Your password is set. Sign in to continue.")
        return response


class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/email/password_reset.txt"
    subject_template_name = "accounts/email/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:login")

    def form_valid(self, form):
        response = super().form_valid(form)
        audit.record("auth.password_reset", actor=form.user, target=form.user, target_repr=form.user.email,
                     request=self.request)
        messages.success(self.request, "Password updated. Sign in with your new password.")
        return response


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:profile")

    def form_valid(self, form):
        response = super().form_valid(form)
        audit.record("auth.password_changed", actor=self.request.user, target=self.request.user,
                     target_repr=self.request.user.email, request=self.request)
        messages.success(self.request, "Password changed.")
        return response


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        before = {"full_name": request.user.full_name, "phone": request.user.phone}
        form.save()
        audit.record("user.profile_updated", actor=request.user, target=request.user,
                     target_repr=request.user.email, before=before,
                     after={"full_name": form.instance.full_name, "phone": form.instance.phone},
                     request=request)
        messages.success(request, "Profile updated.")
        return redirect("accounts:profile")
    return render(request, "accounts/profile.html", {"form": form})
