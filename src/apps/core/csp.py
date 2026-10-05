"""Content-Security-Policy (audit finding S-1). A fresh nonce per request is exposed as ``request.csp_nonce`` and,
through ``apps.core.csp.csp_nonce``, as ``{{ csp_nonce }}`` for the few inline ``<script>`` blocks. Inline event
handler attributes are not allowed (they were moved into ``static/js/app.js``); inline ``style`` attributes are
tolerated (``style-src 'unsafe-inline'``) because they cannot execute script."""
import secrets

POLICY = (
    "default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'"
)


# The Swagger UI page (signed-in users only) loads its assets from a CDN and runs one inline script; it gets its own,
# looser policy. Every other page uses POLICY.
DOCS_POLICY = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://cdn.jsdelivr.net; "
    "font-src 'self' https://cdn.jsdelivr.net; connect-src 'self'; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'"
)


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(16)
        response = self.get_response(request)
        if "Content-Security-Policy" not in response:
            policy = DOCS_POLICY if request.path.startswith("/api/v1/docs/") else POLICY.format(nonce=request.csp_nonce)
            response["Content-Security-Policy"] = policy
        # the M12 scanner needs the camera on our own pages only (never in embedded third-party content)
        response.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        return response


def csp_nonce(request):
    return {"csp_nonce": getattr(request, "csp_nonce", "")}
