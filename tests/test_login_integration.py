"""End-to-end login regression suite (incident 2026-10-03, see docs/INCIDENT_2026-10-03_LOGIN.md).

These tests deliberately mimic a real browser: CSRF enforcement ON, GET-then-POST with the token and Origin header,
the production password hasher (PBKDF2, not the fast test hasher), a Super Admin created through the real
management command, several users sharing one IP, and an Nginx-style proxy in front.
"""
import re

import pytest
from axes.models import AccessAttempt
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, RequestFactory

from apps.core.net import axes_username, client_ip

pytestmark = pytest.mark.django_db
User = get_user_model()
PW = "Local-Test-Admin-Pw-2026"  # test-only value, never used outside the test database
OTHER_PW = "Another-Test-Pw-2026-Zz"


@pytest.fixture(autouse=True)
def production_hasher(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]


def browser(**environ):
    return Client(enforce_csrf_checks=True, HTTP_HOST="localhost", **environ)


def login(client, email, password, **extra):
    page = client.get("/accounts/login/")
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
    return client.post(
        "/accounts/login/",
        {"csrfmiddlewaretoken": token, "username": email, "password": password},
        HTTP_ORIGIN="http://localhost", HTTP_REFERER="http://localhost/accounts/login/", **extra,
    )


def make_admin(monkeypatch, email="superadmin@fieldops.test", password=PW):
    monkeypatch.setenv("FIELDOPS_ADMIN_PASSWORD", password)
    call_command("create_platform_admin", email=email, full_name="Platform Admin")
    return User.objects.get(email=email)


# --- the exact incident scenario ------------------------------------------------------------------------------


def test_command_created_super_admin_logs_in_via_browser_flow(monkeypatch):
    user = make_admin(monkeypatch)
    assert (user.is_platform_admin, user.is_staff, user.is_superuser) == (True, False, False)  # design preserved
    c = browser()
    r = login(c, "superadmin@fieldops.test", PW)
    assert r.status_code == 302 and r["Location"] == "/app/"
    assert "_auth_user_id" in c.session and c.session["_auth_user_id"] == str(user.pk)  # session created
    final = c.get("/app/", follow=True)
    assert final.redirect_chain[-1][0] == "/platform/organizations/" and final.status_code == 200
    assert c.get("/platform/organizations/").status_code == 200  # still authenticated after navigation
    assert c.get("/platform/audit/").status_code == 200


def test_wrong_password_is_rejected_without_session(monkeypatch):
    make_admin(monkeypatch)
    c = browser()
    r = login(c, "superadmin@fieldops.test", PW + "x")
    assert r.status_code == 200 and "Invalid email or password" in r.content.decode()
    assert "_auth_user_id" not in c.session


@pytest.mark.parametrize("typed", ["SuperAdmin@FieldOps.TEST", "  superadmin@fieldops.test  "])
def test_email_case_and_whitespace_are_normalised(monkeypatch, typed):
    make_admin(monkeypatch)
    assert login(browser(), typed, PW).status_code == 302


def test_unknown_user_gets_same_generic_error():
    r = login(browser(), "nobody@fieldops.test", PW)
    assert r.status_code == 200 and "Invalid email or password" in r.content.decode()


# --- django-axes integration (the defect) ----------------------------------------------------------------------


def test_axes_records_real_username_and_ip_not_none(monkeypatch):
    make_admin(monkeypatch)
    login(browser(REMOTE_ADDR="10.1.2.3"), "SuperAdmin@fieldops.test", "wrong-password-1")
    attempt = AccessAttempt.objects.get()
    assert attempt.username == "superadmin@fieldops.test"  # was NULL before the fix
    assert attempt.ip_address == "10.1.2.3"


def test_lockout_blocks_correct_password_with_429_and_friendly_page(monkeypatch):
    make_admin(monkeypatch)
    c = browser()
    for i in range(5):
        login(c, "superadmin@fieldops.test", f"wrong-password-{i}")
    r = login(c, "superadmin@fieldops.test", PW)  # correct password, but locked
    assert r.status_code == 429 and "Too many sign-in attempts" in r.content.decode()
    assert "_auth_user_id" not in c.session


def test_one_users_failures_do_not_lock_other_users_on_same_ip(monkeypatch):
    """Regression for the incident: failures were keyed by IP only, so five bad attempts by anyone (behind Nginx:
    everyone) locked everybody out."""
    make_admin(monkeypatch)
    make_admin(monkeypatch, email="second.admin@fieldops.test", password=OTHER_PW)
    attacker = browser()
    for i in range(6):
        login(attacker, "superadmin@fieldops.test", f"wrong-password-{i}")
    assert login(browser(), "superadmin@fieldops.test", PW).status_code == 429  # target is locked...
    assert login(browser(), "second.admin@fieldops.test", OTHER_PW).status_code == 302  # ...bystander is not
    assert login(browser(), "never.registered@fieldops.test", "x").status_code == 200  # unknown users just fail


def test_lockout_is_per_username_and_ip_combination(monkeypatch):
    make_admin(monkeypatch)
    for i in range(5):
        login(browser(REMOTE_ADDR="10.0.0.1"), "superadmin@fieldops.test", f"wrong-password-{i}")
    assert login(browser(REMOTE_ADDR="10.0.0.1"), "superadmin@fieldops.test", PW).status_code == 429
    assert login(browser(REMOTE_ADDR="10.0.0.2"), "superadmin@fieldops.test", PW).status_code == 302


def test_case_variants_cannot_dodge_the_failure_counter(monkeypatch):
    make_admin(monkeypatch)
    for i, variant in enumerate(["SUPERADMIN@fieldops.test", "Superadmin@fieldops.test", "superadmin@FIELDOPS.test",
                                 "superadmin@fieldops.TEST", "SuperAdmin@fieldops.test"]):
        login(browser(), variant, f"wrong-password-{i}")
    assert login(browser(), "superadmin@fieldops.test", PW).status_code == 429


# --- reverse proxy (Nginx) handling ---------------------------------------------------------------------------


def test_behind_trusted_proxy_throttling_uses_the_real_client_ip(monkeypatch, settings):
    settings.TRUSTED_PROXY_COUNT = 1
    make_admin(monkeypatch)
    proxy = {"REMOTE_ADDR": "172.21.0.7"}  # what Django sees: the Nginx container
    for i in range(5):
        login(browser(**proxy), "superadmin@fieldops.test", f"wrong-password-{i}", HTTP_X_FORWARDED_FOR="203.0.113.10")
    assert AccessAttempt.objects.get().ip_address == "203.0.113.10"
    assert login(browser(**proxy), "superadmin@fieldops.test", PW, HTTP_X_FORWARDED_FOR="203.0.113.10").status_code == 429
    # a different real client behind the same proxy is unaffected
    assert login(browser(**proxy), "superadmin@fieldops.test", PW, HTTP_X_FORWARDED_FOR="203.0.113.20").status_code == 302


def test_client_supplied_forwarded_for_cannot_spoof_identity(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    req = RequestFactory().get("/", REMOTE_ADDR="172.21.0.7", HTTP_X_FORWARDED_FOR="1.1.1.1, 203.0.113.10")
    assert client_ip(req) == "203.0.113.10"  # the hop our proxy appended, not the forged left-most value
    settings.TRUSTED_PROXY_COUNT = 0
    assert client_ip(req) == "172.21.0.7"  # no trusted proxy configured: header ignored entirely


def test_client_ip_ignores_garbage_headers(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    req = RequestFactory().get("/", REMOTE_ADDR="172.21.0.7", HTTP_X_FORWARDED_FOR="not-an-ip")
    assert client_ip(req) == "172.21.0.7"


def test_axes_username_normalisation():
    req = RequestFactory().post("/accounts/login/", {"username": " A@B.test "})
    assert axes_username(req) == "a@b.test"
    assert axes_username(req, {"username": "X@Y.test"}) == "x@y.test"
    assert axes_username(RequestFactory().post("/accounts/login/", {})) is None


def test_axes_settings_match_the_credential_key_django_passes(settings):
    assert settings.AXES_USERNAME_FORM_FIELD == "username"
    assert settings.AXES_LOCKOUT_PARAMETERS == [["username", "ip_address"]]


# --- authorization after login ------------------------------------------------------------------------------


def test_tenant_owner_lands_in_org_and_cannot_reach_platform(owner_a):
    from tests.conftest import PASSWORD

    owner_a.set_password(PASSWORD)
    owner_a.save()
    c = browser()
    r = login(c, owner_a.email, PASSWORD)
    assert r.status_code == 302 and r["Location"] == "/app/"
    assert c.get("/app/").status_code == 200
    assert c.get("/platform/organizations/").status_code == 403
    assert (owner_a.is_platform_admin, owner_a.is_staff, owner_a.is_superuser) == (False, False, False)


def test_super_admin_has_no_tenant_access_after_login(monkeypatch, org_a):
    make_admin(monkeypatch)
    c = browser()
    login(c, "superadmin@fieldops.test", PW)
    assert c.get("/app/users/").status_code in (302, 403)
    assert c.get("/app/users/", follow=True).redirect_chain[-1][0] == "/platform/organizations/"


# --- diagnostic tool -------------------------------------------------------------------------------------------


def test_diagnose_login_command_reports_each_stage(monkeypatch, capsys):
    make_admin(monkeypatch)
    target = "apps.accounts.management.commands.diagnose_login.getpass.getpass"
    monkeypatch.setattr(target, lambda *a, **k: PW)
    call_command("diagnose_login", email="superadmin@fieldops.test")
    out = capsys.readouterr().out
    assert "password matches stored: True" in out and "authenticate() result  : OK" in out
    monkeypatch.setattr(target, lambda *a, **k: PW + "!")
    call_command("diagnose_login", email="superadmin@fieldops.test")
    out = capsys.readouterr().out
    assert "password matches stored: False" in out and "REJECTED" in out
    assert PW not in out  # the typed password is never echoed
