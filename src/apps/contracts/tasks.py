"""Celery task for M10: renewal alerts. Idempotent (an agreement alerts once per end date), tenant-safe (one
organization at a time inside ``tenant_context``)."""
import logging

from celery import shared_task

log = logging.getLogger(__name__)


@shared_task(name="apps.contracts.tasks.send_renewal_alerts")
def send_renewal_alerts() -> dict:
    from apps.core.tenant import tenant_context
    from apps.tenancy.models import Organization

    from . import services

    total = {"organizations": 0, "alerts": 0}
    for org in Organization.objects.filter(status=Organization.Status.ACTIVE):
        total["organizations"] += 1
        with tenant_context(org):
            total["alerts"] += services.run_alerts(org)
    log.info("Contract renewal alerts finished", extra={"contract_result": total})
    return total


@shared_task(name="apps.contracts.tasks.send_renewal_alerts_for_org", bind=True, autoretry_for=(OSError,),
             retry_backoff=True, max_retries=3)
def send_renewal_alerts_for_org(self, organization_id: str) -> dict:
    """One organization's renewal / expiry alerts (an agreement alerts once per end date). Suspended = no-op."""
    from apps.core.tenant import tenant_context
    from apps.tenancy.models import Organization

    from . import services

    org = Organization.objects.filter(pk=organization_id, status=Organization.Status.ACTIVE).first()
    if org is None:
        return {"organization": organization_id, "skipped": True}
    with tenant_context(org):
        return {"organization": organization_id, "alerts": services.run_alerts(org)}


@shared_task(name="apps.contracts.tasks.fan_out_renewal_alerts")
def fan_out_renewal_alerts() -> dict:
    """Beat entry point: one independent task per active organization."""
    from apps.tenancy.models import Organization

    ids = [str(pk) for pk in Organization.objects.filter(status=Organization.Status.ACTIVE).values_list("pk", flat=True)]
    for pk in ids:
        send_renewal_alerts_for_org.delay(pk)
    return {"dispatched": len(ids)}
