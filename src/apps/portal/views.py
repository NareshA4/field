from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import TemplateView

from apps.core.exceptions import DomainError
from apps.incidents.models import ServiceRequest
from apps.incidents.selectors import evidence_for
from apps.ui.mixins import TenantPermissionMixin

from . import forms, selectors, services


class DashboardView(LoginRequiredMixin, TemplateView):
    """Client / Requester portal dashboard.

    Accessible strictly to authenticated members who hold portal permissions
    and an active, enabled PortalAccount within the current organization.
    """

    template_name = "portal/dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if request.tenant_error:
            return render(request, "errors/tenant.html", {"code": request.tenant_error}, status=403)

        if request.membership is None:
            return redirect("ui:choose_org")

        try:
            self.account = services.verify_portal_access(request.membership)
        except Exception as exc:
            raise PermissionDenied(str(exc)) from exc

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["account"] = self.account
        ctx["membership"] = self.request.membership
        ctx["org"] = self.request.organization
        ctx["company_name"] = self.account.company_name or self.request.organization.name
        ctx["created_ref"] = self.request.GET.get("created")
        ctx["recent_requests"] = (
            ServiceRequest.objects.for_organization(self.request.organization)
            .filter(reported_by=self.request.membership)
            .select_related("asset", "site")
            .order_by("-created_at")[:5]
        )
        return ctx


class RequestCreateView(LoginRequiredMixin, View):
    """Client / Requester view to create and submit a new service request."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if request.tenant_error:
            return render(request, "errors/tenant.html", {"code": request.tenant_error}, status=403)

        if request.membership is None:
            return redirect("ui:choose_org")

        try:
            self.account = services.verify_portal_request_create_access(request.membership)
        except Exception as exc:
            raise PermissionDenied(str(exc)) from exc

        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        form = forms.PortalServiceRequestForm(organization=request.organization, membership=request.membership)
        return render(
            request,
            "portal/request_create.html",
            {
                "form": form,
                "account": self.account,
                "company_name": self.account.company_name or request.organization.name,
                "org": request.organization,
            },
        )

    def post(self, request):
        form = forms.PortalServiceRequestForm(
            request.POST, request.FILES, organization=request.organization, membership=request.membership
        )
        if form.is_valid():
            d = form.cleaned_data
            attachment = d.pop("attachment", None)
            attachments = [attachment] if attachment else []
            try:
                sr = services.create_portal_service_request(
                    request.membership,
                    asset=d["asset"],
                    title=d["title"],
                    description=d.get("description", ""),
                    severity=d.get("severity", ServiceRequest.Severity.MEDIUM),
                    service_impact=d.get("service_impact", ServiceRequest.Impact.NONE),
                    impact_notes=d.get("impact_notes", ""),
                    attachments=attachments,
                    actor=request.user,
                    request=request,
                )
                messages.success(
                    request,
                    f"Service request {sr.number} has been submitted successfully.",
                )
                return redirect(f"/app/portal/?created={sr.number}")
            except DomainError as exc:
                form.add_error(None, exc.message)

        return render(
            request,
            "portal/request_create.html",
            {
                "form": form,
                "account": self.account,
                "company_name": self.account.company_name or request.organization.name,
                "org": request.organization,
            },
            status=400,
        )


class RequestListView(LoginRequiredMixin, View):
    """Client / Requester view to browse and track submitted service requests."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if request.tenant_error:
            return render(request, "errors/tenant.html", {"code": request.tenant_error}, status=403)

        if request.membership is None:
            return redirect("ui:choose_org")

        try:
            self.account = services.verify_portal_access(request.membership)
        except Exception as exc:
            raise PermissionDenied(str(exc)) from exc

        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        qs = selectors.client_requests_for(request.membership)

        status_filter = request.GET.get("status", "").strip().upper()
        if status_filter:
            qs = qs.filter(status=status_filter)

        search_query = request.GET.get("q", "").strip()
        if search_query:
            qs = qs.filter(Q(title__icontains=search_query) | Q(number__icontains=search_query))

        paginator = Paginator(qs, 15)
        page_obj = paginator.get_page(request.GET.get("page"))

        return render(
            request,
            "portal/requests_list.html",
            {
                "page_obj": page_obj,
                "account": self.account,
                "company_name": self.account.company_name or request.organization.name,
                "org": request.organization,
                "status_filter": status_filter,
                "search_query": search_query,
                "statuses": ServiceRequest.Status.choices,
            },
        )


class RequestDetailView(LoginRequiredMixin, View):
    """Client / Requester view to inspect status tracking and progress of a request."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if request.tenant_error:
            return render(request, "errors/tenant.html", {"code": request.tenant_error}, status=403)

        if request.membership is None:
            return redirect("ui:choose_org")

        try:
            self.account = services.verify_portal_access(request.membership)
        except Exception as exc:
            raise PermissionDenied(str(exc)) from exc

        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        sr = selectors.get_client_request(request.membership, pk)
        if sr is None:
            raise Http404("Service request not found.")

        history = selectors.client_request_history_for(sr)
        attachments = evidence_for(request.organization, sr)
        eligible_for_confirmation, ineligibility_reason = services.check_closure_confirmation_eligibility(
            request.membership, sr
        )
        from .models import PortalClosureConfirmation
        confirmation = getattr(sr, "closure_confirmation", None) or PortalClosureConfirmation.objects.filter(request=sr).first()

        wo = selectors.get_linked_work_order(sr)
        tech_name = None
        if wo and wo.status in ("DISPATCHED", "IN_PROGRESS", "COMPLETED", "CLOSED") and wo.assigned_to:
            tech_name = wo.assigned_to.user.full_name

        eligible_for_reopen, reopen_ineligibility_reason = services.check_reopen_eligibility(
            request.membership, sr
        )

        return render(
            request,
            "portal/request_detail.html",
            {
                "sr": sr,
                "account": self.account,
                "company_name": self.account.company_name or request.organization.name,
                "org": request.organization,
                "history": history,
                "attachments": attachments,
                "current_stage": selectors.get_status_stage(sr.status),
                "status_label": selectors.get_status_label(sr.status),
                "eligible_for_confirmation": eligible_for_confirmation,
                "ineligibility_reason": ineligibility_reason,
                "eligible_for_reopen": eligible_for_reopen,
                "reopen_ineligibility_reason": reopen_ineligibility_reason,
                "confirmation": confirmation,
                "work_order": wo,
                "technician_name": tech_name,
            },
        )


class RequestConfirmView(LoginRequiredMixin, View):
    """Client / Requester view to confirm satisfactory completion of a service request."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if request.tenant_error:
            return render(request, "errors/tenant.html", {"code": request.tenant_error}, status=403)

        if request.membership is None:
            return redirect("ui:choose_org")

        try:
            self.account = services.verify_portal_request_confirm_access(request.membership)
        except Exception as exc:
            raise PermissionDenied(str(exc)) from exc

        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk):
        rating = request.POST.get("rating")
        feedback = request.POST.get("feedback", "")
        try:
            confirmation = services.confirm_portal_request_closure(
                request.membership,
                pk,
                rating=rating,
                feedback=feedback,
                actor=request.user,
                request=request,
            )
            messages.success(
                request,
                f"Service request {confirmation.request.number} completion confirmed successfully. Thank you for your confirmation!",
            )
        except DomainError as exc:
            messages.error(request, exc.message)

        return redirect("portal:request_detail", pk=pk)


class RequestReopenView(LoginRequiredMixin, View):
    """Client / Requester view to reopen a resolved service request if the issue is unresolved."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if request.tenant_error:
            return render(request, "errors/tenant.html", {"code": request.tenant_error}, status=403)

        if request.membership is None:
            return redirect("ui:choose_org")

        try:
            self.account = services.verify_portal_access(request.membership)
        except Exception as exc:
            raise PermissionDenied(str(exc)) from exc

        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk):
        reason = request.POST.get("reason", "").strip()
        if len(reason) < 3:
            messages.error(request, "A reason is required to reopen the request (minimum 3 characters).")
            return redirect("portal:request_detail", pk=pk)

        try:
            sr = services.reopen_portal_service_request(
                request.membership,
                pk,
                reason=reason,
                actor=request.user,
                request=request,
            )
            messages.success(
                request,
                f"Service request {sr.number} has been reopened for rework. Our service team will review it.",
            )
        except DomainError as exc:
            messages.error(request, exc.message)

        return redirect("portal:request_detail", pk=pk)


class AccountsListView(TenantPermissionMixin, View):
    """Staff management view for organization portal accounts.

    Requires portal.manage permission (held by Service Manager / Owner).
    """

    required_permission = "portal.manage"

    def get(self, request):
        accounts = selectors.accounts_for_organization(request.organization)
        return render(
            request,
            "portal/accounts_list.html",
            {
                "accounts": accounts,
                "org": request.organization,
            },
        )


class AccountToggleView(TenantPermissionMixin, View):
    """Toggle enabled/disabled state of a portal account."""

    required_permission = "portal.manage"

    def post(self, request, pk):
        account = selectors.get_portal_account_by_id(request.organization, pk)
        if account is None:
            raise Http404("Portal account not found.")

        updated = services.toggle_portal_account(account, actor=request.user)
        state_label = "enabled" if updated.is_enabled else "disabled"
        messages.success(
            request,
            f"Portal account for {updated.membership.user.email} is now {state_label}.",
        )
        return redirect("portal:accounts_list")

