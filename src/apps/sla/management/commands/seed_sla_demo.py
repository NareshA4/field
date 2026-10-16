"""Seed functional SLA policies and track existing demo requests / work orders.

Run ``python manage.py seed_sla_demo`` after the local demo organization and records exist.
This command is local-development-only and safe to run repeatedly.
"""
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.tenant import tenant_context
from apps.incidents.models import ServiceRequest
from apps.rbac.models import Role
from apps.tenancy.models import Membership, Organization
from apps.workorders.models import WorkOrder

from apps.sla import services
from apps.sla.models import REQUEST, WORK_ORDER, EscalationRule, SLAProfile, SLATracking


POLICIES = (
    {
        "name": "Demo Incident Response Policy",
        "applies_to": REQUEST,
        "targets": {
            "LOW": (240, 1440), "MEDIUM": (120, 720), "HIGH": (60, 240), "CRITICAL": (15, 120),
        },
    },
    {
        "name": "Demo Work Order Response Policy",
        "applies_to": WORK_ORDER,
        "targets": {"LOW": (240, 1440), "MEDIUM": (120, 720), "HIGH": (60, 240), "URGENT": (15, 120)},
    },
)


class Command(BaseCommand):
    help = "Create demo SLA policies, escalation rules, and tracking for existing demo records."

    def add_arguments(self, parser):
        parser.add_argument("--org", default="Demo Organization", help="Organization name to seed.")

    def handle(self, *args, **options):
        if not (settings.DEBUG or os.environ.get("FIELDOPS_ALLOW_DEMO_SEED", "").lower() == "true"):
            raise CommandError("Demo SLA seed blocked. Use DEBUG or explicitly allow local demo seeding.")
        org = Organization.objects.filter(name=options["org"]).first()
        if org is None:
            raise CommandError("Organization not found. Create the local demo organization first.")
        owner = Membership.objects.filter(organization=org, user__email="owner@demo.local",
                                          status=Membership.Status.ACTIVE).select_related("user").first()
        if owner is None:
            raise CommandError("Active demo owner is missing. Run the local demo seed first.")

        with tenant_context(org):
            supervisor_role = Role.objects.filter(organization=org, system_key="supervisor").first()
            operations_role = Role.objects.filter(organization=org, system_key="operations_manager").first()
            if supervisor_role is None:
                raise CommandError("The demo organization needs a supervisor role for escalation examples.")

            profiles = [self._policy(org, owner.user, row) for row in POLICIES]
            self._rules(org, owner.user, profiles, supervisor_role, operations_role or supervisor_role)
            self._backfill(org)

        self.stdout.write(self.style.SUCCESS(
            f"SLA demo data ready for {org.name}: {SLAProfile.objects.for_organization(org).count()} policies, "
            f"{SLATracking.objects.for_organization(org).count()} tracking records."))

    @staticmethod
    def _policy(org, actor, spec):
        profile = SLAProfile.objects.for_organization(org).filter(name=spec["name"]).first()
        if profile is None:
            profile = services.create_profile(org, name=spec["name"], applies_to=spec["applies_to"], actor=actor,
                                              description="Local demo targets with automatic supervisor escalation.")
        elif not profile.is_active:
            profile = services.set_profile_active(profile, True, actor=actor)
        for priority, (response, resolution) in spec["targets"].items():
            if not profile.targets.filter(priority=priority).exists():
                services.set_target(profile, priority=priority, response_minutes=response,
                                    resolution_minutes=resolution, warning_percent=80, actor=actor)
        return profile

    @staticmethod
    def _rules(org, actor, profiles, supervisor_role, operations_role):
        rule_specs = (
            ("RESPONSE", EscalationRule.Trigger.WARNING, 0, 1, supervisor_role),
            ("RESPONSE", EscalationRule.Trigger.BREACH, 0, 1, supervisor_role),
            ("RESPONSE", EscalationRule.Trigger.ESCALATION, 30, 2, operations_role),
            ("RESOLUTION", EscalationRule.Trigger.BREACH, 0, 1, supervisor_role),
            ("RESOLUTION", EscalationRule.Trigger.ESCALATION, 60, 2, operations_role),
        )
        for profile in profiles:
            for kind, trigger, after, level, role in rule_specs:
                exists = EscalationRule.objects.for_organization(org).filter(
                    profile=profile, target_kind=kind, trigger=trigger, after_minutes=after).exists()
                if not exists:
                    services.create_rule(profile, target_kind=kind, trigger=trigger, after_minutes=after,
                                          level=level, notify_role=role, actor=actor)

    @staticmethod
    def _backfill(org):
        """Start SLA tracking for demo records created before demo policies were configured.

        Replaying the current state through the same lifecycle hooks records met, breached, paused,
        cancelled, and review states using the real SLA service logic. Linked Work Orders continue
        to use their originating request's clock.
        """
        for sr in ServiceRequest.objects.for_organization(org).select_related("reported_by__user"):
            if SLATracking.objects.for_organization(org).filter(request=sr).exists():
                continue
            tracking = services.on_request_created(sr)
            if tracking is not None:
                services.on_request_status_changed(sr, "NEW", sr.status, actor=sr.reported_by.user)
        for wo in WorkOrder.objects.for_organization(org).select_related("created_by", "source_request"):
            if wo.source_request_id or SLATracking.objects.for_organization(org).filter(work_order=wo).exists():
                continue
            tracking = services.on_work_order_created(wo)
            if tracking is not None:
                services.on_work_order_changed(wo, WorkOrder.Status.DRAFT, wo.status, actor=wo.created_by)
