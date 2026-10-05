import io

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test.utils import override_settings

from apps.core.exceptions import InvalidTransition, ValidationFailed
from apps.core.uploads import clean_display_name, validate_upload
from apps.core.workflow import StateMachine, Transition
from apps.files import services as files
from apps.rbac.models import Role

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82")

WO = StateMachine("wo", ("DRAFT", "PLANNED", "CLOSED"), [
    Transition("plan", ("DRAFT",), "PLANNED"),
    Transition("close", ("PLANNED",), "CLOSED", guard=lambda o, **kw: (_ for _ in ()).throw(
        ValidationFailed("notes required")) if not kw.get("notes") else None),
])


class Obj:
    status = "DRAFT"


def test_state_machine_valid_and_invalid():
    o = Obj()
    assert WO.apply(o, "plan") == ("DRAFT", "PLANNED") and o.status == "PLANNED"
    with pytest.raises(InvalidTransition):
        WO.apply(o, "plan")
    with pytest.raises(ValidationFailed):
        WO.apply(o, "close")
    assert o.status == "PLANNED"  # guard failure leaves state untouched
    WO.apply(o, "close", notes="done")
    assert o.status == "CLOSED" and WO.available("CLOSED") == []


def test_state_machine_rejects_unknown_states():
    with pytest.raises(ValueError):
        StateMachine("x", ("A",), [Transition("go", ("A",), "B")])


def test_upload_accepts_real_png():
    meta = validate_upload(SimpleUploadedFile("photo.png", PNG))
    assert meta["mime"] == "image/png"


def test_upload_rejects_disguised_content():
    with pytest.raises(ValidationError):
        validate_upload(SimpleUploadedFile("evil.png", b"MZ\x90\x00 not an image"))


def test_upload_rejects_disallowed_extension_and_size_and_empty():
    with pytest.raises(ValidationError):
        validate_upload(SimpleUploadedFile("run.exe", b"MZ"))
    with pytest.raises(ValidationError):
        validate_upload(SimpleUploadedFile("a.txt", b""))
    with override_settings(UPLOAD_MAX_BYTES=10), pytest.raises(ValidationError):
        validate_upload(SimpleUploadedFile("a.txt", b"x" * 11))


def test_upload_name_sanitised():
    assert "/" not in clean_display_name("../../etc/passwd") and ".." not in clean_display_name("a/../b.png").split("/")


@pytest.mark.django_db
def test_attach_stores_hash_and_blocks_cross_tenant(org_a, org_b, owner_a, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    role_a = Role.objects.unscoped().filter(organization=org_a).first()
    role_b = Role.objects.unscoped().filter(organization=org_b).first()
    att = files.attach(SimpleUploadedFile("p.png", PNG), target=role_a, organization=org_a, user=owner_a)
    assert len(att.sha256) == 64 and att.file.name.startswith(f"organizations/{org_a.pk}/")
    assert "p.png" not in att.file.name  # client name never used in the storage path
    with pytest.raises(ValidationFailed):
        files.attach(SimpleUploadedFile("p.png", PNG), target=role_b, organization=org_a, user=owner_a)


@pytest.mark.django_db
def test_download_scoped_and_permission_gated(client, org_a, org_b, owner_a, tech_a, owner_b, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    role_a = Role.objects.unscoped().filter(organization=org_a).first()
    open_att = files.attach(SimpleUploadedFile("p.png", PNG), target=role_a, organization=org_a, user=owner_a)
    gated = files.attach(SimpleUploadedFile("q.png", PNG), target=role_a, organization=org_a, user=owner_a,
                         read_permission="audit.view")
    client.force_login(tech_a)
    assert client.get(f"/app/files/{open_att.pk}/download/").status_code == 200
    assert client.get(f"/app/files/{gated.pk}/download/").status_code == 403
    client.force_login(owner_b)
    assert client.get(f"/app/files/{open_att.pk}/download/").status_code == 404
    client.force_login(owner_a)
    r = client.get(f"/app/files/{gated.pk}/download/")
    assert r.status_code == 200 and "attachment" in r["Content-Disposition"] and r["X-Content-Type-Options"] == "nosniff"
    io.BytesIO(b"".join(r.streaming_content))


@pytest.mark.django_db
def test_health_endpoints(client):
    assert client.get("/health/live/").json() == {"status": "ok"}
    r = client.get("/health/ready/")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"


@pytest.mark.django_db
def test_openapi_schema_generates_for_signed_in_users_only(client, owner_a):
    assert client.get("/api/v1/schema/").status_code in (401, 403)  # not anonymous (audit decision)
    assert client.get("/api/v1/docs/").status_code in (401, 403)
    client.force_login(owner_a)
    r = client.get("/api/v1/schema/")
    assert r.status_code == 200 and b"/api/v1/members/" in r.content
    docs = client.get("/api/v1/docs/")
    assert docs.status_code == 200 and "cdn.jsdelivr.net" in docs["Content-Security-Policy"]  # docs page only


@pytest.mark.django_db
def test_api_error_envelope_shape(api):
    err = api.get("/api/v1/members/").json()["error"]
    assert set(err) == {"code", "message", "details", "request_id"}


def test_celery_registered_tasks():
    from config.celery import app
    app.loader.import_default_modules()
    for name in ("apps.core.tasks.clear_expired_sessions", "apps.accounts.tasks.send_invitation_email",
                 "apps.notifications.tasks.send_notification_email"):
        assert name in app.tasks
    from django.conf import settings
    assert "clear-expired-sessions" in settings.CELERY_BEAT_SCHEDULE


@pytest.mark.django_db
def test_content_security_policy_header_and_nonce(client, owner_a):
    r = client.get("/accounts/login/")
    csp = r["Content-Security-Policy"]
    assert "unsafe-eval" not in csp and "connect-src 'self'" in csp  # HTMX XHR stays same-origin
    assert "script-src 'self' 'nonce-" in csp and "frame-ancestors 'none'" in csp and "'unsafe-inline'" not in csp.split("style-src")[0]
    nonce = csp.split("'nonce-")[1].split("'")[0]
    other = client.get("/accounts/login/")["Content-Security-Policy"]
    assert f"'nonce-{nonce}'" not in other  # a fresh nonce per response
    client.force_login(owner_a)
    page = client.get("/app/organization/")
    body = page.content.decode()
    assert "onclick=" not in body and "<script>" not in body  # inline handlers/scripts are gone or carry the nonce
    pnonce = page["Content-Security-Policy"].split("'nonce-")[1].split("'")[0]
    assert all(f'nonce="{pnonce}"' in tag for tag in __import__("re").findall(r"<script(?![^>]*\bsrc=)[^>]*>", body))



@pytest.mark.django_db
def test_htmx_and_static_assets_still_load_under_the_policy(client, owner_a):
    from django.contrib.staticfiles import finders
    client.force_login(owner_a)
    body = client.get("/app/").content.decode()
    for asset in ("lib/htmx.min.js", "lib/bootstrap.bundle.min.js", "js/app.js"):
        assert f"{asset}" in body and finders.find(asset), asset  # same-origin scripts (allowed by script-src 'self')
    # an HTMX fragment request is answered normally and carries the same policy (no inline script in fragments)
    r = client.get("/app/search/?q=ow", HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and "Content-Security-Policy" in r
    assert "<script" not in r.content.decode()
