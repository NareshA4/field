import uuid

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.views.decorators.http import require_GET

from apps.rbac import services as rbac

from . import access
from .models import Attachment


@login_required
@require_GET
def download(request, pk):
    """Tenant-scoped (default manager) + per-attachment read permission; always served as a download."""
    if request.membership is None:
        raise PermissionDenied
    try:
        att = Attachment.objects.for_organization(request.organization).get(pk=uuid.UUID(str(pk)))
    except (Attachment.DoesNotExist, ValueError) as exc:
        raise Http404 from exc
    checker = access.checker_for(f"{att.content_type.app_label}.{att.content_type.model}")
    if checker is not None:
        target = att.target
        if target is None or not access.allowed(checker, request.membership, target, att):
            raise Http404  # out of scope looks the same as absent
    elif att.read_permission and not rbac.has_permission(request.membership, att.read_permission):
        raise PermissionDenied
    response = FileResponse(att.file.open("rb"), as_attachment=True, filename=att.original_name,
                            content_type="application/octet-stream")
    response["X-Content-Type-Options"] = "nosniff"
    return response
