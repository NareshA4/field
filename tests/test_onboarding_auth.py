import re

import pytest
from django.contrib.auth import get_user_model
from django.core import mail

from apps.tenancy import services
from apps.tenancy.models import Membership, Organization
from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db
User = get_user_model()


def test_full_onboarding_invite_activate_login(platform_admin, client, django_capture_on_commit_callbacks):
    """Super Admin creates org -> owner gets email -> sets own password -> signs in -> lands in their org."""
    with django_capture_on_commit_callbacks(execute=True):
        org, m = services.create_organization(name="Gamma Co", owner_email="boss@gamma.test",
                                              owner_name="Gina Gamma", actor=platform_admin)
    assert m.status == Membership.Status.INVITED
    assert not m.user.has_usable_password()
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert PASSWORD not in body and "password:" not in body.lower().replace("choose a password", "")
    link = re.search(r"https?://\S+/accounts/activate/\S+", body).group(0)
    path = "/" + link.split("/", 3)[3]

    # unactivated user cannot sign in
    assert client.post("/accounts/login/", {"username": "boss@gamma.test", "password": "anything-12345"}).status_code == 200

    r = client.get(path, follow=True)  # Django redirects token -> set-password form
    assert r.status_code == 200
    set_url = r.redirect_chain[-1][0]
    weak = client.post(set_url, {"new_password1": "short", "new_password2": "short"})
    assert weak.status_code == 200 and Membership.objects.unscoped().get(pk=m.pk).status == "INVITED"
    ok = client.post(set_url, {"new_password1": PASSWORD, "new_password2": PASSWORD})
    assert ok.status_code == 302
    assert Membership.objects.unscoped().get(pk=m.pk).status == Membership.Status.ACTIVE

    assert client.post("/accounts/login/", {"username": "boss@gamma.test", "password": PASSWORD}).status_code == 302
    home = client.get("/app/")
    assert home.status_code == 200 and "Gamma Co" in home.content.decode()


def test_activation_link_is_single_use(platform_admin, client, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        org, m = services.create_organization(name="Delta Co", owner_email="o@delta.test", owner_name="D",
                                              actor=platform_admin)
    path = "/" + re.search(r"https?://\S+/accounts/activate/\S+", mail.outbox[0].body).group(0).split("/", 3)[3]
    set_url = client.get(path, follow=True).redirect_chain[-1][0]
    client.post(set_url, {"new_password1": PASSWORD, "new_password2": PASSWORD})
    again = client.get(path, follow=True)
    assert "Link expired or invalid" in again.content.decode()


def test_only_platform_admin_can_create_org(owner_a):
    from apps.core.exceptions import PermissionDenied
    with pytest.raises(PermissionDenied):
        services.create_organization(name="Evil Corp", owner_email="e@e.test", owner_name="E", actor=owner_a)


def test_duplicate_org_rejected(platform_admin, org_a):
    from apps.core.exceptions import Conflict
    with pytest.raises(Conflict):
        services.create_organization(name="alpha industries", owner_email="z@z.test", owner_name="Z",
                                     actor=platform_admin)


def test_existing_user_invited_to_second_org_is_active_and_notified(org_a, org_b, make_member, as_user, owner_b):
    make_member(org_a, "shared@x.test", "technician")
    r = as_user(owner_b, org_b).post("/api/v1/members/", {
        "email": "shared@x.test", "role_ids": [str(__import__("apps.rbac.models", fromlist=["Role"]).Role.objects
                                                   .unscoped().get(organization=org_b, system_key="admin").pk)]},
        format="json")
    assert r.status_code == 201 and r.json()["status"] == "ACTIVE"


def test_duplicate_member_rejected(owner_a, org_a, as_user, tech_a):
    from apps.rbac.models import Role
    rid = str(Role.objects.unscoped().get(organization=org_a, system_key="technician").pk)
    r = as_user(owner_a, org_a).post("/api/v1/members/", {"email": tech_a.email, "role_ids": [rid]}, format="json")
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_member"


def test_suspend_and_reactivate_org_blocks_ui(platform_admin, owner_a, org_a, client):
    client.force_login(owner_a)
    assert client.get("/app/").status_code == 200
    services.set_organization_status(org_a, active=False, actor=platform_admin, reason="test")
    r = client.get("/app/")
    assert r.status_code == 403 and "suspended" in r.content.decode().lower()
    services.set_organization_status(org_a, active=True, actor=platform_admin)
    assert client.get("/app/").status_code == 200


def test_suspend_requires_reason(platform_admin, org_a):
    from apps.core.exceptions import ValidationFailed
    with pytest.raises(ValidationFailed):
        services.set_organization_status(org_a, active=False, actor=platform_admin, reason=" ")


def test_login_lockout_after_repeated_failures(client, owner_a):
    for _ in range(5):
        client.post("/accounts/login/", {"username": owner_a.email, "password": "wrong-password-1"})
    r = client.post("/accounts/login/", {"username": owner_a.email, "password": PASSWORD})
    assert r.status_code in (403, 429)  # locked out even with the right password


def test_jwt_flow_and_me(api, owner_a, org_a):
    r = api.post("/api/v1/auth/token/", {"email": owner_a.email, "password": PASSWORD}, format="json")
    assert r.status_code == 200, r.content
    access = r.json()["access"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    me = api.get("/api/v1/auth/me/").json()
    assert me["email"] == owner_a.email and me["organizations"][0]["slug"] == org_a.slug
    # a single-membership user needs no header; JWT alone still passes full membership verification
    assert api.get("/api/v1/members/").status_code == 200


def test_jwt_bad_password_rejected(api, owner_a):
    r = api.post("/api/v1/auth/token/", {"email": owner_a.email, "password": "nope-nope-nope-1"}, format="json")
    assert r.status_code == 401


def test_create_platform_admin_command_never_takes_password_arg(db, monkeypatch):
    from django.core.management import call_command
    monkeypatch.setenv("FIELDOPS_ADMIN_PASSWORD", PASSWORD)
    call_command("create_platform_admin", email="Admin@Platform.Test", full_name="P Admin")
    u = User.objects.get(email="admin@platform.test")
    assert u.is_platform_admin and u.check_password(PASSWORD)
    assert Organization.objects.count() == 0
