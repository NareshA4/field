"""Test settings. Uses a dedicated PostgreSQL database (Django prefixes it with test_)."""
import os
import secrets

os.environ.setdefault("DJANGO_SECRET_KEY", secrets.token_urlsafe(50))  # ephemeral, never persisted

from pathlib import Path

_env_file = Path(__file__).resolve().parent.parent.parent.parent / ".env"
_pg_pass = os.environ.get("POSTGRES_PASSWORD")
if not _pg_pass and _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        if _line.startswith("POSTGRES_PASSWORD="):
            _pg_pass = _line.split("=", 1)[1].strip()
            break
_pg_pass = _pg_pass or "McEXD9PVj4TKfk31GaBHYlAA14pXAhFLvlhJZuYA0BU"
os.environ.setdefault("DATABASE_URL", f"postgres://fieldops:{_pg_pass}@localhost:5432/fieldops")

from .base import *  # noqa: E402,F401,F403

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # speed only; tests only
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
MEDIA_ROOT = BASE_DIR.parent / ".pytest_media"  # noqa: F405
ALLOWED_HOSTS = ["testserver", "localhost"]
REST_FRAMEWORK = {  # noqa: F405
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {"anon": "10000/min", "user": "10000/min", "login": "10000/min"},
}
LOGGING["root"]["level"] = "WARNING"  # noqa: F405
MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]  # noqa: F405
WORKORDER_PROVISIONAL_CHECKLIST_ENABLED = True
