import uuid
from itertools import groupby

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View

from apps.core.exceptions import DomainError
from apps.tenancy.forms import RoleForm
from apps.ui.mixins import TenantPermissionMixin

from . import catalog, services
from .models import Role


def _get_role(request, pk) -> Role:
    try:
        return Role.objects.for_organization(request.organization).get(pk=uuid.UUID(str(pk)))
    except (Role.DoesNotExist, ValueError) as exc:
        raise Http404 from exc


def _matrix(selected: set[str]):
    rows = []
    for module, defs in groupby(catalog.all_defs(), key=lambda d: d.module):
        rows.append({"module": module, "perms": [{"code": d.code, "description": d.description,
                                                   "checked": d.code in selected} for d in defs]})
    return rows


class RoleListView(TenantPermissionMixin, View):
    required_permission = "role.view"

    def get(self, request):
        roles = Role.objects.for_organization(request.organization).prefetch_related("role_permissions")
        return render(request, "rbac/roles.html", {
            "roles": roles, "can_manage": services.has_permission(request.membership, "role.manage"),
        })


class RoleCreateView(TenantPermissionMixin, View):
    required_permission = "role.manage"

    def get(self, request):
        return render(request, "rbac/role_form.html", {"form": RoleForm(), "matrix": _matrix(set()), "role": None})

    def post(self, request):
        form = RoleForm(request.POST)
        codes = request.POST.getlist("permissions")
        if form.is_valid():
            try:
                role = services.create_role(request.organization, name=form.cleaned_data["name"],
                                            description=form.cleaned_data["description"], permission_codes=codes,
                                            actor=request.user, request=request)
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(request, f"Role '{role.name}' created.")
                return redirect("rbac:role_detail", pk=role.pk)
        return render(request, "rbac/role_form.html", {"form": form, "matrix": _matrix(set(codes)), "role": None})


class RoleDetailView(TenantPermissionMixin, View):
    required_permission = "role.view"

    def get(self, request, pk):
        role = _get_role(request, pk)
        selected = set(role.role_permissions.values_list("permission__code", flat=True))
        form = RoleForm(initial={"name": role.name, "description": role.description})
        return render(request, "rbac/role_form.html", {
            "form": form, "matrix": _matrix(selected), "role": role,
            "can_manage": services.has_permission(request.membership, "role.manage"),
            "member_count": role.membership_roles.count(),
        })

    def post(self, request, pk):
        if not services.has_permission(request.membership, "role.manage"):
            raise PermissionDenied
        role = _get_role(request, pk)
        try:
            if request.POST.get("action") == "delete":
                services.delete_role(role, actor=request.user, request=request)
                messages.success(request, "Role deleted.")
                return redirect("rbac:roles")
            form = RoleForm(request.POST)
            if form.is_valid():
                services.update_role(role, name=form.cleaned_data["name"],
                                     description=form.cleaned_data["description"],
                                     permission_codes=request.POST.getlist("permissions"),
                                     actor=request.user, request=request)
                messages.success(request, "Role saved.")
        except DomainError as exc:
            messages.error(request, exc.message)
        return redirect("rbac:role_detail", pk=role.pk)
