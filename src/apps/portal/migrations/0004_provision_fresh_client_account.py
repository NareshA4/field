from django.db import migrations


def provision_clients(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Organization = apps.get_model("tenancy", "Organization")
    Membership = apps.get_model("tenancy", "Membership")
    Role = apps.get_model("rbac", "Role")
    MembershipRole = apps.get_model("rbac", "MembershipRole")
    PortalAccount = apps.get_model("portal", "PortalAccount")
    PortalAssetGrant = apps.get_model("portal", "PortalAssetGrant")
    Asset = apps.get_model("assets", "Asset")
    from django.contrib.auth.hashers import make_password

    # Find organization (by name or slug or first active)
    org = Organization.objects.filter(name="Apex Industrial Technologies Ltd").first()
    if not org:
        org = Organization.objects.filter(slug__icontains="apex").first()
    if not org:
        org = Organization.objects.first()
    if not org:
        return

    role = Role.objects.filter(organization=org, system_key="client_requester").first()
    if not role:
        role = Role.objects.filter(system_key="client_requester").first()

    clients = [
        ("client.new@apexindustrial.com", "Sarah Connor", "Apex Client Solutions"),
        ("customer.portal@apexindustrial.com", "John Doe", "Apex Premier Client"),
    ]

    for email, full_name, company_name in clients:
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            user = User(
                email=email,
                full_name=full_name,
                is_active=True,
                password=make_password("ClientPass2026!"),
            )
            user.save()
        else:
            user.password = make_password("ClientPass2026!")
            user.is_active = True
            user.full_name = full_name
            user.save()

        mem = Membership.objects.filter(organization=org, user=user).first()
        if not mem:
            mem = Membership(
                organization=org,
                user=user,
                job_title="Client Representative",
                status="ACTIVE",
            )
            mem.save()
        else:
            mem.status = "ACTIVE"
            mem.save()

        if role:
            if not MembershipRole.objects.filter(membership=mem, role=role).exists():
                MembershipRole.objects.create(membership=mem, role=role, site=None)

        pa = PortalAccount.objects.filter(organization=org, membership=mem).first()
        if not pa:
            pa = PortalAccount(
                organization=org,
                membership=mem,
                company_name=company_name,
                is_enabled=True,
            )
            pa.save()
        else:
            pa.is_enabled = True
            pa.company_name = company_name
            pa.save()

        # Grant available assets
        assets = Asset.objects.filter(organization=org).exclude(status__in=["RETIRED", "DISPOSED"])
        for asset in assets:
            if not PortalAssetGrant.objects.filter(organization=org, portal_account=pa, asset=asset).exists():
                PortalAssetGrant.objects.create(
                    organization=org,
                    portal_account=pa,
                    asset=asset,
                )


def reverse_func(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0003_portalassetgrant"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'portal_portalaccount' AND column_name = 'phone_number'
                ) THEN
                    ALTER TABLE portal_portalaccount ALTER COLUMN phone_number DROP NOT NULL;
                END IF;
            END $$;
            """,
            reverse_sql="",
        ),
        migrations.RunPython(provision_clients, reverse_code=reverse_func),
    ]
