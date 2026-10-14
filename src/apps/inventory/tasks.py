"""Celery tasks for M09: automated inventory min-max reorder alerts.
Idempotent and tenant-safe (runs inside tenant_context per organization).
"""
from __future__ import annotations

import logging
from decimal import Decimal

from celery import shared_task
from django.db import OperationalError

log = logging.getLogger(__name__)


@shared_task(name="apps.inventory.tasks.check_reorder_alerts_for_org", bind=True,
             autoretry_for=(OperationalError, OSError), retry_backoff=True, max_retries=3)
def check_reorder_alerts_for_org(self, organization_id: str) -> dict:
    """Checks stock levels against min/reorder thresholds for an organization and creates notifications."""
    from apps.core.tenant import tenant_context
    from apps.inventory.models import StockBalance
    from apps.notifications import services as notifications
    from apps.notifications.models import Notification
    from apps.rbac.models import MembershipRole
    from apps.tenancy.models import Organization

    org = Organization.objects.filter(pk=organization_id, status=Organization.Status.ACTIVE).first()
    if org is None:
        return {"organization": organization_id, "skipped": True}

    with tenant_context(org):
        # Find store managers, operations managers, and owners to notify
        recipient_memberships = MembershipRole.objects.filter(
            membership__organization=org,
            membership__status="ACTIVE",
            role__system_key__in=("stores_manager", "operations_manager", "owner", "admin"),
        ).values_list("membership_id", flat=True).distinct()

        if not recipient_memberships:
            return {"organization": organization_id, "alerts": 0, "reason": "no_recipients"}

        low_balances = []
        for bal in StockBalance.objects.for_organization(org).select_related("part", "warehouse"):
            if bal.is_low:
                low_balances.append(bal)

        alerts_created = 0
        for bal in low_balances:
            reorder_qty = bal.effective_reorder_quantity or Decimal("0")
            title = f"Low stock alert: {bal.part.part_number} at {bal.warehouse.code}"
            body = (
                f"Part {bal.part.part_number} ({bal.part.name}) in warehouse {bal.warehouse.code} "
                f"has available stock {bal.available} {bal.part.unit}, which is at or below minimum level "
                f"{bal.effective_min}. Suggested reorder quantity: {reorder_qty} {bal.part.unit}."
            )
            # Avoid duplicate unread notification in the last 24h
            recent = Notification.objects.for_organization(org).filter(
                title=title,
                source="inventory.reorder",
                read_at__isnull=True,
            ).exists()
            if not recent:
                notifications.notify(
                    org,
                    recipient_memberships,
                    title=title,
                    body=body,
                    link=f"/app/inventory/warehouses/{bal.warehouse_id}/",
                    level=Notification.Level.WARNING,
                    source="inventory.reorder",
                )
                alerts_created += 1

        log.info("Inventory reorder check complete", extra={
            "organization_id": organization_id,
            "low_balances": len(low_balances),
            "alerts_created": alerts_created,
        })
        return {
            "organization": organization_id,
            "low_balances": len(low_balances),
            "alerts_created": alerts_created,
        }


@shared_task(name="apps.inventory.tasks.fan_out_reorder_alerts")
def fan_out_reorder_alerts() -> dict:
    """Beat entry point: dispatches one task per active organization."""
    from apps.tenancy.models import Organization

    ids = [str(pk) for pk in Organization.objects.filter(status=Organization.Status.ACTIVE).values_list("pk", flat=True)]
    for pk in ids:
        check_reorder_alerts_for_org.delay(pk)
    return {"dispatched": len(ids)}
