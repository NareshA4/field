from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views import View

from apps.core.exceptions import DomainError
from apps.rbac import services as rbac
from apps.ui.mixins import TenantPermissionMixin

from . import selectors, services
from .forms import InviteForm, MemberEditForm, OrganizationForm, RoleAssignmentForm
from .models import Membership

ORG_FIELDS = ["name", "legal_name", "timezone", "country", "contact_email", "contact_phone", "address"]


class OrganizationView(TenantPermissionMixin, View):
    required_permission = "organization.view"

    def get(self, request):
        form = OrganizationForm(initial={f: getattr(request.organization, f) for f in ORG_FIELDS})
        return render(request, "tenancy/organization.html", {"form": form, "can_edit": self._can_edit(request)})

    def post(self, request):
        if not self._can_edit(request):
            raise PermissionDenied
        form = OrganizationForm(request.POST)
        if form.is_valid():
            try:
                services.update_organization(request.organization, actor=request.user, request=request,
                                             **form.cleaned_data)
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(request, "Organization profile saved.")
                return redirect("tenancy:organization")
        return render(request, "tenancy/organization.html", {"form": form, "can_edit": True})

    @staticmethod
    def _can_edit(request):
        return rbac.has_permission(request.membership, "organization.update")


class MemberListView(TenantPermissionMixin, View):
    required_permission = "user.view"

    def get(self, request):
        org = request.organization
        qs = selectors.organization_members(org)
        status = request.GET.get("status", "")
        q = (request.GET.get("q") or "").strip()
        if status in Membership.Status.values:
            qs = qs.filter(status=status)
        if q:
            qs = qs.filter(Q(user__full_name__icontains=q) | Q(user__email__icontains=q))
        page = Paginator(qs.order_by("user__full_name"), 20).get_page(request.GET.get("page"))
        return render(request, "tenancy/members.html", {
            "page": page, "status": status, "q": q, "statuses": Membership.Status.choices,
            "can_invite": rbac.has_permission(request.membership, "user.invite"),
        })


class MemberInviteView(TenantPermissionMixin, View):
    required_permission = "user.invite"

    def get(self, request):
        return render(request, "tenancy/member_invite.html", {"form": InviteForm(org=request.organization)})

    def post(self, request):
        form = InviteForm(request.POST, org=request.organization)
        if form.is_valid():
            try:
                m = services.invite_member(
                    request.organization, email=form.cleaned_data["email"],
                    full_name=form.cleaned_data["full_name"],
                    role_ids=[r.pk for r in form.cleaned_data["roles"]],
                    site_ids=[x.pk for x in form.cleaned_data["sites"]] or None,
                    actor=request.user, request=request,
                )
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(request, f"Invitation sent to {m.user.email}.")
                return redirect("tenancy:member_detail", pk=m.pk)
        return render(request, "tenancy/member_invite.html", {"form": form})


class MemberDetailView(TenantPermissionMixin, View):
    required_permission = "user.view"

    def _get(self, request, pk):
        from django.http import Http404

        from apps.core.exceptions import NotFound

        try:
            return services.get_membership(request.organization, pk)
        except NotFound as exc:  # not in this organization -> 404 (never reveals other tenants' records)
            raise Http404 from exc

    def _ctx(self, request, membership, form=None, assign_form=None):
        form = form or MemberEditForm(
            initial={"full_name": membership.user.full_name, "job_title": membership.job_title},
        )
        m = request.membership
        return {
            "member": membership, "form": form,
            "assignments": list(membership.membership_roles.select_related("role", "site")),
            "assign_form": assign_form or RoleAssignmentForm(org=request.organization),
            "can_update": rbac.has_permission(m, "user.update"),
            "can_deactivate": rbac.has_permission(m, "user.deactivate") and membership.user_id != request.user.pk,
            "can_invite": rbac.has_permission(m, "user.invite"),
        }

    def get(self, request, pk):
        membership = self._get(request, pk)
        return render(request, "tenancy/member_detail.html", self._ctx(request, membership))

    def post(self, request, pk):
        membership = self._get(request, pk)
        action = request.POST.get("action")
        needed = {"update": "user.update", "deactivate": "user.deactivate", "reactivate": "user.deactivate",
                  "resend": "user.invite", "assign_role": "user.update", "remove_role": "user.update"}.get(action)
        if needed is None or not rbac.has_permission(request.membership, needed):
            raise PermissionDenied
        try:
            if action == "update":
                form = MemberEditForm(request.POST)
                if not form.is_valid():
                    return render(request, "tenancy/member_detail.html", self._ctx(request, membership, form))
                services.update_member(
                    membership, actor=request.user, request=request,
                    full_name=form.cleaned_data["full_name"], job_title=form.cleaned_data["job_title"],
                )
                messages.success(request, "Member updated.")
            elif action == "assign_role":
                aform = RoleAssignmentForm(request.POST, org=request.organization)
                if not aform.is_valid():
                    return render(request, "tenancy/member_detail.html",
                                  self._ctx(request, membership, assign_form=aform))
                site = aform.cleaned_data["site"]
                services.add_role_assignment(
                    membership, role_id=aform.cleaned_data["role"].pk, site_id=site.pk if site else None,
                    actor=request.user, request=request)
                messages.success(request, "Role assigned.")
            elif action == "remove_role":
                services.remove_role_assignment(membership, assignment_id=request.POST.get("assignment_id"),
                                                actor=request.user, request=request)
                messages.success(request, "Role assignment removed.")
            elif action in ("deactivate", "reactivate"):
                services.set_member_active(membership, active=action == "reactivate", actor=request.user,
                                           request=request)
                messages.success(request, f"Member {action}d.")
            elif action == "resend":
                services.resend_invitation(membership, actor=request.user, request=request)
                messages.success(request, "Invitation re-sent.")
        except DomainError as exc:
            messages.error(request, exc.message)
        return redirect("tenancy:member_detail", pk=membership.pk)
