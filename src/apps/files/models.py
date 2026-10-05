from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.core.models import TenantOwnedModel
from apps.core.uploads import tenant_upload_path


class Attachment(TenantOwnedModel):
    """Generic evidence/file attachment for any tenant-owned object (HPE entity: Attachment)."""

    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.CharField(max_length=64)
    target = GenericForeignKey("content_type", "object_id")
    file = models.FileField(upload_to=tenant_upload_path, max_length=300)
    original_name = models.CharField(max_length=150)
    mime_type = models.CharField(max_length=100)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    description = models.CharField(max_length=200, blank=True)
    read_permission = models.CharField(
        max_length=80, blank=True,
        help_text="Permission code required to download; blank = any active member of the organization.",
    )
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "content_type", "object_id"])]
