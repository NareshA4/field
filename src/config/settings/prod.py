import os
from .base import *  # noqa: F401,F403

DEBUG = False
# TLS terminates at the edge in production. The local HTTP verification stack sets these to false.
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)  # noqa: F405
_secure_cookies = env_bool("DJANGO_SECURE_COOKIES", True)  # noqa: F405
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = _secure_cookies
CSRF_COOKIE_SECURE = _secure_cookies
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365 if _secure_cookies else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = _secure_cookies
SECURE_HSTS_PRELOAD = _secure_cookies

# Render deployment automatic hosts & CSRF trust
render_hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
if render_hostname:
    if render_hostname not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(render_hostname)
    trusted = f"https://{render_hostname}"
    if trusted not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(trusted)

for default_origin in ("https://*.onrender.com", "https://feildops.onrender.com"):
    if default_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(default_origin)

for default_host in (".onrender.com", "feildops.onrender.com"):
    if default_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(default_host)
