"""Creates credentials for Asset Manager, Service Manager, Maintenance Planner, and Auditor.

Can be run via:
    python manage.py shell < scripts/create_role_credentials.py
or:
    docker compose exec web python manage.py shell -c "exec(open('scripts/create_role_credentials.py').read())"
"""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
if not django.apps.apps.ready:
    django.setup()

from django.contrib.auth import get_user_model
from apps.accounts import services as accounts
from apps.core.tenant import tenant_context
from apps.rbac import services as rbac
from apps.tenancy import services as tenancy
from apps.tenancy.models import Membership, Organization

PASSWORD = os.environ.get("FIELDOPS_DEMO_PASSWORD", "FieldOps-Dev-2026!")
User = get_user_model()

org = Organization.objects.filter(name="Demo Organization").first() or Organization.objects.first()
if not org:
    print("Error: No organization found. Please run seed_dev_demo first.")
    exit(1)

owner = User.objects.filter(email="owner@demo.local").first()
if not owner:
    owner = org.memberships.filter(membership_roles__role__is_owner=True).first().user

NEW_USERS = [
    {
        "email": "asset_manager@demo.local",
        "name": "Demo Asset Manager",
        "role_key": "asset_manager",
        "title": "Asset Manager",
    },
    {
        "email": "service_manager@demo.local",
        "name": "Demo Service Manager",
        "role_key": "service_manager",
        "title": "Service Manager",
    },
    {
        "email": "maintenance_planner@demo.local",
        "name": "Demo Maintenance Planner",
        "role_key": "maintenance_planner",
        "title": "Maintenance Planner",
    },
    {
        "email": "auditor@demo.local",
        "name": "Demo Auditor",
        "role_key": "auditor",
        "title": "Auditor",
    },
]

print(f"Organization: {org.name} ({org.slug})")
print(f"Assigning roles & credentials (Password: {PASSWORD}):\n" + "-" * 60)

with tenant_context(org):
    for u_info in NEW_USERS:
        email = u_info["email"]
        name = u_info["name"]
        role_key = u_info["role_key"]
        title = u_info["title"]

        role = rbac.system_role_by_key(org, role_key)
        user = User.objects.filter(email=email).first()

        if not user:
            tenancy.invite_member(org, email=email, full_name=name, role_ids=[role.pk], actor=owner)
            user = User.objects.get(email=email)
        else:
            # Ensure membership exists and has the role
            membership = Membership.objects.unscoped().filter(organization=org, user=user).first()
            if not membership:
                membership = Membership.objects.create(organization=org, user=user, status=Membership.Status.ACTIVE)
            if not membership.membership_roles.filter(role=role).exists():
                membership.membership_roles.create(role=role)

        # Set/reset usable password & activate pending memberships
        user.set_password(PASSWORD)
        user.is_active = True
        user.save()
        accounts.activate_pending_memberships(user)

        print(f"[{title}]")
        print(f"  Email:    {email}")
        print(f"  Password: {PASSWORD}")
        print(f"  Role Key: {role_key} ({role.name})")
        print()

print("-" * 60)
print("All credentials created and active successfully.")
