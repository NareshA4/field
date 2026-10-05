from __future__ import annotations

from django.db import transaction

from .models import Notification


def notify(org, recipients, *, title: str, body: str = "", link: str = "", level: str = Notification.Level.INFO,
           source: str = "", email: bool = False) -> list[Notification]:
    """Creates in-app notifications for each recipient (users with an ACTIVE membership in ``org``);
    optionally also emails them through Celery. Recipients outside the organization are ignored."""
    from apps.tenancy.models import Membership

    ids = {getattr(r, "pk", r) for r in recipients}
    valid = Membership.objects.unscoped().filter(
        organization=org, user_id__in=ids, status=Membership.Status.ACTIVE
    ).select_related("user")
    created = []
    for m in valid:
        n = Notification(organization=org, recipient=m.user, title=title[:160], body=body, link=link[:300],
                         level=level, source=source[:60])
        n.save()
        created.append(n)
    if email and created:
        from .tasks import send_notification_email

        pks = [str(n.pk) for n in created]
        transaction.on_commit(lambda: send_notification_email.delay(pks))
    return created


def unread_count(org, user) -> int:
    return Notification.objects.for_organization(org).filter(recipient=user, read_at__isnull=True).count()


def mark_all_read(org, user) -> int:
    from django.utils import timezone

    return Notification.objects.for_organization(org).filter(recipient=user, read_at__isnull=True).update(
        read_at=timezone.now()
    )
