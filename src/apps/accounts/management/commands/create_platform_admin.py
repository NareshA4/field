import getpass
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Creates a Platform Super Admin (is_platform_admin only; no Django staff/superuser flags). The password is "
        "read from the hidden interactive prompt (asked twice) or, for automation, FIELDOPS_ADMIN_PASSWORD; it is "
        "never accepted on the command line. Prefer the prompt: run with a TTY (omit -T) so shell quoting cannot "
        "alter what you type."
    )

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--full-name", required=True)

    def handle(self, *args, **opts):
        User = get_user_model()
        email = opts["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise CommandError("A user with this email already exists.")
        password = os.environ.get("FIELDOPS_ADMIN_PASSWORD")
        if not password:
            password = getpass.getpass("Password (input hidden): ")
            if password != getpass.getpass("Password (again): "):
                raise CommandError("Passwords do not match.")
        validate_password(password)
        User.objects.create_user(email=email, password=password, full_name=opts["full_name"],
                                 is_platform_admin=True)
        self.stdout.write(self.style.SUCCESS(
            f"Platform admin {email} created (password length {len(password)}). "
            f"If sign-in is ever rejected, run: python manage.py diagnose_login --email {email}"))
