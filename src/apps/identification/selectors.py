from __future__ import annotations

import uuid
from apps.core.exceptions import NotFound
from .models import AssetIdentifier, ScanEvent


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def identifiers_for(org, asset):
    return AssetIdentifier.objects.for_organization(org).filter(asset=asset)


def active_for_asset(org, asset) -> dict[str, AssetIdentifier | None]:
    active = {
        ident.kind: ident
        for ident in AssetIdentifier.objects.for_organization(org).filter(asset=asset, is_active=True)
    }
    return {
        "QR": active.get("QR"),
        "BARCODE": active.get("BARCODE"),
    }


def history_for_asset(org, asset):
    return (
        AssetIdentifier.objects.for_organization(org)
        .filter(asset=asset, is_active=False)
        .order_by("-revoked_at", "-created_at")
    )


def scans_for_asset(org, asset, limit: int = 10):
    return (
        ScanEvent.objects.for_organization(org)
        .filter(asset=asset)
        .select_related("scanned_by")
        .order_by("-created_at")[:limit]
    )


def get_identifier(org, pk) -> AssetIdentifier:
    ident = (
        AssetIdentifier.objects.for_organization(org)
        .filter(pk=_uuid_or_none(pk))
        .select_related("asset__site", "asset__zone")
        .first()
    )
    if ident is None:
        raise NotFound("Label not found.")
    return ident
