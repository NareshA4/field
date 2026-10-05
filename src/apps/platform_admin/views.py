"""Platform console (Super Admin). Platform admins hold NO tenant permissions: they manage organizations
and see platform-level audit, never an organization's operational data."""
import uuid

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View

from apps.audit import selectors as audit_selectors
from apps.audit.models import AuditLog
from apps.core.exceptions import DomainError
from apps.tenancy import services
from apps.tenancy.models import Membership, Organization


class PlatformAdminMixin:
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        if not request.user.is_platform_admin:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class OrganizationCreateForm(forms.Form):
    name = forms.CharField(max_length=150)
    slug = forms.CharField(max_length=60, required=False, help_text="Leave blank to derive from the name.")
    timezone = forms.CharField(max_length=64, initial="UTC")
    country = forms.CharField(max_length=2, required=False)
    owner_name = forms.CharField(max_length=150, label="Owner full name")
    owner_email = forms.EmailField(label="Owner email")


class SuspendForm(forms.Form):
    reason = forms.CharField(max_length=300, required=False)


class OrganizationListView(PlatformAdminMixin, View):
    def get(self, request):
        qs = Organization.objects.all()
        q = (request.GET.get("q") or "").strip()
        status = request.GET.get("status", "")
        if q:
            qs = qs.filter(name__icontains=q)
        if status in Organization.Status.values:
            qs = qs.filter(status=status)
        page = Paginator(qs, 20).get_page(request.GET.get("page"))
        return render(request, "platform_admin/organizations.html",
                      {"page": page, "q": q, "status": status, "statuses": Organization.Status.choices})


class OrganizationCreateView(PlatformAdminMixin, View):
    def get(self, request):
        return render(request, "platform_admin/organization_form.html", {"form": OrganizationCreateForm()})

    def post(self, request):
        form = OrganizationCreateForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                org, _ = services.create_organization(
                    name=d["name"], slug=d["slug"] or None, timezone_name=d["timezone"], country=d["country"],
                    owner_email=d["owner_email"], owner_name=d["owner_name"], actor=request.user, request=request,
                )
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(request, f"Organization created. An activation email was sent to {d['owner_email']}.")
                return redirect("platform_admin:organization_detail", pk=org.pk)
        return render(request, "platform_admin/organization_form.html", {"form": form})


class OrganizationDetailView(PlatformAdminMixin, View):
    def _org(self, pk):
        org = Organization.objects.filter(pk=pk).first()
        if org is None:
            raise Http404
        return org

    def get(self, request, pk):
        org = self._org(pk)
        owners = Membership.objects.unscoped().filter(
            organization=org, membership_roles__role__is_owner=True).select_related("user").distinct()
        counts = {
            "members": Membership.objects.unscoped().filter(organization=org).count(),
            "active": Membership.objects.unscoped().filter(organization=org, status="ACTIVE").count(),
        }
        recent = AuditLog.objects.for_organization(org)[:10]
        return render(request, "platform_admin/organization_detail.html", {
            "org": org, "owners": owners, "counts": counts, "recent": recent, "suspend_form": SuspendForm(),
        })

    def post(self, request, pk):
        org = self._org(pk)
        action = request.POST.get("action")
        try:
            if action == "suspend":
                services.set_organization_status(org, active=False, actor=request.user,
                                                 reason=request.POST.get("reason", ""), request=request)
                messages.success(request, "Organization suspended. Its users can no longer sign in to it.")
            elif action == "activate":
                services.set_organization_status(org, active=True, actor=request.user, request=request)
                messages.success(request, "Organization re-activated.")
            elif action == "resend_owner_invite":
                m = Membership.objects.unscoped().filter(
                    pk=request.POST.get("membership"), organization=org,
                    status=Membership.Status.INVITED, membership_roles__role__is_owner=True).first()
                if m is None:
                    raise Http404
                services.resend_invitation(m, actor=request.user, request=request)
                messages.success(request, "Invitation re-sent.")
            else:
                raise PermissionDenied
        except DomainError as exc:
            messages.error(request, exc.message)
        except (ValueError, TypeError):
            raise Http404 from None
        return redirect("platform_admin:organization_detail", pk=org.pk)


class PlatformAuditView(PlatformAdminMixin, View):
    def get(self, request):
        qs = audit_selectors.filter_logs(AuditLog.objects.select_related("actor", "organization"), request.GET)
        org = request.GET.get("organization", "")
        if org:
            try:
                qs = qs.filter(organization_id=uuid.UUID(org))
            except ValueError:
                qs = qs.none()
        page = Paginator(qs, 25).get_page(request.GET.get("page"))
        return render(request, "platform_admin/audit.html", {"page": page, "filters": request.GET})
