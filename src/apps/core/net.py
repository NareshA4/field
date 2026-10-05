"""Client network identity helpers shared by django-axes (login throttling) and the audit log.

Why this exists (incident 2026-10-03): behind Nginx every request has REMOTE_ADDR = the proxy, so per-IP
throttling treated all users as one client. ``client_ip`` honours X-Forwarded-For ONLY for the number of trusted
proxy hops configured in ``TRUSTED_PROXY_COUNT`` (0 = ignore the header entirely, so it cannot be spoofed).
"""
import ipaddress

from django.conf import settings


def _valid(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request) -> str | None:
    remote = _valid(request.META.get("REMOTE_ADDR", "")) if request.META.get("REMOTE_ADDR") else None
    hops = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
    if hops > 0:
        # Each trusted proxy appends the address it received the request from; the client is `hops` entries
        # from the right. Anything the client itself put at the left of the header is ignored.
        chain = [p for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
        if len(chain) >= hops:
            candidate = _valid(chain[-hops])
            if candidate:
                return candidate
    return remote


def axes_username(request, credentials=None) -> str | None:
    """Username used by django-axes for lockout bookkeeping: the credential Django passes to authenticate()
    (key ``username``, even though our USERNAME_FIELD is ``email``), normalised so that case/whitespace
    variants cannot dodge the failure counter."""
    value = None
    if credentials:
        value = credentials.get("username")
    if value is None:
        data = getattr(request, "data", None) or request.POST
        value = data.get("username")
    return value.strip().lower() if isinstance(value, str) and value.strip() else None
