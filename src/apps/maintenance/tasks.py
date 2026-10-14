"""Celery tasks for M04. Idempotent (running them again changes nothing until the next occurrence is due),
retry-safe (each schedule generates in its own transaction under a row lock), tenant-safe (one organization at a
time inside ``tenant_context``; suspended organizations are skipped) and observable (structured result + log +
``MaintenanceSchedule.last_run_at / last_error``)."""
import logging

from celery import shared_task
from django.db import OperationalError

log = logging.getLogger(__name__)


@shared_task(name="apps.maintenance.tasks.generate_due_maintenance")
def generate_due_maintenance() -> dict:
    from apps.tenancy.models import Organization

    from . import services

    total = {"organizations": 0, "generated": 0, "errors": 0, "reminders": 0}
    for org in Organization.objects.filter(status=Organization.Status.ACTIVE):
        total["organizations"] += 1
        result = services.run_for_organization(org)
        for key in ("generated", "errors", "reminders"):
            total[key] += result[key]
    log.info("PM scheduler finished", extra={"pm_result": total})
    return total


@shared_task(name="apps.maintenance.tasks.generate_schedule", bind=True, autoretry_for=(OperationalError,),
             retry_backoff=True, max_retries=5)
def generate_schedule(self, organization_id: str, schedule_id: str) -> str | None:
    """Generates one schedule of one organization (safe to retry: a second run finds nothing due, returns None)."""
    from apps.core.tenant import tenant_context
    from apps.tenancy.models import Organization

    from . import services
    from .models import MaintenanceSchedule

    org = Organization.objects.get(pk=organization_id, status=Organization.Status.ACTIVE)
    with tenant_context(org):
        cycle = services.generate_cycle(MaintenanceSchedule.objects.get(pk=schedule_id))
    return str(cycle.pk) if cycle else None


@shared_task(name="apps.maintenance.tasks.generate_due_maintenance_for_org", bind=True,
             autoretry_for=(OperationalError,), retry_backoff=True, max_retries=5)
def generate_due_maintenance_for_org(self, organization_id: str) -> dict:
    """One organization's PM run (idempotent; the DB allows one work order per cycle). Suspended = no-op."""
    from apps.tenancy.models import Organization

    from . import services

    org = Organization.objects.filter(pk=organization_id, status=Organization.Status.ACTIVE).first()
    if org is None:
        return {"organization": organization_id, "skipped": True}
    return {"organization": organization_id, **services.run_for_organization(org)}


@shared_task(name="apps.maintenance.tasks.fan_out_maintenance")
def fan_out_maintenance() -> dict:
    """Beat entry point: one independent task per active organization."""
    from apps.tenancy.models import Organization

    ids = [str(pk) for pk in Organization.objects.filter(status=Organization.Status.ACTIVE).values_list("pk", flat=True)]
    for pk in ids:
        generate_due_maintenance_for_org.delay(pk)
    return {"dispatched": len(ids)}
