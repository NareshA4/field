"""M05 demo data: incidents / breakdown reports on demo assets, with severity, service impact, downtime and photos.

    python manage.py seed_m05_demo [--org <organization-slug>]

Needs the M01 demo organization (``seed_dev_demo``). Goes through the real service layer (validation, history, audit),
so the records are the same as data entered in the UI. Never run by migrations. For LOCAL / test environments only:
refuses to run when DEBUG is off unless FIELDOPS_ALLOW_DEMO_SEED=true. Re-running is safe (records are matched by title).
"""
import base64
import os
from datetime import timedelta

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.assets import services as asset_services
from apps.assets.models import Asset, AssetCategory
from apps.core.tenant import tenant_context
from apps.incidents import services
from apps.incidents.models import ServiceRequest
from apps.sites.models import Site
from apps.tenancy.models import Membership, Organization

DEFAULT_ORG_NAME = "Demo Organization"
# 1x1 transparent PNG: a real, valid image so the upload validation runs for real
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAHnOcQAAAAABJRU5ErkJggg==")
ASSETS = [("M05-GEN-01", "Diesel generator 500 kVA", "Generator"), ("M05-PUMP-02", "Coolant pump", "Pump"),
          ("M05-HVAC-03", "Rooftop air handler", "HVAC")]


class Command(BaseCommand):
    help = "Seeds demo incidents (severity, service impact, downtime, photos) for the demo organization."

    def add_arguments(self, parser):
        parser.add_argument("--org", default="", help="Organization slug (default: the 'Demo Organization')")

    def handle(self, *args, org: str = "", **options):
        if not (settings.DEBUG or os.environ.get("FIELDOPS_ALLOW_DEMO_SEED") == "true"):
            raise CommandError("seed_m05_demo is for local development only (DEBUG is off). "
                               "Set FIELDOPS_ALLOW_DEMO_SEED=true to run it on a local stack.")
        qs = Organization.objects.filter(slug=org) if org else Organization.objects.filter(name=DEFAULT_ORG_NAME)
        organization = qs.first()
        if organization is None:
            raise CommandError("Organization not found. Run `python manage.py seed_dev_demo` (M01) first.")
        with tenant_context(organization):
            self._seed(organization)
        self.stdout.write(self.style.SUCCESS(f"M05 incident demo data ready for {organization.name}."))

    def _member(self, org, email):
        m = Membership.objects.filter(organization=org, user__email=email, status="ACTIVE").select_related(
            "user").first()
        if m is None:
            raise CommandError(f"Active member {email} not found. Run `seed_dev_demo` first.")
        return m

    def _seed(self, org):
        owner = self._member(org, "owner@demo.local")
        tech = self._member(org, "technician@demo.local")
        site = Site.objects.filter(code="PUNE-1").first()
        if site is None:
            raise CommandError("Demo site PUNE-1 not found. Run `seed_dev_demo` (M01) first.")
        assets = {}
        for tag, name, cat in ASSETS:
            asset = Asset.objects.filter(asset_tag=tag).first()
            if asset is None:
                category = AssetCategory.objects.filter(name=cat).first() or asset_services.create_category(
                    org, name=cat, actor=owner.user)
                asset = asset_services.create_asset(org, site=site, category=category, actor=owner.user,
                                                    asset_tag=tag, name=name)
            assets[tag] = asset
        now = timezone.now()

        def report(tag, title, **kw):
            sr = ServiceRequest.objects.filter(title=title).first()
            if sr is not None:
                return sr, False
            return services.create_request(org, asset=assets[tag], reporter=tech, actor=tech.user, title=title,
                                           **kw), True

        sr, new = report("M05-GEN-01", "Generator trips on load", severity="CRITICAL", service_impact="FULL_OUTAGE",
                         impact_notes="Plant on grid power only", description="Trips about 2 minutes after load.",
                         occurred_at=now - timedelta(hours=5), downtime_started_at=now - timedelta(hours=5))
        if new:
            services.add_evidence(sr, SimpleUploadedFile("generator-panel.png", PNG, "image/png"),
                                  description="Control panel fault code", actor=tech.user)
            services.transition(sr, action="triage", actor=owner.user)
            services.transition(sr, action="approve", actor=owner.user)
        sr, new = report("M05-PUMP-02", "Coolant pump leaking at seal", severity="HIGH", service_impact="DEGRADED",
                         occurred_at=now - timedelta(days=1))
        if new:
            services.set_downtime(sr, started_at=now - timedelta(days=1), ended_at=now - timedelta(hours=20),
                                  actor=owner.user)
            services.transition(sr, action="triage", actor=owner.user)
        report("M05-HVAC-03", "Air handler noisy bearing", severity="LOW", service_impact="NONE")
        sr, new = report("M05-HVAC-03", "Request: replace air filters", kind="SERVICE_REQUEST", severity="MEDIUM",
                         service_impact="NONE")
        if new:
            services.transition(sr, action="triage", actor=owner.user)
            services.transition(sr, action="reject", reason="Covered by the preventive maintenance plan",
                                actor=owner.user)
