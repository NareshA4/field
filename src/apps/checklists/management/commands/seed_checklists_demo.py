"""Management command to seed comprehensive demo data for FON-M08 manual testing.

Seeds:
- Realistic Assets (Pumps, HVAC Chillers, Generators) across PUNE-1 and MUM-1
- Active Work Orders in IN_PROGRESS state
- Checklist Templates with all 4 question types (BOOLEAN, NUMERIC, SELECTION, TEXT)
- Completed inspections with:
  1. Open non-conformance findings (ready for supervisor resolution in UI)
  2. 100% compliant pass inspections
  3. Live In-Progress inspections
  4. Resolved findings with full audit notes
"""
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.tenant import tenant_context
from apps.tenancy.models import Organization, Membership
from apps.sites.models import Site, Zone
from apps.sites import services as site_services
from apps.assets.models import Asset, AssetCategory
from apps.assets import services as asset_services
from apps.workorders.models import WorkOrder
from apps.workorders import services as wo_services
from apps.checklists.models import ChecklistTemplate, ChecklistItem, Inspection, Finding, InspectionResponse
from apps.checklists import services as cl


class Command(BaseCommand):
    help = "Seeds comprehensive checklist templates, assets, work orders, and inspection runs for manual testing."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Clear existing checklists demo runs before seeding.")

    def handle(self, *args, **options):
        org = Organization.objects.filter(slug="demo-organization").first() or Organization.objects.first()
        if not org:
            self.stdout.write(self.style.ERROR("No organization found. Please run seed_dev_demo first."))
            return

        with tenant_context(org):
            self.stdout.write(f"Seeding demo data for organization: {org.name} ({org.slug})...")

            # Reset previous checklist runs if requested
            if options.get("reset"):
                Finding.objects.filter(organization=org).delete()
                InspectionResponse.objects.filter(organization=org).delete()
                Inspection.objects.filter(organization=org).delete()
                ChecklistItem.objects.filter(organization=org).delete()
                ChecklistTemplate.objects.filter(organization=org).delete()
                self.stdout.write(self.style.WARNING("Existing checklist templates & inspections wiped."))

            # Retrieve key user memberships
            owner_m = Membership.objects.filter(organization=org, membership_roles__role__is_owner=True).first()
            sup_m = Membership.objects.filter(organization=org, user__email="supervisor@demo.local").first() or owner_m
            tech_m = Membership.objects.filter(organization=org, user__email="technician@demo.local").first() or owner_m

            actor = owner_m.user

            # 1. Ensure Sites & Zones exist
            pune_site = Site.objects.filter(organization=org, code="PUNE-1").first()
            if not pune_site:
                pune_site = site_services.create_site(
                    org, actor=actor, code="PUNE-1", name="Pune Manufacturing Plant",
                    city="Pune", country="IN", timezone="Asia/Kolkata", address="Bhosari MIDC, Pune"
                )
            zone_pune = Zone.objects.filter(organization=org, site=pune_site, code="Z-PUMP").first()
            if not zone_pune:
                zone_pune = site_services.create_zone(pune_site, actor=actor, name="Heavy Machinery Zone", zone_type="BUILDING", code="Z-PUMP")

            mum_site = Site.objects.filter(organization=org, code="MUM-1").first()
            if not mum_site:
                mum_site = site_services.create_site(
                    org, actor=actor, code="MUM-1", name="Mumbai Logistics Depot",
                    city="Mumbai", country="IN", timezone="Asia/Kolkata", address="Andheri East, Mumbai"
                )
            zone_mum = Zone.objects.filter(organization=org, site=mum_site, code="Z-HVAC").first()
            if not zone_mum:
                zone_mum = site_services.create_zone(mum_site, actor=actor, name="Rooftop HVAC Bay", zone_type="BUILDING", code="Z-HVAC")

            # 2. Ensure Asset Categories exist
            cat_pump = AssetCategory.objects.filter(organization=org, name="Industrial Pumps").first()
            if not cat_pump:
                cat_pump = asset_services.create_category(org, name="Industrial Pumps", actor=actor, description="Heavy rotary centrifugal & slurry pumps")

            cat_hvac = AssetCategory.objects.filter(organization=org, name="HVAC & Cooling").first()
            if not cat_hvac:
                cat_hvac = asset_services.create_category(org, name="HVAC & Cooling", actor=actor, description="Chillers, condensers, AHUs, and air handlers")

            cat_gen = AssetCategory.objects.filter(organization=org, name="Power Generators").first()
            if not cat_gen:
                cat_gen = asset_services.create_category(org, name="Power Generators", actor=actor, description="Diesel and standby backup power generators")

            # 3. Ensure Realistic Assets exist
            asset_pump = Asset.objects.filter(organization=org, asset_tag="AST-PUMP-01").first()
            if not asset_pump:
                asset_pump = asset_services.create_asset(
                    org, actor=actor, asset_tag="AST-PUMP-01", name="Centrifugal Slurry Pump #2",
                    category=cat_pump, site=pune_site, zone=zone_pune, manufacturer="Kirloskar Brothers",
                    model="KPD-150/400", serial_number="KBL-2024-8842", description="Primary slurry circulation pump in chemical processing line"
                )

            asset_chiller = Asset.objects.filter(organization=org, asset_tag="AST-HVAC-01").first()
            if not asset_chiller:
                asset_chiller = asset_services.create_asset(
                    org, actor=actor, asset_tag="AST-HVAC-01", name="Chiller Compressor Unit #1",
                    category=cat_hvac, site=pune_site, zone=zone_pune, manufacturer="Daikin",
                    model="EWAD-TZ-B", serial_number="DKN-99214-CH", description="Main central plant water-cooled screw chiller"
                )

            asset_gen = Asset.objects.filter(organization=org, asset_tag="AST-GEN-01").first()
            if not asset_gen:
                asset_gen = asset_services.create_asset(
                    org, actor=actor, asset_tag="AST-GEN-01", name="Main Diesel Generator 500kVA",
                    category=cat_gen, site=pune_site, zone=zone_pune, manufacturer="Cummins",
                    model="C500D5e", serial_number="CUM-500K-771", description="Emergency standby diesel generator for plant operations"
                )

            asset_ahu = Asset.objects.filter(organization=org, asset_tag="AST-AHU-01").first()
            if not asset_ahu:
                asset_ahu = asset_services.create_asset(
                    org, actor=actor, asset_tag="AST-AHU-01", name="Air Handling Unit AHU-04",
                    category=cat_hvac, site=mum_site, zone=zone_mum, manufacturer="Blue Star",
                    model="BS-AHU-4500", serial_number="BLU-2023-AH44", description="Rooftop air handler supplying air to logistics bay"
                )

            self.stdout.write(self.style.SUCCESS("[OK] Assets verified."))

            # 4. Ensure Work Orders in IN_PROGRESS state exist
            now = timezone.now()
            t1_start = now + timedelta(days=1)
            t1_end = t1_start + timedelta(hours=3)

            t2_start = now + timedelta(days=2)
            t2_end = t2_start + timedelta(hours=3)

            t3_start = now + timedelta(days=3)
            t3_end = t3_start + timedelta(hours=3)

            wo_pump = WorkOrder.objects.filter(organization=org, asset=asset_pump, status="IN_PROGRESS").first()
            if not wo_pump:
                wo_pump = wo_services.create_work_order(
                    org, asset=asset_pump, actor=actor, title="PM: Quarterly Pump Bearing & Pressure Check",
                    work_type="PREVENTIVE", priority="HIGH"
                )
                wo_pump = wo_services.transition(wo_pump, action="plan", actor=actor, membership=owner_m, planned_start=t1_start, planned_end=t1_end)
                wo_pump = wo_services.transition(wo_pump, action="assign", actor=actor, membership=owner_m, technician=tech_m)
                wo_pump = wo_services.transition(wo_pump, action="dispatch", actor=actor, membership=owner_m)
                wo_pump = wo_services.transition(wo_pump, action="start", actor=tech_m.user, membership=tech_m)

            wo_chiller = WorkOrder.objects.filter(organization=org, asset=asset_chiller, status="IN_PROGRESS").first()
            if not wo_chiller:
                wo_chiller = wo_services.create_work_order(
                    org, asset=asset_chiller, actor=actor, title="Routine HVAC Operating Parameter Verification",
                    work_type="PREVENTIVE", priority="MEDIUM"
                )
                wo_chiller = wo_services.transition(wo_chiller, action="plan", actor=actor, membership=owner_m, planned_start=t2_start, planned_end=t2_end)
                wo_chiller = wo_services.transition(wo_chiller, action="assign", actor=actor, membership=owner_m, technician=tech_m)
                wo_chiller = wo_services.transition(wo_chiller, action="dispatch", actor=actor, membership=owner_m)
                wo_chiller = wo_services.transition(wo_chiller, action="start", actor=tech_m.user, membership=tech_m)

            wo_gen = WorkOrder.objects.filter(organization=org, asset=asset_gen, status="IN_PROGRESS").first()
            if not wo_gen:
                wo_gen = wo_services.create_work_order(
                    org, asset=asset_gen, actor=actor, title="Generator Standby Operational Readiness Inspection",
                    work_type="PREVENTIVE", priority="URGENT"
                )
                wo_gen = wo_services.transition(wo_gen, action="plan", actor=actor, membership=owner_m, planned_start=t3_start, planned_end=t3_end)
                wo_gen = wo_services.transition(wo_gen, action="assign", actor=actor, membership=owner_m, technician=tech_m)
                wo_gen = wo_services.transition(wo_gen, action="dispatch", actor=actor, membership=owner_m)
                wo_gen = wo_services.transition(wo_gen, action="start", actor=tech_m.user, membership=tech_m)

            self.stdout.write(self.style.SUCCESS("[OK] Work orders in IN_PROGRESS state verified."))

            # 5. Checklist Templates
            # Template 1: Pump Safety Checklist (Active, Mandatory gate)
            t_pump = ChecklistTemplate.objects.filter(organization=org, name="Pre-Operational Pump Safety Checklist").first()
            if not t_pump:
                t_pump = cl.create_template(
                    org, actor=actor, name="Pre-Operational Pump Safety Checklist",
                    description="Mandatory pre-run inspection covering casing integrity, pressure thresholds, and lubricant status.",
                    work_type="PREVENTIVE", is_required=True
                )
                cl.add_item(t_pump, actor=actor, prompt="Casing free of visible leaks and cracks", item_type="BOOLEAN", required=True)
                cl.add_item(t_pump, actor=actor, prompt="Discharge pressure", item_type="NUMERIC", min_value="2.0", max_value="6.5", unit="bar", required=True)
                cl.add_item(t_pump, actor=actor, prompt="Shaft seal condition", item_type="SELECTION", options=["Optimal", "Acceptable", "Worn", "Failed"], exception_options=["Failed"], required=True)
                cl.add_item(t_pump, actor=actor, prompt="Technician observations & notes", item_type="TEXT", required=False)
                t_pump = cl.activate_template(t_pump, actor=actor)

            # Template 2: HVAC Air Handler Inspection
            t_hvac = ChecklistTemplate.objects.filter(organization=org, name="HVAC Air Handler & Thermal Inspection").first()
            if not t_hvac:
                t_hvac = cl.create_template(
                    org, actor=actor, name="HVAC Air Handler & Thermal Inspection",
                    description="Quality verification checklist for AHU fan belts, supply air temperature, and filter differential.",
                    work_type="", is_required=False
                )
                cl.add_item(t_hvac, actor=actor, prompt="Fan belt tension and alignment verified", item_type="BOOLEAN", required=True)
                cl.add_item(t_hvac, actor=actor, prompt="Supply air temperature", item_type="NUMERIC", min_value="12.0", max_value="24.0", unit="degC", required=True)
                cl.add_item(t_hvac, actor=actor, prompt="Filter condition", item_type="SELECTION", options=["Clean", "Moderate", "Clogged"], exception_options=["Clogged"], required=True)
                cl.add_item(t_hvac, actor=actor, prompt="Evaporator coil condition & cleanliness", item_type="TEXT", required=False)
                t_hvac = cl.activate_template(t_hvac, actor=actor)

            # Template 3: Generator Oil & Battery Gate
            t_gen = ChecklistTemplate.objects.filter(organization=org, name="Generator Electrical & Oil Level Gate").first()
            if not t_gen:
                t_gen = cl.create_template(
                    org, actor=actor, name="Generator Electrical & Oil Level Gate",
                    description="Standby diesel generator readiness checklist covering fluid levels, starter battery, and emergency stop.",
                    work_type="PREVENTIVE", is_required=True
                )
                cl.add_item(t_gen, actor=actor, prompt="Engine oil level within marked dipstick range", item_type="BOOLEAN", required=True)
                cl.add_item(t_gen, actor=actor, prompt="Battery terminal voltage", item_type="NUMERIC", min_value="24.0", max_value="28.5", unit="V", required=True)
                cl.add_item(t_gen, actor=actor, prompt="Coolant level inspection", item_type="SELECTION", options=["Full", "Low", "Empty"], exception_options=["Empty"], required=True)
                t_gen = cl.activate_template(t_gen, actor=actor)

            # Template 4: Draft Template for UI Builder Testing
            t_draft = ChecklistTemplate.objects.filter(organization=org, name="Electrical Switchgear Overhaul (Draft)").first()
            if not t_draft:
                t_draft = cl.create_template(
                    org, actor=actor, name="Electrical Switchgear Overhaul (Draft)",
                    description="Draft checklist template to test adding, editing, reordering, and activating questions.",
                    work_type="CORRECTIVE", is_required=False
                )
                cl.add_item(t_draft, actor=actor, prompt="Lockout/Tagout (LOTO) procedure confirmed", item_type="BOOLEAN", required=True)
                cl.add_item(t_draft, actor=actor, prompt="Busbar contact resistance", item_type="NUMERIC", min_value="0.01", max_value="0.05", unit="mOhm", required=True)

            self.stdout.write(self.style.SUCCESS("[OK] Checklist templates verified."))

            # 6. Seed Sample Inspections, Responses, and Findings
            # Scenario A: Completed Inspection with HIGH Severity OPEN finding
            insp_pump = Inspection.objects.filter(organization=org, work_order=wo_pump).first()
            if not insp_pump:
                insp_pump = cl.start_inspection(org, template=t_pump, membership=tech_m, actor=tech_m.user, work_order=wo_pump)
                items = {i.prompt: str(i.pk) for i in t_pump.items.all()}
                # Save answers with Q2 being an exception (6.8 bar > max 6.5 bar)
                cl.save_responses(insp_pump, {
                    items["Casing free of visible leaks and cracks"]: True,
                    items["Discharge pressure"]: "6.8",  # Exception triggered!
                    items["Shaft seal condition"]: "Worn",
                    items["Technician observations & notes"]: "Abnormal pressure spikes observed during 15-minute trial run.",
                }, membership=tech_m, actor=tech_m.user)

                # Record finding for the exception
                q2_item = t_pump.items.filter(prompt="Discharge pressure").first()
                f1 = cl.add_finding(
                    insp_pump,
                    description="Discharge pressure exceeded safe operational threshold (6.8 bar recorded vs max limit 6.5 bar). Mechanical seal is subject to over-pressure risk.",
                    severity=Finding.Severity.HIGH,
                    item=q2_item,
                    membership=tech_m,
                    actor=tech_m.user,
                )
                # Complete the inspection
                cl.complete_inspection(insp_pump, membership=tech_m, actor=tech_m.user, summary="Quarterly checks completed. 1 critical exception recorded regarding discharge pressure.")
                self.stdout.write(self.style.SUCCESS("[OK] Inspection 1 (Completed with OPEN finding) created."))

            # Scenario B: Completed Clean Inspection (100% Pass)
            insp_chiller = Inspection.objects.filter(organization=org, work_order=wo_chiller).first()
            if not insp_chiller:
                insp_chiller = cl.start_inspection(org, template=t_hvac, membership=tech_m, actor=tech_m.user, work_order=wo_chiller)
                items = {i.prompt: str(i.pk) for i in t_hvac.items.all()}
                cl.save_responses(insp_chiller, {
                    items["Fan belt tension and alignment verified"]: True,
                    items["Supply air temperature"]: "15.8",  # Within 12.0 - 24.0 range
                    items["Filter condition"]: "Clean",
                    items["Evaporator coil condition & cleanliness"]: "Evaporator coil clean. Fins straight, no signs of frosting or blockage.",
                }, membership=tech_m, actor=tech_m.user)
                cl.complete_inspection(insp_chiller, membership=tech_m, actor=tech_m.user, summary="HVAC inspection completed cleanly with zero non-conformances.")
                self.stdout.write(self.style.SUCCESS("[OK] Inspection 2 (Completed Clean Pass) created."))

            # Scenario C: In-Progress Inspection
            insp_gen = Inspection.objects.filter(organization=org, work_order=wo_gen).first()
            if not insp_gen:
                insp_gen = cl.start_inspection(org, template=t_gen, membership=tech_m, actor=tech_m.user, work_order=wo_gen)
                items = {i.prompt: str(i.pk) for i in t_gen.items.all()}
                cl.save_responses(insp_gen, {
                    items["Engine oil level within marked dipstick range"]: True,
                }, membership=tech_m, actor=tech_m.user)
                self.stdout.write(self.style.SUCCESS("[OK] Inspection 3 (In-Progress) created."))

            # Scenario D: Standalone Inspection on Mumbai AHU with RESOLVED finding
            insp_ahu = Inspection.objects.filter(organization=org, asset=asset_ahu, work_order__isnull=True).first()
            if not insp_ahu:
                insp_ahu = cl.start_inspection(org, template=t_hvac, membership=owner_m, actor=actor, asset=asset_ahu)
                items = {i.prompt: str(i.pk) for i in t_hvac.items.all()}
                cl.save_responses(insp_ahu, {
                    items["Fan belt tension and alignment verified"]: False,  # Exception!
                    items["Supply air temperature"]: "21.5",
                    items["Filter condition"]: "Moderate",
                    items["Evaporator coil condition & cleanliness"]: "Dust accumulation on primary filter mesh.",
                }, membership=owner_m, actor=actor)

                q1_item = t_hvac.items.filter(prompt="Fan belt tension and alignment verified").first()
                f_ahu = cl.add_finding(
                    insp_ahu,
                    description="Excessive belt slack (35mm deflection) causing intermittent slippage at start-up.",
                    severity=Finding.Severity.MEDIUM,
                    item=q1_item,
                    membership=owner_m,
                    actor=actor,
                )
                cl.complete_inspection(insp_ahu, membership=owner_m, actor=actor, summary="Completed with belt tension exception.")

                # Resolve finding with notes
                cl.resolve_finding(
                    f_ahu,
                    notes="Adjusted motor slide rails to tension belt to 15mm deflection. Checked pulley alignment with laser guide.",
                    actor=sup_m.user,
                )
                self.stdout.write(self.style.SUCCESS("[OK] Inspection 4 (Completed with RESOLVED finding) created."))

            self.stdout.write(self.style.SUCCESS("\n========================================================"))
            self.stdout.write(self.style.SUCCESS("DEMO SEED COMPLETED SUCCESSFULLY FOR MANUAL TESTING!"))
            self.stdout.write(self.style.SUCCESS("========================================================"))
            self.stdout.write(f"- Templates: {ChecklistTemplate.objects.filter(organization=org).count()} (3 Active, 1 Draft)")
            self.stdout.write(f"- Field Inspections: {Inspection.objects.filter(organization=org).count()} (3 Completed, 1 In-Progress)")
            self.stdout.write(f"- Findings: {Finding.objects.filter(organization=org).count()} (1 OPEN, 1 RESOLVED)")
            self.stdout.write(f"- Work Orders: {WorkOrder.objects.filter(organization=org).count()} active")
            self.stdout.write("========================================================\n")
