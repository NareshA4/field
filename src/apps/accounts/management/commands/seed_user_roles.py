from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.tenancy.models import Organization, Membership
from apps.rbac.models import Role, MembershipRole
from apps.rbac.services import seed_system_roles
from apps.portal.models import PortalAccount


class Command(BaseCommand):
    help = "Seeds standardized user credentials and role memberships for all system personas."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="FieldOps@2026!",
            help="Default password for all seeded users (default: FieldOps@2026!)",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        password = options["password"]
        User = get_user_model()

        self.stdout.write(self.style.NOTICE("Initializing system roles & user credentials..."))

        # 1. Organization Setup
        org, _ = Organization.objects.get_or_create(
            code="APEX-IND",
            defaults={
                "name": "Apex Industrial Technologies Ltd",
                "slug": "apex-industrial-technologies",
                "contact_email": "operations@apexindustrial.example.com",
                "contact_phone": "+94 11 289 9000",
            },
        )
        self.stdout.write(self.style.SUCCESS(f"[OK] Organization: {org.name} ({org.code})"))

        # 2. Seed System Roles for the Organization
        roles_dict = seed_system_roles(org)
        self.stdout.write(self.style.SUCCESS(f"[OK] Seeded {len(roles_dict)} system roles"))

        # 3. Define Standard User Personas
        personas = [
            {
                "email": "owner@apexindustrial.example.com",
                "full_name": "Dinuka Wijesinghe",
                "job_title": "Managing Director / Owner",
                "role_key": "owner",
                "is_platform_admin": False,
                "is_staff": False,
            },
            {
                "email": "admin@apexindustrial.example.com",
                "full_name": "Tharindu Alwis",
                "job_title": "IT & Systems Administrator",
                "role_key": "admin",
                "is_platform_admin": False,
                "is_staff": False,
            },
            {
                "email": "ops.manager@apexindustrial.example.com",
                "full_name": "Anura Senanayake",
                "job_title": "Head of Operations",
                "role_key": "operations_manager",
                "is_platform_admin": False,
                "is_staff": False,
            },
            {
                "email": "asset.manager@apexindustrial.example.com",
                "full_name": "Ruwan Jayasinghe",
                "job_title": "Principal Asset Manager",
                "role_key": "asset_manager",
                "is_platform_admin": False,
                "is_staff": False,
            },
            {
                "email": "planner@apexindustrial.example.com",
                "full_name": "Nimali Fernando",
                "job_title": "Senior Maintenance Planner",
                "role_key": "maintenance_planner",
                "is_platform_admin": False,
                "is_staff": False,
            },
            {
                "email": "supervisor@apexindustrial.example.com",
                "full_name": "Sunil Bandara",
                "job_title": "Maintenance Supervisor",
                "role_key": "supervisor",
                "is_platform_admin": False,
                "is_staff": False,
            },
            {
                "email": "technician@apexindustrial.example.com",
                "full_name": "Kasun Perera",
                "job_title": "Lead Field Technician",
                "role_key": "technician",
                "is_platform_admin": False,
                "is_staff": False,
            },
            {
                "email": "stores.manager@apexindustrial.example.com",
                "full_name": "Chaminda Silva",
                "job_title": "Inventory & Stores Manager",
                "role_key": "stores_manager",
                "is_platform_admin": False,
                "is_staff": False,
            },
            {
                "email": "service.manager@apexindustrial.example.com",
                "full_name": "Pradeep Mendis",
                "job_title": "Service & SLA Manager",
                "role_key": "service_manager",
                "is_platform_admin": False,
                "is_staff": False,
            },
            {
                "email": "auditor@apexindustrial.example.com",
                "full_name": "Shani De Silva",
                "job_title": "Compliance & Audit Officer",
                "role_key": "auditor",
                "is_platform_admin": False,
                "is_staff": False,
            },
            {
                "email": "client.requester@acmecorp.example.com",
                "full_name": "Raveen Fernando",
                "job_title": "Facility Manager (Acme Corp)",
                "role_key": "client_requester",
                "is_platform_admin": False,
                "is_staff": False,
                "is_portal_client": True,
            },
        ]

        # 4. Create Persona Users, Memberships & Assign Roles
        created_users = []
        for p in personas:
            email = p["email"]
            user = User.objects.filter(email__iexact=email).first()
            if not user:
                user = User.objects.create_user(
                    email=email,
                    password=password,
                    full_name=p["full_name"],
                    is_active=True,
                )
            else:
                user.set_password(password)
                user.full_name = p["full_name"]
                user.is_active = True
                user.save()

            # Membership in organization
            membership, _ = Membership.objects.unscoped().get_or_create(
                organization=org,
                user=user,
                defaults={"job_title": p["job_title"], "status": "ACTIVE"}
            )
            membership.status = "ACTIVE"
            membership.job_title = p["job_title"]
            membership.save()

            # Assign Role
            role = Role.objects.unscoped().filter(organization=org, system_key=p["role_key"]).first()
            if role:
                MembershipRole.objects.get_or_create(
                    membership=membership,
                    role=role,
                    defaults={"site": None}
                )

            # If portal client, setup PortalAccount
            if p.get("is_portal_client"):
                PortalAccount.objects.unscoped().get_or_create(
                    organization=org,
                    membership=membership,
                    defaults={"company_name": "Acme Corporation", "is_enabled": True}
                )

            created_users.append({
                "role": role.name if role else p["role_key"],
                "role_key": p["role_key"],
                "email": email,
                "name": p["full_name"],
            })

        self.stdout.write(self.style.SUCCESS("\n[SUCCESS] Successfully seeded all user credentials and roles!"))
