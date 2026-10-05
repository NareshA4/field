from axes.signals import user_locked_out
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from apps.audit import services as audit


def _orgs_of(user):
    from apps.tenancy import selectors

    return [m.organization for m in selectors.active_memberships(user)] or [None]


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    # One row per organization the user belongs to, so each org's audit trail shows its members' sign-ins.
    for org in _orgs_of(user):
        audit.record("auth.login", actor=user, organization=org, target=user, target_repr=user.email,
                     request=request)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        for org in _orgs_of(user):
            audit.record("auth.logout", actor=user, organization=org, target=user, target_repr=user.email,
                         request=request)


@receiver(user_locked_out)
def on_lockout(sender, request, username, ip_address, **kwargs):
    audit.record("auth.lockout", metadata={"username": str(username)[:254], "ip": ip_address}, request=request)
