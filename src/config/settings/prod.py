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
