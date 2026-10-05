from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@require_GET
@never_cache
def live(request):
    """Liveness: the process is up and serving."""
    return JsonResponse({"status": "ok"})


@require_GET
@never_cache
def ready(request):
    """Readiness: dependencies (PostgreSQL, cache/Redis) answer."""
    checks = {}
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
    try:
        cache.set("health:probe", "1", 10)
        checks["cache"] = "ok" if cache.get("health:probe") == "1" else "error"
    except Exception:
        checks["cache"] = "error"
    healthy = all(v == "ok" for v in checks.values())
    return JsonResponse({"status": "ok" if healthy else "degraded", "checks": checks},
                        status=200 if healthy else 503)
