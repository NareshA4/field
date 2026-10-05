"""Optional demo data for Phase 1 (sites, locations, calendars, categories, assets, hierarchy, meters).

    python manage.py seed_phase1_demo --org <organization-slug>

Goes through the real service layer (validation, history, audit) so the data is indistinguishable from data
entered in the UI. It is never run by migrations and never replaces working logic (HPE sample-data policy).
Re-running is safe: existing sites/assets are left untouched."""
from datetime import time

from django.core.management.base import BaseCommand, CommandError

from apps.assets import hierarchy
from apps.assets import services as asset_services
from apps.assets.models import Asset, AssetCategory
from apps.core.tenant import tenant_context
from apps.sites import services as site_services
from apps.sites.models import Site
from apps.tenancy.models import Membership, Organization


class Command(BaseCommand):
    help = "Seeds realistic simulated Phase 1 data (sites, locations, assets, hierarchy, meters) for one organization."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Organization slug")

    def handle(self, *args, org: str, **options):
        try:
            organization = Organization.objects.get(slug=org)
        except Organization.DoesNotExist as exc:
            raise CommandError(f"Unknown organization '{org}'.") from exc
        with tenant_context(organization):
            owner = (Membership.objects.filter(organization=organization, status="ACTIVE",
                                               membership_roles__role__is_owner=True).select_related("user").first())
            actor = owner.user if owner else None
            self._seed(organization, actor)
        self.stdout.write(self.style.SUCCESS(f"Phase 1 demo data ready for {organization.name}."))

    def _site(self, org, actor, code, name, city, tz):
        site = Site.objects.filter(code=code).first()
        if site:
            return site, False
        site = site_services.create_site(org, actor=actor, code=code, name=name, city=city, country="IN",
                                         timezone=tz, address=f"{name} Industrial Estate",
                                         contact_name="Plant Manager", contact_phone="+91 20 5550 0100")
        site_services.create_calendar(site, actor=actor, name="Day shift", working_days=[1, 2, 3, 4, 5, 6],
                                      start_time=time(8), end_time=time(18))
        site_services.add_contact(site, actor=actor, name="Site Manager", role_title="Site Manager", phone="+91 20 5550 0101")
        site_services.add_contact(site, actor=actor, name="Regional Head", role_title="Escalation", email="regional@example.test")
        return site, True

    def _seed(self, org, actor):
        cats = {n: AssetCategory.objects.filter(name=n).first() or asset_services.create_category(
            org, name=n, actor=actor) for n in ("Generator", "Pump", "Motor", "HVAC")}
        pune, created = self._site(org, actor, "PUNE-1", "Pune Plant", "Pune", "Asia/Kolkata")
        mumbai, _ = self._site(org, actor, "MUM-1", "Mumbai Depot", "Mumbai", "Asia/Kolkata")
        if not created:
            self.stdout.write("Demo sites already present; skipping the rest.")
            return
        block = site_services.create_zone(pune, actor=actor, name="Block A", zone_type="BUILDING", code="BLK-A")
        floor = site_services.create_zone(pune, actor=actor, name="Ground floor", parent=block)
        hall = site_services.create_zone(pune, actor=actor, name="Generator hall", parent=floor,
                                         zone_type="SERVICE_AREA")
        site_services.create_zone(mumbai, actor=actor, name="Warehouse", zone_type="BUILDING")

        def asset(tag, name, cat, site, zone=None, **kw):
            if Asset.objects.filter(asset_tag=tag).exists():
                return Asset.objects.get(asset_tag=tag)
            return asset_services.create_asset(org, site=site, zone=zone, category=cats[cat], actor=actor,
                                               asset_tag=tag, name=name, **kw)

        gen = asset("GEN-001", "Diesel generator 500 kVA", "Generator", pune, hall, manufacturer="Cummins",
                    model="C500D5", serial_number="CU-500-8841", warranty_ref="AMC-2026-14")
        engine = asset("GEN-001-ENG", "Engine assembly", "Generator", pune, hall, manufacturer="Cummins")
        alt = asset("GEN-001-ALT", "Alternator", "Motor", pune, hall)
        cool = asset("GEN-001-COOL", "Cooling system", "HVAC", pune, hall)
        pump = asset("GEN-001-PMP", "Coolant pump", "Pump", pune, hall, manufacturer="Grundfos", serial_number="GF-1182")
        filt = asset("GEN-001-FLT", "Oil filter", "Pump", pune, hall)
        hierarchy.add_component(gen, engine, relationship_type="ASSEMBLY", actor=actor)
        hierarchy.add_component(gen, alt, relationship_type="ASSEMBLY", actor=actor)
        hierarchy.add_component(gen, cool, relationship_type="ASSEMBLY", actor=actor)
        hierarchy.add_component(cool, pump, relationship_type="COMPONENT", actor=actor)
        hierarchy.add_component(engine, filt, relationship_type="REPLACEABLE_PART", quantity=2,
                                part_number="LF-3000", actor=actor)
        meter = asset_services.create_meter(gen, name="Run hours", unit="h", actor=actor)
        for value in (120, 250.5, 410):
            asset_services.record_reading(meter, value=value, actor=actor)
        asset_services.change_status(pump, action="start_maintenance", reason="Bearing replacement", actor=actor)
        asset("MUM-FORK-01", "Forklift", "Motor", mumbai, manufacturer="Toyota", serial_number="TY-7781")
