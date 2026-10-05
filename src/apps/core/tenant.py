"""Tenant context + tenant-scoped manager (the data-layer half of tenant isolation).

Request-cycle behaviour is FAIL-CLOSED:
  * the middleware starts every request in ``NO_TENANT`` mode -> tenant-owned querysets are EMPTY;
  * once an organization membership is verified the context becomes that organization -> querysets are
    filtered to it automatically;
  * platform-console views run in ``PLATFORM`` mode (explicit, platform admins only).
Code running outside a request (management commands, migrations, shell) is in ``UNSET`` mode and is
unscoped. Celery tasks MUST wrap tenant work in ``tenant_context(org)``.

``Model.objects.unscoped()`` is the single greppable escape hatch. It is permitted only in the modules
listed in ``tests/test_tenant_isolation.py::test_unscoped_is_only_used_in_trusted_modules``.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager

from django.db import models


class _Mode:
    def __init__(self, name: str):
        self.name = name

    def __repr__(self):
        return f"<tenant:{self.name}>"


UNSET = _Mode("unset")
NO_TENANT = _Mode("none")
PLATFORM = _Mode("platform")

_current = contextvars.ContextVar("fieldops_tenant", default=UNSET)


def get_current():
    """Returns an Organization, or one of UNSET / NO_TENANT / PLATFORM."""
    return _current.get()


def get_current_org():
    value = _current.get()
    return None if isinstance(value, _Mode) else value


def set_current(value):
    return _current.set(value)


def reset_current(token):
    _current.reset(token)


@contextmanager
def tenant_context(org):
    token = _current.set(org)
    try:
        yield org
    finally:
        _current.reset(token)


@contextmanager
def platform_context():
    token = _current.set(PLATFORM)
    try:
        yield
    finally:
        _current.reset(token)


class TenantQuerySet(models.QuerySet):
    def for_organization(self, org):
        return self.filter(organization=org)


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """Default manager of every tenant-owned model."""

    use_in_migrations = False

    def get_queryset(self):
        qs = super().get_queryset()
        current = get_current()
        if current is UNSET or current is PLATFORM:
            return qs
        if current is NO_TENANT:
            return qs.none()
        return qs.filter(organization=current)

    def unscoped(self):
        """Explicit, audited-by-grep bypass of tenant scoping. Do not use in views."""
        return TenantQuerySet(self.model, using=self._db)
