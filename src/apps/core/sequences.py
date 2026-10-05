"""Human-readable, never-repeating document numbers per organization (INC-000001, WO-000001)."""
from django.db import IntegrityError, transaction
from django.db.models import F

from .models import DocumentSequence


def next_number(org, key: str, prefix: str, width: int = 6) -> str:
    """Must run inside the caller's transaction: the row lock is held until commit, so two concurrent callers
    get different values; a rolled-back caller also rolls back the increment, so numbers stay gap-free."""
    qs = DocumentSequence.objects.for_organization(org).filter(key=key)
    if not qs.update(last_value=F("last_value") + 1):
        try:
            with transaction.atomic():
                DocumentSequence(organization=org, key=key, last_value=0).save()
        except IntegrityError:  # another transaction created it first
            pass
        qs.update(last_value=F("last_value") + 1)
    value = qs.values_list("last_value", flat=True).get()
    return f"{prefix}-{value:0{width}d}"
