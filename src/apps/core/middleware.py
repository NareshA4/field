import re
import time
import uuid

from . import logging as flogging

_VALID_ID = re.compile(r"^[A-Za-z0-9\-_.]{8,64}$")


class RequestIdMiddleware:
    """Assigns/propagates X-Request-ID and emits one structured access-log line per request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        import logging

        incoming = request.headers.get("X-Request-ID", "")
        rid = incoming if _VALID_ID.match(incoming) else uuid.uuid4().hex
        request.request_id = rid
        token = flogging.request_id_var.set(rid)
        started = time.monotonic()
        try:
            response = self.get_response(request)
        finally:
            elapsed = int((time.monotonic() - started) * 1000)
            flogging.request_id_var.reset(token)
        response["X-Request-ID"] = rid
        logging.getLogger("fieldops.access").info(
            "%s %s -> %s (%d ms)", request.method, request.path, response.status_code, elapsed,
            extra={"request_id": rid},
        )
        return response
