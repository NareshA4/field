"""Upload validation: extension allow-list, size, sniffed MIME type, sanitized storage path.

The extension must be allowed, the *content* (magic bytes) must match the extension family, and the stored
file name is a random UUID - the client-supplied name is kept only as metadata (never used in paths).
"""
import os
import re
import uuid

import filetype
from django.conf import settings
from django.core.exceptions import ValidationError

ALLOWED = {
    # extension: allowed sniffed MIME types
    "pdf": {"application/pdf"},
    "png": {"image/png"},
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "webp": {"image/webp"},
    "gif": {"image/gif"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/zip"},
    "xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/zip"},
    "txt": {None},  # text has no magic bytes; checked for NUL bytes below
    "csv": {None},
}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ \-]")


def clean_display_name(name: str) -> str:
    base = os.path.basename(name or "file").replace("\x00", "")
    return _SAFE_NAME.sub("_", base)[:150] or "file"


def validate_upload(uploaded, *, max_bytes: int | None = None) -> dict:
    """Validates an UploadedFile; returns {'name','extension','mime','size'}. Raises ValidationError."""
    max_bytes = max_bytes or settings.UPLOAD_MAX_BYTES
    name = clean_display_name(uploaded.name)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED:
        raise ValidationError(f"File type '.{ext}' is not allowed.", code="file_type")
    if uploaded.size > max_bytes:
        raise ValidationError(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit.", code="file_size")
    if uploaded.size == 0:
        raise ValidationError("File is empty.", code="file_empty")

    head = uploaded.read(8192)
    uploaded.seek(0)
    kind = filetype.guess(head)
    mime = kind.mime if kind else None
    allowed = ALLOWED[ext]
    if None in allowed:
        if b"\x00" in head:
            raise ValidationError("Text files must not contain binary data.", code="file_content")
        mime = "text/plain" if ext == "txt" else "text/csv"
    elif mime not in allowed:
        raise ValidationError("File content does not match its extension.", code="file_content")
    return {"name": name, "extension": ext, "mime": mime, "size": uploaded.size}


def tenant_upload_path(instance, filename: str) -> str:
    """organizations/<org-uuid>/<yyyy>/<uuid>.<ext> - no user-controlled path components."""
    from django.utils import timezone

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    ext = re.sub(r"[^a-z0-9]", "", ext)[:8] or "bin"
    return f"organizations/{instance.organization_id}/{timezone.now():%Y}/{uuid.uuid4().hex}.{ext}"
