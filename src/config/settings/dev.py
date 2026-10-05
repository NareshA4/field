from .base import *  # noqa: F401,F403

DEBUG = True
INTERNAL_IPS = ["127.0.0.1"]
STORAGES = {  # no manifest build step needed in development
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
