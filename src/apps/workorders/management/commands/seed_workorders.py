"""Create a repeatable, audited Module 6 demo dataset.

Run after ``seed_dev_demo`` with ``FIELDOPS_ALLOW_DEMO_SEED=true``. Each sample is
created through the same work-order services used by the UI and API; transitions,
labor, materials, and history are therefore persisted normally.
"""
import json
import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.sites.models import Site
from apps.tenancy.models import Membership, Organization
from apps.workorders import services
from apps.workorders.models import WorkOrder


class Command(BaseCommand):
    help = "Seed repeatable Module 6 work orders for local filter and workflow demos."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=27, help="Number of sample work orders to create.")

    def handle(self, *args, **options):
        if not (settings.DEBUG or os.environ.get("FIELDOPS_ALLOW_DEMO_SEED") == "true"):
            raise CommandError("Local-only seed blocked. Set FIELDOPS_ALLOW_DEMO_SEED=true for the local Docker stack.")
        count = options["count"]
        if count < 9 or count > 100:
            raise CommandError("--count must be between 9 and 100 so workflow/filter coverage stays useful.")
        organization = Organization.objects.filter(name="Demo Organization").first()
        if organization is None:
            raise CommandError("Run seed_dev_demo first to create Demo Organization and its demo users/sites.")
        owner = self._member(organization, "owner@demo.local")
        technician = self._member(organization, "technician@demo.local")
        supervisor = self._member(organization, "supervisor@demo.local")
        sites = list(Site.objects.for_organization(organization).filter(status=Site.Status.ACTIVE).order_by("code"))
        if not sites:
            raise CommandError("No active demo sites were found. Run seed_dev_demo first.")

        seed_file = Path(__file__).resolve().parents[2] / "fixtures" / "demo_work_orders.json"
        try:
            seed_data = json.loads(seed_file.read_text(encoding="utf-8"))
            records = seed_data["records"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise CommandError(f"Unable to load Work Order seed data from {seed_file}.") from exc
        if not isinstance(records, list) or len(records) < 9:
            raise CommandError("Work Order seed data must contain at least nine workflow examples.")

        created = skipped = 0
        now = timezone.now()

        for index in range(count):
            serial = index + 1
            site = sites[index % len(sites)]
            sample = records[index % len(records)]
            status_target = sample["status"]
            asset_reference = sample["asset_reference"]
            title = f"Demo {sample['title']} · {serial:03d}"
            if WorkOrder.objects.for_organization(organization).filter(title=title).exists():
                skipped += 1
                continue

            source_reference = sample["source_reference"].format(serial=serial)
            # Three non-overlapping work windows per day exercise the assignment conflict check.
            start = now.replace(minute=0, second=0, microsecond=0) + timedelta(
                days=(index // 3) - 3, hours=8 + (index % 3) * 3)
            end = start + timedelta(hours=2)
            due_at = end + timedelta(hours=1)
            checklist = [{"label": label, "required": True, "completed": False}
                         for label in sample["checklist"]]
            wo = services.create_work_order(
                organization, site_id=site.pk, title=title, actor=owner.user, membership=owner,
                source_type=sample["source_type"], source_reference=source_reference,
                asset_reference=asset_reference, work_type=sample["work_type"],
                priority=sample["priority"], description=sample["description"],
                planned_start=start, planned_end=end, due_at=due_at,
                estimated_hours=Decimal("2.00"), checklist_items=checklist,
            )

            if status_target == WorkOrder.Status.DRAFT:
                created += 1
                continue
            wo = services.plan_work_order(wo, membership=owner, actor=owner.user,
                                          planned_start=start, planned_end=end, estimated_hours=Decimal("2.00"))
            if status_target == WorkOrder.Status.PLANNED:
                created += 1
                continue
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
            services.record_labor(wo, membership=technician, actor=technician.user,
                                  work_date=timezone.localdate(), hours=Decimal("1.50"),
                                  description="Inspected equipment and recorded service findings.")
            services.record_material(wo, membership=technician, actor=technician.user,
                                     item_name=sample["material"]["item_name"],
                                     part_reference=sample["material"]["part_reference"],
                                     quantity=Decimal(sample["material"]["quantity"]),
                                     unit=sample["material"]["unit"],
                                     notes="Seed example; no inventory movement.")
            if status_target in (WorkOrder.Status.COMPLETED, WorkOrder.Status.SUPERVISOR_REVIEW,
                                 WorkOrder.Status.CLOSED):
                completed_checklist = [{"label": item["label"], "required": True, "completed": True}
                                       for item in checklist]
                wo = services.complete_work_order(
                    wo, membership=technician, actor=technician.user,
                    resolution_notes="Inspection completed; equipment condition and readings were recorded.",
                    checklist_items=completed_checklist,
                )
                if status_target in (WorkOrder.Status.SUPERVISOR_REVIEW, WorkOrder.Status.CLOSED):
                    wo = services.submit_for_review(wo, membership=technician, actor=technician.user)
                if status_target == WorkOrder.Status.CLOSED:
                    services.review_work_order(wo, membership=supervisor, actor=supervisor.user,
                                               approved=True, notes="Seed example approved by supervisor.")
            created += 1

        self.stdout.write(self.style.SUCCESS(f"Created {created} sample work orders; skipped {skipped} existing records."))
        self.stdout.write("Source, site, priority, work type, due-date, assignee, status and pagination filters are populated.")
        self.stdout.write("COMPLETED is persisted; submit_for_review advances it to SUPERVISOR_REVIEW through the workflow service.")

    @staticmethod
    def _member(organization, email):
        member = Membership.objects.for_organization(organization).filter(user__email=email).select_related("user").first()
        if member is None or not member.is_active:
            raise CommandError(f"Active demo membership missing: {email}. Run seed_dev_demo first.")
        return member
