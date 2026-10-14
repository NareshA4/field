from django import forms

from apps.rbac.models import Role
from apps.ui.forms import BootstrapFormMixin


class OrganizationForm(forms.Form):
    name = forms.CharField(max_length=150)
    legal_name = forms.CharField(max_length=200, required=False)
    timezone = forms.CharField(max_length=64)
    country = forms.CharField(max_length=2, required=False, help_text="ISO country code, e.g. IN")
    contact_email = forms.EmailField(required=False)
    contact_phone = forms.CharField(max_length=32, required=False)
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)


def _role_field(org, membership=None):
    from apps.rbac import services as rbac

    qs = Role.objects.for_organization(org).order_by("name")
    if membership is not None:
        if not rbac.has_role_assignment_permission(membership):
            return forms.ModelMultipleChoiceField(
                queryset=Role.objects.none(),
                widget=forms.CheckboxSelectMultiple, label="Roles",
            )
        if not rbac.is_owner(membership):
            qs = qs.exclude(is_owner=True)
            if not rbac.has_permission(membership, "role.manage"):
                actor_perms = set(rbac.membership_permissions(membership))
                for perms in rbac.site_permissions(membership).values():
                    actor_perms.update(perms)
                allowed_ids = [
                    r.pk for r in qs.prefetch_related("role_permissions__permission")
                    if set(r.role_permissions.values_list("permission__code", flat=True)).issubset(actor_perms)
                ]
                qs = qs.filter(pk__in=allowed_ids)
    return forms.ModelMultipleChoiceField(
        queryset=qs,
        widget=forms.CheckboxSelectMultiple, label="Roles",
    )


def _site_field(org, membership=None, *, required=False):
    from apps.rbac import services as rbac
    from apps.sites.models import Site

    qs = Site.objects.for_organization(org).order_by("code")
    if membership is not None:
        if not rbac.has_role_assignment_permission(membership):
            return forms.ModelMultipleChoiceField(
                queryset=Site.objects.none(), required=True,
                widget=forms.CheckboxSelectMultiple, label="Limit to sites",
            )
        scope = rbac.authorized_sites_for_actor(membership)
        if not scope.all_sites:
            qs = qs.filter(pk__in=scope.site_ids)
            required = True
    return forms.ModelMultipleChoiceField(
        queryset=qs, required=required,
        widget=forms.CheckboxSelectMultiple, label="Limit to sites",
        help_text="Leave empty for organization-wide access. Selected sites limit the chosen roles to those sites.",
    )


class InviteForm(forms.Form):
    email = forms.EmailField()
    full_name = forms.CharField(max_length=150, required=False, help_text="Required for new accounts.")

    def __init__(self, *args, org, membership=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["roles"] = _role_field(org, membership=membership)
        self.fields["sites"] = _site_field(org, membership=membership)


class MemberEditForm(forms.Form):
    full_name = forms.CharField(max_length=150)
    job_title = forms.CharField(max_length=100, required=False)


class RoleAssignmentForm(BootstrapFormMixin, forms.Form):
    role = forms.ModelChoiceField(queryset=Role.objects.none())
    site = forms.ModelChoiceField(queryset=None, required=False, empty_label="All sites (organization-wide)")

    def __init__(self, *args, org, membership=None, **kwargs):
        from apps.sites.models import Site

        super().__init__(*args, **kwargs)
        self.org = org
        self.membership = membership

        roles_qs = Role.objects.for_organization(org).order_by("name")
        sites_qs = Site.objects.for_organization(org).order_by("code")

        if membership is not None:
            from apps.rbac import services as rbac

            if not rbac.has_role_assignment_permission(membership):
                self.fields["site"].queryset = Site.objects.none()
                self.fields["role"].queryset = Role.objects.none()
                return

            scope = rbac.authorized_sites_for_actor(membership)
            if scope.all_sites:
                self.fields["site"].queryset = sites_qs
                self.fields["site"].required = False
                self.fields["site"].empty_label = "All sites (organization-wide)"
            else:
                self.fields["site"].queryset = sites_qs.filter(pk__in=scope.site_ids)
                self.fields["site"].required = True
                self.fields["site"].empty_label = "Select a site"

            # Filter assignable roles according to delegation rules
            if not rbac.is_owner(membership):
                roles_qs = roles_qs.exclude(is_owner=True)
                if not rbac.has_permission(membership, "role.manage"):
                    actor_perms = set(rbac.membership_permissions(membership))
                    for perms in rbac.site_permissions(membership).values():
                        actor_perms.update(perms)
                    allowed_role_ids = []
                    for r in roles_qs.prefetch_related("role_permissions__permission"):
                        r_codes = set(r.role_permissions.values_list("permission__code", flat=True))
                        scopable_codes = {c for c in r_codes if c in rbac.catalog.site_scoped_codes()}
                        if r_codes.issubset(actor_perms) or (not scope.all_sites and scopable_codes.issubset(actor_perms)):
                            allowed_role_ids.append(r.pk)
                    roles_qs = roles_qs.filter(pk__in=allowed_role_ids)
        else:
            self.fields["site"].queryset = sites_qs

        self.fields["role"].queryset = roles_qs

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get("role")
        site = cleaned_data.get("site")

        if self.membership is not None:
            from apps.rbac import services as rbac

            if not rbac.has_role_assignment_permission(self.membership):
                self.add_error(None, "You do not have permission to assign roles.")
                return cleaned_data

            scope = rbac.authorized_sites_for_actor(self.membership)
            if site is None:
                if not scope.all_sites:
                    self.add_error("site", "Organization-wide role assignment requires full site authority.")
            else:
                if not scope.allows(site):
                    self.add_error("site", "You are not authorized to assign roles for this site.")

            if role:
                if role.is_owner and not rbac.is_owner(self.membership):
                    self.add_error("role", "Only an Owner can grant the Owner role.")
                elif not rbac.is_owner(self.membership) and not rbac.has_permission(self.membership, "role.manage"):
                    actor_perms = set(rbac.membership_permissions(self.membership))
                    if site:
                        actor_perms |= set(rbac.site_permissions(self.membership).get(site.pk, frozenset()))
                        role_codes = {
                            c for c in role.role_permissions.values_list("permission__code", flat=True)
                            if c in rbac.catalog.site_scoped_codes()
                        }
                    else:
                        role_codes = set(role.role_permissions.values_list("permission__code", flat=True))
                    if role_codes - actor_perms:
                        self.add_error("role", "Cannot assign roles with permissions beyond your own authority.")

        return cleaned_data


class RoleForm(forms.Form):
    name = forms.CharField(max_length=80)
    description = forms.CharField(max_length=300, required=False)
