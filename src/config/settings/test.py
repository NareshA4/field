"""Test settings. Uses a dedicated PostgreSQL database (Django prefixes it with test_)."""
import os
import secrets

os.environ.setdefault("DJANGO_SECRET_KEY", secrets.token_urlsafe(50))  # ephemeral, never persisted
os.environ.setdefault("DATABASE_URL", "postgres://fieldops:fieldops@localhost:5432/fieldops")

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
