"""Celery tasks for M11. Idempotent (a warning / breach / escalation is persisted once under a unique key, a second
run finds it and does nothing), retry-safe (one transaction and row lock per tracking), tenant-safe (one organization
at a time inside ``tenant_context``; suspended organizations are skipped)."""
import logging

from celery import shared_task
from django.db import OperationalError

log = logging.getLogger(__name__)


@shared_task(name="apps.sla.tasks.monitor_sla", bind=True, autoretry_for=(OperationalError,), retry_backoff=True,
             max_retries=5)
def monitor_sla(self) -> dict:
    from apps.tenancy.models import Organization

    from . import services

    total = {"organizations": 0, "checked": 0, "warnings": 0, "breaches": 0, "escalations": 0, "errors": 0}
    for org in Organization.objects.filter(status=Organization.Status.ACTIVE):
        total["organizations"] += 1
        res = services.process_organization(org)
        for key in ("checked", "warnings", "breaches", "escalations", "errors"):
            total[key] += res[key]
    log.info("SLA monitor finished", extra={"sla_result": total})
    return total


@shared_task(name="apps.sla.tasks.monitor_sla_for_org", bind=True, autoretry_for=(OperationalError,),
             retry_backoff=True, max_retries=5)
def monitor_sla_for_org(self, organization_id: str) -> dict:
    """One organization's SLA run (the unit of work of the fan-out). Idempotent like ``monitor_sla``; a suspended or
    unknown organization is a no-op."""
    from apps.tenancy.models import Organization

    from . import services

    org = Organization.objects.filter(pk=organization_id, status=Organization.Status.ACTIVE).first()
    if org is None:
        return {"organization": organization_id, "skipped": True}
    return {"organization": organization_id, **services.process_organization(org)}


@shared_task(name="apps.sla.tasks.fan_out_sla_monitor")
def fan_out_sla_monitor() -> dict:
    """Beat entry point: one independent task per active organization (a slow or failing tenant cannot delay the
    others, and workers process tenants in parallel)."""
    from apps.tenancy.models import Organization

    ids = [str(pk) for pk in Organization.objects.filter(status=Organization.Status.ACTIVE).values_list("pk", flat=True)]
    for pk in ids:
        monitor_sla_for_org.delay(pk)
    return {"dispatched": len(ids)}
