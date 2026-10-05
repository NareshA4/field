from __future__ import annotations

import hashlib

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError

from apps.audit import services as audit
from apps.core.exceptions import ValidationFailed
from apps.core.uploads import validate_upload

from .models import Attachment


def attach(uploaded, *, target, organization, user, description: str = "", read_permission: str = "",
           request=None) -> Attachment:
    """Validates and stores an upload against a tenant-owned ``target`` object.

    The target must belong to ``organization`` (checked here so a module cannot attach across tenants)."""
    if getattr(target, "organization_id", None) != organization.pk:
        raise ValidationFailed("Attachment target belongs to a different organization.", code="cross_tenant")
    try:
        meta = validate_upload(uploaded)
    except ValidationError as exc:
        raise ValidationFailed(exc.messages[0], code=exc.code or "invalid_file") from exc
    digest = hashlib.sha256()
    for chunk in uploaded.chunks():
        digest.update(chunk)
    uploaded.seek(0)
    att = Attachment(
        organization=organization,
        content_type=ContentType.objects.get_for_model(target),
        object_id=str(target.pk),
        original_name=meta["name"], mime_type=meta["mime"], size=meta["size"], sha256=digest.hexdigest(),
        description=description[:200], read_permission=read_permission, uploaded_by=user,
    )
    att.file.save(meta["name"], uploaded, save=False)
    att.save()
    audit.record("file.uploaded", actor=user, organization=organization, target=att,
                 target_repr=att.original_name, metadata={"size": att.size, "sha256": att.sha256,
                                                          "for": f"{att.content_type.model}:{att.object_id}"},
                 request=request)
    return att
