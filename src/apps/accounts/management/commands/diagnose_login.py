import getpass

from django.contrib.auth import authenticate, get_user_model
from django.core.management.base import BaseCommand
from django.test import RequestFactory

from apps.core.net import axes_username, client_ip


class Command(BaseCommand):
    help = (
        "Interactive login diagnostic. Prompts for the password (hidden, never logged or accepted as an "
        "argument, so shell quoting cannot alter it) and runs the REAL authenticate() pipeline, reporting which "
        "stage rejects. Run with a TTY: docker compose exec web python manage.py diagnose_login --email you@x.com"
    )

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)

    def handle(self, *args, **opts):
        email = opts["email"]
        pw = getpass.getpass("Password (input hidden): ")
        User = get_user_model()
        user = User.objects.filter(email__iexact=email.strip()).first()
        out = self.stdout.write
        out(f"1. user exists            : {bool(user)}")
        if user:
            out(f"2. is_active              : {user.is_active}")
            out(f"3. has usable password    : {user.has_usable_password()}")
            out(f"4. password matches stored: {user.check_password(pw)}  (typed length {len(pw)})")
            out(f"   flags                  : platform_admin={user.is_platform_admin} staff={user.is_staff} superuser={user.is_superuser}")
        request = RequestFactory().post("/accounts/login/", {"username": email}, REMOTE_ADDR="127.0.0.1")
        request.session = {}
        out(f"5. axes identity          : username={axes_username(request)!r} ip={client_ip(request)!r}")
        result = authenticate(request, username=email.strip().lower(), password=pw)
        out(f"6. authenticate() result  : {'OK -> ' + result.email if result else 'REJECTED'}")
        if result:
            out(self.style.SUCCESS("Backend accepts these credentials. If the browser still rejects them, the browser is sending different text (autofill / typo / layout)."))
        elif user and not user.check_password(pw):
            out(self.style.ERROR("The stored password differs from what you typed. Reset with: python manage.py changepassword <email>"))
        else:
            out(self.style.ERROR("Rejected by Axes lockout, inactive account, or unknown user (see lines above)."))
