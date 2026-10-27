from django.db import migrations


def provision_platform_admin(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Organization = apps.get_model("tenancy", "Organization")
    Membership = apps.get_model("tenancy", "Membership")
    Role = apps.get_model("rbac", "Role")
    MembershipRole = apps.get_model("rbac", "MembershipRole")
    from django.contrib.auth.hashers import make_password

    # 1. Provision Platform Super Admin user
    email = "platform.admin@fieldops.io"
    user = User.objects.filter(email__iexact=email).first()
    if not user:
        user = User(
            email=email,
            full_name="Platform Super Administrator",
            is_active=True,
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
            password=make_password("Password123!"),
        )
        user.save()
    else:
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.is_platform_admin = True
        user.password = make_password("Password123!")
        user.save()

    # 2. Also ensure admin@apexindustrial.example.com has platform admin privileges
    admin_user = User.objects.filter(email__iexact="admin@apexindustrial.example.com").first()
    if admin_user:
        admin_user.is_platform_admin = True
        admin_user.save()

    # 3. Associate with organization so Platform Admin can access tenant operations
    org = Organization.objects.filter(name="Apex Industrial Technologies Ltd").first()
    if not org:
        org = Organization.objects.filter(slug__icontains="apex").first()
    if not org:
        org = Organization.objects.first()

    if org:
        mem = Membership.objects.filter(organization=org, user=user).first()
        if not mem:
            mem = Membership(
                organization=org,
                user=user,
                job_title="Platform Super Administrator",
                status="ACTIVE",
            )
            mem.save()
        else:
            mem.status = "ACTIVE"
            mem.save()

        admin_role = Role.objects.filter(organization=org, system_key="admin").first() or Role.objects.filter(organization=org, system_key="owner").first()
        if admin_role and not MembershipRole.objects.filter(membership=mem, role=admin_role).exists():
            MembershipRole.objects.create(membership=mem, role=admin_role, site=None)


def reverse_func(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("tenancy", "0001_initial"),
        ("rbac", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(provision_platform_admin, reverse_code=reverse_func),
    ]
