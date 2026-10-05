"""Local development data: a platform admin, one demo organization, role-holder users and sample Sites & Locations.

    python manage.py seed_dev_demo

Goes through the real service layer (validation, audit), so the data is the same as data entered in the UI. For LOCAL
development only: refuses to run when DEBUG is off unless FIELDOPS_ALLOW_DEMO_SEED=true (local docker stack). All users share the local-only password below (override with the
environment variable FIELDOPS_DEMO_PASSWORD). Re-running is safe; existing records are left untouched."""
import os
from datetime import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.accounts import services as accounts
from apps.core.tenant import tenant_context
from apps.rbac import services as rbac
from apps.sites import services as site_services
from apps.sites.models import Site
from apps.tenancy import services as tenancy
from apps.tenancy.models import Membership, Organization

DEFAULT_PASSWORD = "FieldOps-Dev-2026!"
ORG_NAME = "Demo Organization"
PLATFORM_ADMIN = ("platform@fieldops.local", "Platform Admin")
OWNER = ("owner@demo.local", "Demo Owner")
ROLE_USERS = [
    ("admin@demo.local", "Demo Admin", "admin"),
    ("supervisor@demo.local", "Demo Supervisor", "supervisor"),
    ("technician@demo.local", "Demo Technician", "technician"),
]


class Command(BaseCommand):
    help = "Creates local demo data: platform admin, demo organization, role users and sample sites."

    def handle(self, *args, **options):
        if not (settings.DEBUG or os.environ.get("FIELDOPS_ALLOW_DEMO_SEED") == "true"):
            raise CommandError("seed_dev_demo is for local development only (DEBUG is off). "
                               "For the local docker stack set FIELDOPS_ALLOW_DEMO_SEED=true.")
        from config.celery import app as celery_app

        celery_app.conf.task_always_eager = True  # invitation emails run inline; no Redis needed for seeding
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        password = os.environ.get("FIELDOPS_DEMO_PASSWORD", DEFAULT_PASSWORD)
        User = get_user_model()

        admin = User.objects.filter(email=PLATFORM_ADMIN[0]).first()
        if admin is None:
            admin = User.objects.create_user(email=PLATFORM_ADMIN[0], password=password,
                                             full_name=PLATFORM_ADMIN[1], is_platform_admin=True)

        org = Organization.objects.filter(name=ORG_NAME).first()
        if org is None:
            org, _ = tenancy.create_organization(name=ORG_NAME, owner_email=OWNER[0], owner_name=OWNER[1],
                                                 actor=admin, timezone_name="Asia/Kolkata", country="IN")
        owner = User.objects.get(email=OWNER[0])
        self._activate(owner, password)

        with tenant_context(org):
            for email, name, key in ROLE_USERS:
                if not Membership.objects.filter(organization=org, user__email=email).exists():
                    role = rbac.system_role_by_key(org, key)
                    tenancy.invite_member(org, email=email, full_name=name, role_ids=[role.pk], actor=owner)
                self._activate(User.objects.get(email=email), password)
            self._seed_sites(org, owner)

        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write("Logins (all use the local demo password):")
        for email in [PLATFORM_ADMIN[0], OWNER[0], *[r[0] for r in ROLE_USERS]]:
            self.stdout.write(f"  {email}")

    @staticmethod
    def _activate(user, password):
        if not user.has_usable_password():
            user.set_password(password)
            user.save()
        accounts.activate_pending_memberships(user)

    def _site(self, org, actor, code, name, city):
        site = Site.objects.filter(code=code).first()
        if site:
            return site, False
        site = site_services.create_site(org, actor=actor, code=code, name=name, city=city, country="IN",
                                         timezone="Asia/Kolkata", address=f"{name} Industrial Estate",
                                         contact_name="Plant Manager", contact_phone="+91 20 5550 0100")
        site_services.create_calendar(site, actor=actor, name="Day shift", working_days=[1, 2, 3, 4, 5, 6],
                                      start_time=time(8), end_time=time(18))
        site_services.add_contact(site, actor=actor, name="Site Manager", role_title="Site Manager",
                                  phone="+91 20 5550 0101")
        site_services.add_contact(site, actor=actor, name="Regional Head", role_title="Escalation",
                                  email="regional@example.test")
        return site, True

    def _seed_sites(self, org, actor):
        pune, created = self._site(org, actor, "PUNE-1", "Pune Plant", "Pune")
        mumbai, _ = self._site(org, actor, "MUM-1", "Mumbai Depot", "Mumbai")
        if not created:
            return
        block = site_services.create_zone(pune, actor=actor, name="Block A", zone_type="BUILDING", code="BLK-A")
        floor = site_services.create_zone(pune, actor=actor, name="Ground floor", parent=block)
        site_services.create_zone(pune, actor=actor, name="Generator hall", parent=floor, zone_type="SERVICE_AREA")
        site_services.create_zone(mumbai, actor=actor, name="Warehouse", zone_type="BUILDING")
