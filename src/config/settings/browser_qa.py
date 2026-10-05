"""Isolated LOCAL browser-acceptance settings: Docker Postgres/Redis only. Refuses to start on any other database."""
import os
from urllib.parse import urlparse

from .dev import *  # noqa: F401,F403

_db = urlparse(os.environ["DATABASE_URL"])
if _db.hostname not in {"localhost", "127.0.0.1"} or _db.path.lstrip("/") != "fieldops_browser_qa":
    raise RuntimeError("browser_qa settings only run against the local fieldops_browser_qa database.")
DEBUG = False  # exercise real error pages / CSP like a deployed build; static served by runserver --insecure
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
MEDIA_ROOT = BASE_DIR.parent / ".browser_qa_media"  # noqa: F405
