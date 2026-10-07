"""Explicit local-only seed data for Module 6 Work Orders.

Run ``python manage.py seed_workorders`` after ``seed_dev_demo``. This command is
never invoked by a page, API request, migration, or application startup. It
creates deterministic records through the module services so workflow history,
audit events, assignments, notes, labor, evidence, and closure decisions are real.
"""
import json
import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.files import services as file_services
from apps.assets import services as asset_services
from apps.assets.models import Asset, AssetCategory
from apps.incidents import services as incident_services
from apps.incidents.models import ServiceRequest
from apps.incidents.workflow import APPROVED, TRIAGED
from apps.rbac import services as rbac
from apps.sites.models import Site
from apps.tenancy.models import Membership, Organization
from apps.workorders import services
from apps.workorders.models import WorkOrder, WorkOrderEvent


class Command(BaseCommand):
    help = "Create idempotent Module 6 work-order examples for local development/testing only."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=9,
                            help="Number of examples to create (9-100; defaults to one per workflow state).")

    @transaction.atomic
    def handle(self, *args, **options):
        if not (settings.DEBUG or os.environ.get("FIELDOPS_ALLOW_DEMO_SEED", "").lower() == "true"):
            raise CommandError("Development seed blocked. Use a DEBUG environment or explicitly set "
                               "FIELDOPS_ALLOW_DEMO_SEED=true for a local stack.")
        count = options["count"]
        if count < 9 or count > 100:
            raise CommandError("--count must be between 9 and 100 so all workflow states are represented.")

        organization = Organization.objects.filter(name="Demo Organization").first()
        if organization is None:
            raise CommandError("Run seed_dev_demo first to create Demo Organization and its demo sites/users.")
        owner = self._member(organization, "owner@demo.local")
        reporter = self._member(organization, "technician@demo.local")
        supervisor = self._member(organization, "supervisor@demo.local")
        sites = list(Site.objects.for_organization(organization).filter(
            status=Site.Status.ACTIVE).order_by("code"))
        if not sites:
            raise CommandError("No active demo sites were found. Run seed_dev_demo first.")

        seed_file = Path(__file__).resolve().parents[2] / "fixtures" / "demo_work_orders.json"
        try:
            records = json.loads(seed_file.read_text(encoding="utf-8"))["records"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise CommandError(f"Unable to load Work Order seed data from {seed_file}.") from exc
        if not isinstance(records, list) or len(records) < 9:
            raise CommandError("Seed fixture must contain at least nine workflow examples.")

        created = skipped = 0
        now = timezone.now()
        for index in range(count):
            serial = index + 1
            sample = records[index % len(records)]
            status_target = sample["status"]
            title = f"Demo {sample['title']} · {serial:03d}"
            if WorkOrder.objects.for_organization(organization).filter(title=title).exists():
                skipped += 1
                continue

            site = sites[index % len(sites)]
            start = now.replace(minute=0, second=0, microsecond=0) + timedelta(
                days=(index // 3) - 3, hours=8 + (index % 3) * 3)
            end = start + timedelta(hours=2)
            due_at = end + timedelta(hours=1)
            checklist = [{"label": label, "required": True, "completed": False}
                         for label in sample["checklist"]]
            asset = self._asset(organization, site, sample["asset_reference"], serial, owner.user)
            create_values = {"title": title, "description": sample["description"],
                "work_type": sample["work_type"], "priority": sample["priority"],
                "planned_start": start, "planned_end": end, "due_at": due_at,
                "estimated_hours": Decimal("2.00"), "checklist_items": checklist}
            source_type = sample["source_type"]
            if source_type in (WorkOrder.SourceType.REQUEST, WorkOrder.SourceType.INCIDENT):
                kind = (ServiceRequest.Kind.SERVICE_REQUEST if source_type == WorkOrder.SourceType.REQUEST
                        else ServiceRequest.Kind.INCIDENT)
                source_title = f"Work Order seed source {serial:03d}"
                source = ServiceRequest.objects.for_organization(organization).filter(title=source_title).first()
                if source is None:
                    source = incident_services.create_request(organization, asset=asset, reporter=reporter,
                        title=source_title, description=sample["description"], kind=kind,
                        severity=sample["priority"], occurred_at=now - timedelta(days=1), actor=owner.user)
                if source.status not in (APPROVED,):
                    if source.status not in ("NEW", TRIAGED):
                        raise CommandError(f"Seed source {source.number} is not eligible for work-order creation.")
                    if source.status == "NEW":
                        source = incident_services.transition(source, action="triage", actor=owner.user)
                    source = incident_services.transition(source, action="approve", actor=owner.user)
                wo = incident_services.create_work_order_for_request(
                    source, actor=owner.user, membership=owner, **create_values)
            else:
                wo = services.create_work_order(organization, site_id=site.pk, asset=asset,
                    actor=owner.user, membership=owner, source_type=WorkOrder.SourceType.MANUAL,
                    source_reference="", **create_values)
            if status_target == WorkOrder.Status.DRAFT:
                created += 1
                continue
            wo = services.plan_work_order(wo, membership=owner, actor=owner.user,
                planned_start=start, planned_end=end, estimated_hours=Decimal("2.00"))
            if status_target == WorkOrder.Status.PLANNED:
                created += 1
                continue

            technician = self._technician(organization, serial)
            wo = services.assign_work_order(wo, membership=owner, actor=owner.user, assignee_id=technician.pk)
            if status_target == WorkOrder.Status.ASSIGNED:
                created += 1
                continue
            wo = services.dispatch_work_order(wo, membership=owner, actor=owner.user)
            if status_target == WorkOrder.Status.DISPATCHED:
                created += 1
                continue
            wo = services.start_work_order(wo, membership=technician, actor=technician.user)
            if status_target == WorkOrder.Status.ON_HOLD:
                wo = services.hold_work_order(wo, membership=technician, actor=technician.user,
                    reason="Waiting for scheduled site access.")

            services.record_work_update(wo, membership=technician, actor=technician.user,
                update="Inspected equipment and recorded service findings.")
            services.record_labor(wo, membership=technician, actor=technician.user,
                work_date=timezone.localdate(), hours=Decimal("1.50"),
                description="Inspected equipment and recorded service findings.")
            services.record_material(wo, membership=technician, actor=technician.user,
                item_name=sample["material"]["item_name"],
                part_reference=sample["material"]["part_reference"],
                quantity=Decimal(sample["material"]["quantity"]), unit=sample["material"]["unit"],
                notes="Seed example; no inventory movement.")

            if status_target in (WorkOrder.Status.COMPLETED, WorkOrder.Status.SUPERVISOR_REVIEW,
                                 WorkOrder.Status.CLOSED):
                attachment = file_services.attach(
                    SimpleUploadedFile(f"work-order-{serial:03d}-evidence.txt",
                        b"Development seed evidence: inspection readings recorded and verified."),
                    target=wo, organization=organization, user=technician.user,
                    description="Development seed completion evidence", read_permission="work_order.view")
                services.add_evidence(wo, membership=technician, actor=technician.user,
                                      attachment_id=attachment.pk)
                completed_checklist = [{"label": item["label"], "required": True, "completed": True}
                                       for item in checklist]
                wo = services.complete_work_order(wo, membership=technician, actor=technician.user,
                    resolution_notes="Inspection completed; equipment condition and readings were recorded.",
                    checklist_items=completed_checklist)
                if status_target in (WorkOrder.Status.SUPERVISOR_REVIEW, WorkOrder.Status.CLOSED):
                    wo = services.submit_for_review(wo, membership=technician, actor=technician.user)
                if status_target == WorkOrder.Status.CLOSED:
                    services.review_work_order(wo, membership=supervisor, actor=supervisor.user,
                        approved=True, notes="Seed example approved by supervisor.")
            created += 1

        self._seed_create_form_options(organization, sites, owner, reporter, now)

        self.stdout.write(self.style.SUCCESS(
            f"Created {created} sample work orders; skipped {skipped} existing records."))
        self.stdout.write("Created through workflow services with database status events and audit records.")
        self.stdout.write("Development/testing only. Page loads, API GETs, migrations, and startup never run this command.")

    @staticmethod
    def _member(organization, email):
        member = Membership.objects.for_organization(organization).filter(
            user__email=email).select_related("user").first()
        if member is None or not member.is_active:
            raise CommandError(f"Active demo membership missing: {email}. Run seed_dev_demo first.")
        return member

    @staticmethod
    def _asset(organization, site, asset_reference, serial, actor):
        category = AssetCategory.objects.for_organization(organization).filter(name="Work Order Demo Assets").first()
        if category is None:
            category = asset_services.create_category(organization, name="Work Order Demo Assets", actor=actor)
        tag = f"{asset_reference[:25]}-{serial:03d}"
        asset = Asset.objects.for_organization(organization).filter(asset_tag=tag).first()
        if asset is None:
            asset = asset_services.create_asset(organization, site=site, category=category, actor=actor,
                asset_tag=tag, name=f"Demo {asset_reference} asset")
        return asset

    @classmethod
    def _seed_create_form_options(cls, organization, sites, owner, reporter, now):
        """Keep all database-backed create-form selectors populated in development."""
        from apps.assets import hierarchy as asset_hierarchy
        from apps.assets.models import AssetComponent

        source_assets = {}
        for index, site in enumerate(sites):
            parent = cls._asset(organization, site, f"FORM-PARENT-{site.code}", 900 + index * 2, owner.user)
            child = cls._asset(organization, site, f"FORM-COMPONENT-{site.code}", 901 + index * 2, owner.user)
            source_assets[site.pk] = parent
            if not AssetComponent.objects.filter(organization=organization, parent=parent, child=child).exists():
                asset_hierarchy.add_component(parent, child, actor=owner.user,
                    notes="Development-only component for Work Order form testing.")

        for kind in (ServiceRequest.Kind.SERVICE_REQUEST, ServiceRequest.Kind.INCIDENT):
            title = f"Work Order create form source {kind.lower().replace('_', ' ')}"
            source = ServiceRequest.objects.for_organization(organization).filter(title=title).first()
            if source is None:
                source = incident_services.create_request(organization,
                    asset=source_assets[sites[0].pk], reporter=reporter, title=title,
                    description="Approved development source for testing Work Order creation.",
                    kind=kind, severity=ServiceRequest.Severity.HIGH,
                    occurred_at=now - timedelta(hours=1), actor=owner.user)
                source = incident_services.transition(source, action="triage", actor=owner.user)
                incident_services.transition(source, action="approve", actor=owner.user)

    @staticmethod
    def _technician(organization, serial):
        """Give each active seeded order its own technician to respect the one-active-job rule."""
        User = get_user_model()
        email = f"workorder-seed-tech-{serial:03d}@demo.local"
        user, _ = User.objects.get_or_create(email=email, defaults={
            "full_name": f"Work Order Demo Technician {serial:03d}",
            "is_active": True,
        })
        if not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])
        if not user.has_usable_password():
            user.set_password(os.environ.get("FIELDOPS_DEMO_PASSWORD", "FieldOps-Dev-2026!"))
            user.save(update_fields=["password"])
        member = Membership.objects.for_organization(organization).filter(user=user).first()
        if member is None:
            member = Membership.objects.create(organization=organization, user=user,
                status=Membership.Status.ACTIVE, job_title="Technician")
        elif not member.is_active:
            member.status = Membership.Status.ACTIVE
            member.save(update_fields=["status"])
        role = rbac.system_role_by_key(organization, "technician")
        if not member.membership_roles.filter(role=role).exists():
            rbac.set_membership_roles(member, [role], actor=None, system=True)
        return member
