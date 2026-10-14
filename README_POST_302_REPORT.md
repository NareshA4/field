# HTTP 302 Redirect Audit on POST Methods

Full audit report available at [`docs/POST_METHOD_302_AUDIT_REPORT.md`](file:///c:/Users/win10/Desktop/fieldops/FieldOps_Nexus_Internal/docs/POST_METHOD_302_AUDIT_REPORT.md).

## Quick Summary

1. **REST API Tier (`/api/v1/...`)**:
   - **0 modules intentionally return 302 on POST.**
   - All REST API endpoints return `200 OK`, `201 Created`, or 4xx error envelopes.
   - If you receive a 302 on an API call, verify that you included the **trailing slash** (e.g., `POST /api/v1/work-orders/`) and are targeting `/api/v1/...` rather than `/app/...`.

2. **Web UI Tier (`/app/...`)**:
   - **All UI modules return 302 on POST** upon successful completion (Django Post/Redirect/Get pattern).
   - Unauthenticated requests to `/app/...` also return 302 redirecting to `/accounts/login/?next=...`.

Refer to [`docs/POST_METHOD_302_AUDIT_REPORT.md`](file:///c:/Users/win10/Desktop/fieldops/FieldOps_Nexus_Internal/docs/POST_METHOD_302_AUDIT_REPORT.md) for the complete endpoint-by-endpoint breakdown across M01 through M15.
