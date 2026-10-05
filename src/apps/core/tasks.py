from celery import shared_task
from django.core.management import call_command


@shared_task(name="apps.core.tasks.clear_expired_sessions")
def clear_expired_sessions():
    call_command("clearsessions")
