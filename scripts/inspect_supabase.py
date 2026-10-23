import os
import sys

# Load .env
with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, "src")
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.prod"

import django
django.setup()

from django.contrib.auth import get_user_model
from apps.tenancy.models import Membership, Organization
from apps.rbac.models import MembershipRole, Role, RolePermission
from apps.rbac import services as rbac

User = get_user_model()

print("--- Querying Users in Database ---")
for u in User.objects.filter(email__icontains="asset.manager"):
    print(f"User: {u.email} (ID: {u.pk}, Full Name: {u.full_name})")
    print(f"Flags: is_superuser={u.is_superuser}, is_staff={u.is_staff}, is_platform_admin={getattr(u, 'is_platform_admin', False)}")

    m_list = Membership.objects.filter(user=u)
    print(f"Memberships ({m_list.count()}):")
    for m in m_list:
        print(f"  Org: {m.organization.name} (Slug: {m.organization.slug}, Status: {m.status})")
        roles = list(MembershipRole.objects.filter(membership=m).select_related("role"))
        for mr in roles:
            print(f"    Assigned Role: {mr.role.name} (Key: {mr.role.system_key}, is_owner={mr.role.is_owner}, site={mr.site})")
            perms = list(mr.role.role_permissions.values_list("permission__code", flat=True))
            print(f"      Role Permission count: {len(perms)}")
            if "organization.view" in perms:
                print("      *** Role has organization.view! ***")
            if "user.view" in perms:
                print("      *** Role has user.view! ***")
        
        has_org_view = rbac.has_permission(m, "organization.view")
        has_user_view = rbac.has_permission(m, "user.view")
        print(f"  Calculated perms: organization.view={has_org_view}, user.view={has_user_view}")

print("\n--- Querying Organization 'apex-industrial-technologies' ---")
org = Organization.objects.filter(slug="apex-industrial-technologies").first()
if org:
    print(f"Found Org: {org.name} (Slug: {org.slug})")
    all_members = Membership.objects.filter(organization=org).select_related("user")
    print(f"Total Members in Org: {all_members.count()}")
    for mem in all_members:
        mr_names = [mr.role.name for mr in MembershipRole.objects.filter(membership=mem).select_related("role")]
        print(f"  {mem.user.email} -> Roles: {mr_names}")
