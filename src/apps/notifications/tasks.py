from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

from apps.core.tenant import tenant_context

from .models import Notification


@shared_task(name="apps.notifications.tasks.send_notification_email", autoretry_for=(OSError,),
             retry_backoff=True, max_retries=5)
def send_notification_email(notification_ids: list[str]):
    for n in Notification.objects.filter(pk__in=notification_ids).select_related("recipient", "organization"):
        with tenant_context(n.organization):
            link = f"\n\n{settings.SITE_BASE_URL.rstrip('/')}{n.link}" if n.link else ""
            send_mail(
                subject=f"[{n.organization.name}] {n.title}",
                message=f"{n.body}{link}".strip() or n.title,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[n.recipient.email],
            )
