from __future__ import annotations

import hashlib
import io
import secrets
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.assets.models import Asset
from apps.assets.workflow import TERMINAL_STATES
from apps.audit import services as audit
from apps.core.exceptions import Conflict, NotFound, PermissionDenied, ValidationFailed
from apps.rbac import services as rbac

from .models import AssetIdentifier, ScanEvent


def _token(kind: str) -> str:
    if kind == AssetIdentifier.Kind.BARCODE:
        # 12 alphanumeric characters (Code 128 friendly)
        return secrets.token_hex(6).upper()
    # 128-bit URL-safe random string for QR codes
    return secrets.token_urlsafe(16)


def _target_url(token: str, request=None) -> str:
    base = getattr(settings, "SITE_BASE_URL", "http://localhost:8000").rstrip("/")
    if request:
        try:
            base = request.build_absolute_uri("/").rstrip("/")
        except Exception:
            pass
    return f"{base}/app/s/{token}/"


def render_qr_svg(token: str, request=None) -> str:
    import segno

    url = _target_url(token, request)
    qr = segno.make(url, error="M")
    out = io.BytesIO()
    qr.save(out, kind="svg", scale=4, border=2)
    return out.getvalue().decode("utf-8")


def render_qr_png(token: str, request=None) -> bytes:
    import segno

    url = _target_url(token, request)
    qr = segno.make(url, error="M")
    out = io.BytesIO()
    qr.save(out, kind="png", scale=8, border=2)
    return out.getvalue()


def render_barcode_svg(token: str) -> str:
    import barcode
    from barcode.writer import SVGWriter

    code128 = barcode.get_barcode_class("code128")
    bc = code128(token, writer=SVGWriter())
    out = io.BytesIO()
    bc.write(out, options={"write_text": True, "module_height": 12.0})
    return out.getvalue().decode("utf-8")


@transaction.atomic
def generate_identifier(asset: Asset, *, kind: str = "QR", actor=None, request=None) -> AssetIdentifier:
    if asset.status in TERMINAL_STATES:
        raise Conflict(
            f"The asset is {asset.status.lower()} and cannot receive labels.",
            code="asset_terminal",
        )
    if kind not in AssetIdentifier.Kind.values:
        raise ValidationFailed(f"Invalid label kind: {kind}", code="invalid_kind")

    existing = AssetIdentifier.objects.filter(
        organization=asset.organization, asset=asset, kind=kind, is_active=True
    ).first()
    if existing:
        raise Conflict(
            f"An active {kind.lower()} label already exists for this asset.",
            code="identifier_exists",
        )

    # Generate unique token
    token = _token(kind)
    while AssetIdentifier.objects.filter(token=token).exists():
        token = _token(kind)

    identifier = AssetIdentifier(
        organization=asset.organization,
        asset=asset,
        kind=kind,
        token=token,
        is_active=True,
    )
    identifier.save()
    audit.record(
        "qr.generated",
        actor=actor,
        organization=asset.organization,
        target=asset,
        after={"identifier_id": str(identifier.pk), "kind": kind, "token": token},
        metadata={"kind": kind},
        request=request,
    )
    return identifier


@transaction.atomic
def replace_identifier(
    identifier: AssetIdentifier, *, reason: str, actor, request=None
) -> AssetIdentifier:
    asset = identifier.asset
    if asset.status in TERMINAL_STATES:
        raise Conflict(
            f"The asset is {asset.status.lower()} and cannot receive labels.",
            code="asset_terminal",
        )
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValidationFailed("A reason is required to replace a label.", code="reason_required")

    old_token = identifier.token
    identifier.is_active = False
    identifier.revoked_at = timezone.now()
    identifier.revoked_by = actor
    identifier.revoked_reason = reason[:300]
    identifier.save(update_fields=["is_active", "revoked_at", "revoked_by", "revoked_reason", "updated_at"])

    audit.record(
        "qr.revoked",
        actor=actor,
        organization=asset.organization,
        target=asset,
        before={"identifier_id": str(identifier.pk), "is_active": True},
        after={"identifier_id": str(identifier.pk), "is_active": False, "reason": reason},
        metadata={"action": "replace", "kind": identifier.kind},
        request=request,
    )

    new_token = _token(identifier.kind)
    while AssetIdentifier.objects.filter(token=new_token).exists():
        new_token = _token(identifier.kind)

    new_identifier = AssetIdentifier(
        organization=asset.organization,
        asset=asset,
        kind=identifier.kind,
        token=new_token,
        is_active=True,
    )
    new_identifier.save()

    audit.record(
        "qr.replaced",
        actor=actor,
        organization=asset.organization,
        target=asset,
        before={"identifier_id": str(identifier.pk), "token": old_token},
        after={"identifier_id": str(new_identifier.pk), "token": new_token},
        metadata={"kind": identifier.kind, "reason": reason},
        request=request,
    )
    return new_identifier


@transaction.atomic
def revoke_identifier(
    identifier: AssetIdentifier, *, reason: str, actor, request=None
) -> AssetIdentifier:
    if not identifier.is_active:
        raise Conflict("This label is already revoked or replaced.", code="already_revoked")
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValidationFailed("A reason is required to revoke a label.", code="reason_required")

    identifier.is_active = False
    identifier.revoked_at = timezone.now()
    identifier.revoked_by = actor
    identifier.revoked_reason = reason[:300]
    identifier.save(update_fields=["is_active", "revoked_at", "revoked_by", "revoked_reason", "updated_at"])

    audit.record(
        "qr.revoked",
        actor=actor,
        organization=identifier.organization,
        target=identifier.asset,
        before={"identifier_id": str(identifier.pk), "is_active": True},
        after={"identifier_id": str(identifier.pk), "is_active": False, "reason": reason},
        metadata={"kind": identifier.kind},
        request=request,
    )
    return identifier


def resolve_token(membership, token: str, *, actor=None, request=None) -> tuple[AssetIdentifier, Asset, bool]:
    """Look up an identifier token in the caller's organization and verify permissions."""
    token = (token or "").strip()
    # Strip URL prefix if a full URL was provided
    if "/s/" in token:
        token = token.split("/s/")[-1].strip("/").split("?")[0].split("#")[0]

    org = membership.organization
    identifier = (
        AssetIdentifier.objects.for_organization(org)
        .filter(token=token)
        .select_related("asset__site", "asset__zone", "asset__category")
        .first()
    )

    token_fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12] if token else "empty"

    if identifier is None:
        audit.record(
            "qr.scan_unknown",
            actor=actor,
            organization=org,
            metadata={"fingerprint": token_fingerprint},
            request=request,
        )
        raise NotFound("Label not found.", code="label_not_found")

    asset = identifier.asset
    # Check site scope / asset.view permission
    if not rbac.has_permission(membership, "asset.view", asset.site_id):
        audit.record(
            "qr.scan_denied",
            actor=actor,
            organization=org,
            target=asset,
            metadata={"identifier_id": str(identifier.pk), "site_id": str(asset.site_id)},
            request=request,
        )
        raise PermissionDenied("You do not have permission to view this asset.", code="permission_denied")

    # Record scan event
    ScanEvent(
        organization=org,
        identifier=identifier,
        asset=asset,
        scanned_by=actor,
    ).save()

    if not identifier.is_active:
        audit.record(
            "qr.scan_revoked",
            actor=actor,
            organization=org,
            target=asset,
            metadata={"identifier_id": str(identifier.pk), "revoked_at": str(identifier.revoked_at)},
            request=request,
        )
    else:
        audit.record(
            "qr.scanned",
            actor=actor,
            organization=org,
            target=asset,
            metadata={"identifier_id": str(identifier.pk), "kind": identifier.kind},
            request=request,
        )

    return identifier, asset, identifier.is_active
