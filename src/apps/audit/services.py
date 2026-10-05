"""Audit foundation. Call ``record()`` from service-layer code inside the same transaction as the change,
so the audit row and the business change commit (or roll back) together."""
from __future__ import annotations

import datetime
import decimal
import uuid

from django.db.models import Model

from apps.core import logging as flogging

from .models import AuditLog


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID | decimal.Decimal):
        return str(value)
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, Model):
        return str(value.pk)
    return value


def snapshot(instance, fields: list[str]) -> dict:
    """Extracts a JSON-safe dict of the named attributes - use for before/after state."""
    return _jsonable({f: getattr(instance, f) for f in fields})


def site_of(target):
    """Site of an audited record, when it has one: its own ``site_id`` (assets, work orders, requests, sites ...),
    else through its asset / work order / plan / warehouse / schedule link. Never raises."""
    try:
        from apps.sites.models import Site

        if isinstance(target, Site):
            return target.pk
        for path in (("site_id",), ("asset", "site_id"), ("work_order", "site_id"), ("warehouse", "site_id"),
                     ("plan", "site_id"), ("balance", "warehouse", "site_id"), ("tracking", "site_id"),
                     ("request", "site_id")):
            obj = target
            for part in path:
                obj = getattr(obj, part, None)
                if obj is None:
                    break
            else:
                return obj
    except Exception:  # an unexpected shape must never break the audited operation
        return None
    return None


def client_ip(request) -> str | None:
    if request is None:
        return None
    from apps.core.net import (
        client_ip as resolve,  # honours TRUSTED_PROXY_COUNT (same rule as login throttling)
    )

    return resolve(request)


def record(
    action: str,
    *,
    actor=None,
    organization=None,
    target=None,
    target_repr: str = "",
    before: dict | None = None,
    after: dict | None = None,
    metadata: dict | None = None,
    request=None,
) -> AuditLog:
    target_type = target_id = ""
    site_id = None
    if target is not None:
        target_type = target._meta.label_lower
        target_id = str(target.pk)
        target_repr = target_repr or str(target)[:300]
        site_id = site_of(target)
    if actor is not None and not getattr(actor, "is_authenticated", True):
        actor = None
    return AuditLog.objects.create(
        organization=organization,
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_repr=target_repr[:300],
        before=_jsonable(before) if before is not None else None,
        after=_jsonable(after) if after is not None else None,
        metadata=_jsonable(metadata or {}),
        site_id=site_id,
        ip_address=client_ip(request),
        request_id=flogging.request_id_var.get() if flogging.request_id_var.get() != "-" else "",
    )
