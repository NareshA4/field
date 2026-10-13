# Comprehensive Audit Report: HTTP 302 Redirects on POST Methods Across Modules

**Project:** FieldOps Nexus ERP (`HPE-PRD-2026-FOPS02`)  
**Audit Scope:** Analysis of all modules for HTTP 302 responses on `POST` requests  
**Date:** October 13, 2026  
**Status:** Complete  

---

## 1. Executive Summary & Core Architectural Finding

In the FieldOps Nexus ERP architecture, requests are strictly divided into two distinct endpoint tiers:

1. **REST API Tier (`/api/v1/...`)**:
   * **Zero (0) REST API endpoints intentionally return HTTP 302.**
   * All API controllers are built with Django REST Framework (DRF) viewsets and API views (`api_views.py`), which return JSON responses with standard REST HTTP status codes:
     * `201 Created` for resource creations
     * `200 OK` for mutations, transitions, or retrievals
     * `400 Bad Request` for validation failures
     * `401 Unauthorized` / `403 Forbidden` for auth or RBAC failures
     * `404 Not Found` for missing or out-of-tenant entities
     * `409 Conflict` for state machine or closure conflicts
   * **When an API client receives HTTP 302 on POST:**
     * **Missing Trailing Slash (Django `APPEND_SLASH`):** A request sent to an endpoint lacking a trailing slash (e.g., `POST /api/v1/work-orders`) triggers Django's `CommonMiddleware` to issue an HTTP 301/302 redirect to the slash-terminated route (`/api/v1/work-orders/`).
     * **Calling UI URLs instead of API URLs:** Sending API payloads to `/app/...` endpoints instead of `/api/v1/...`.

2. **Web UI Tier (`/app/...` and `/accounts/...`)**:
   * **All Web UI controllers return HTTP 302 on successful `POST`.**
   * These views (`views.py` / `ui_views.py`) implement the standard **Post/Redirect/Get (PRG)** design pattern: after a form POST mutates data, the server returns an `HTTP 302 Found` redirecting the browser to a GET view (typically the entity detail or list page). This prevents accidental duplicate submissions on browser refresh.
   * **Unauthenticated Requests:** Any unauthenticated POST to an `/app/...` URL triggers `LoginRequiredMixin` or `AuthenticationMiddleware` to return an `HTTP 302 Found` redirecting to `LOGIN_URL` (`/accounts/login/?next=...`).

---

## 2. Cross-Module Architectural Matrix

| Module ID | Module Name | Django App | Web UI Tier (`/app/...`) POST Status | REST API Tier (`/api/v1/...`) POST Status |
| :---: | :--- | :--- | :---: | :---: |
| **M01** | Site & Location Master | `apps.sites` | **302 Redirect** (PRG) | **201 / 200** (JSON) |
| **M02** | Asset Registry | `apps.assets` | **302 Redirect** (PRG) | **201 / 200** (JSON) |
| **M03** | Asset Hierarchy | `apps.assets` | **302 Redirect** (PRG) | **201 / 200** (JSON) |
| **M04** | Preventive Maintenance | `apps.maintenance` | *N/A (Cron / API driven)* | **201 / 200** (JSON) |
| **M05** | Incident & Breakdown | `apps.incidents` | **302 Redirect** (PRG) | **201 / 200** (JSON) |
| **M06** | Work Order Management | `apps.workorders` | **302 Redirect** (PRG) | **201 / 200** (JSON) |
| **M07** | Technician Workspace | `apps.workspace` | **302 Redirect** (PRG) | **201 / 200** (JSON) |
| **M08** | Inspection Checklists | `apps.checklists` | **302 Redirect** (PRG) | **201 / 200** (JSON) |
| **M09** | Spare Parts & Inventory | `apps.inventory` | **302 Redirect** (PRG) | **201 / 200** (JSON) |
| **M10** | Contracts & Coverage | `apps.contracts` | **302 Redirect** (PRG) | **201 / 200** (JSON) |
| **M11** | SLA & Escalation | `apps.sla` | **302 Redirect** (PRG) | **201 / 200** (JSON) |
| **M12** | QR & Barcode Identifiers | `apps.identification` | **302 Redirect** (PRG) | **201 / 200** (JSON) |
| **M13** | Client Portal | `apps.portal` | **302 Redirect** (PRG) | **201 / 200** (JSON) |
| **M14** | Operational Dashboards | `apps.dashboards` | *Read-only (GET)* | *Read-only (GET)* |
| **M15** | Audit & Compliance | `apps.audit` | *Read-only (GET)* | *Read-only (GET)* |
| **Core** | Auth, Tenancy, RBAC | `apps.accounts`, `tenancy`, `rbac` | **302 Redirect** (PRG) | **201 / 200** (JSON) |

---

## 3. Deep-Dive by Module: Endpoints Returning HTTP 302 on POST

### Module 07: Technician Workspace (`apps.workspace`)
* **Source:** `src/apps/workspace/views.py`
* **Web UI Endpoints returning 302 on POST:**
  * `POST /app/workspace/jobs/<pk>/start/` &rarr; `302 Found` (redirects to `workspace:job_detail`)
  * `POST /app/workspace/jobs/<pk>/pause/` &rarr; `302 Found` (redirects to `workspace:job_detail`)
  * `POST /app/workspace/jobs/<pk>/resume/` &rarr; `302 Found` (redirects to `workspace:job_detail`)
  * `POST /app/workspace/jobs/<pk>/travel/` &rarr; `302 Found` (redirects to `workspace:job_detail`)
  * `POST /app/workspace/jobs/<pk>/labor/` &rarr; `302 Found` (redirects to `workspace:job_detail`)
  * `POST /app/workspace/jobs/<pk>/materials/` &rarr; `302 Found` (redirects to `workspace:job_detail`)
  * `POST /app/workspace/jobs/<pk>/evidence/` &rarr; `302 Found` (redirects to `workspace:job_detail`)
  * `POST /app/workspace/jobs/<pk>/complete/` &rarr; `302 Found` (redirects to `workspace:job_detail` on error, or `workspace:my_jobs` on completion)
* *Note: When called with `HX-Request: true` header (HTMX), drawer actions return `200 OK` with HTML partial fragments.*
* **Equivalent REST API (Returns 200/201 JSON, Never 302):**
  * `POST /api/v1/workspace/jobs/<pk>/start/` &rarr; `200 OK`
  * `POST /api/v1/workspace/jobs/<pk>/pause/` &rarr; `200 OK`
  * `POST /api/v1/workspace/jobs/<pk>/resume/` &rarr; `200 OK`
  * `POST /api/v1/workspace/jobs/<pk>/complete/` &rarr; `200 OK`
  * `POST /api/v1/workspace/jobs/<pk>/travel/` &rarr; `201 Created`
  * `POST /api/v1/workspace/jobs/<pk>/labor/` &rarr; `201 Created`
  * `POST /api/v1/workspace/jobs/<pk>/materials/` &rarr; `201 Created`
  * `POST /api/v1/workspace/jobs/<pk>/notes/` &rarr; `201 Created`

---

### Module 06: Work Order Management (`apps.workorders`)
* **Source:** `src/apps/workorders/ui_views.py`
* **Web UI Endpoints returning 302 on POST:**
  * `POST /app/work-orders/create/` &rarr; `302 Found` (redirects to `workorders:detail`)
  * `POST /app/work-orders/<pk>/edit/` &rarr; `302 Found` (redirects to `workorders:detail`)
  * `POST /app/work-orders/<pk>/actions/<operation>/` &rarr; `302 Found` (start, hold, resume, complete, cancel, review)
  * `POST /app/work-orders/<pk>/labor/` &rarr; `302 Found` (redirects to `workorders:detail?tab=labor`)
  * `POST /app/work-orders/<pk>/materials/` &rarr; `302 Found` (redirects to `workorders:detail?tab=parts`)
  * `POST /app/work-orders/<pk>/evidence/` &rarr; `302 Found` (redirects to `workorders:detail?tab=evidence`)
  * `POST /app/work-orders/schedule/plan/` &rarr; `302 Found` (redirects to `workorders:schedule`)
  * `POST /app/work-orders/schedule/assign/` &rarr; `302 Found` (redirects to `workorders:schedule`)
  * `POST /app/work-orders/schedule/dispatch/` &rarr; `302 Found` (redirects to `workorders:schedule`)
* **Equivalent REST API (Returns 200/201 JSON, Never 302):**
  * `POST /api/v1/work-orders/` &rarr; `201 Created`
  * `POST /api/v1/work-orders/<pk>/plan/` &rarr; `200 OK`
  * `POST /api/v1/work-orders/<pk>/assign/` &rarr; `200 OK`
  * `POST /api/v1/work-orders/<pk>/dispatch/` &rarr; `200 OK`
  * `POST /api/v1/work-orders/<pk>/start/` &rarr; `200 OK`
  * `POST /api/v1/work-orders/<pk>/hold/` &rarr; `200 OK`
  * `POST /api/v1/work-orders/<pk>/resume/` &rarr; `200 OK`
  * `POST /api/v1/work-orders/<pk>/complete/` &rarr; `200 OK`
  * `POST /api/v1/work-orders/<pk>/close/` &rarr; `200 OK`

---

### Module 05: Incident & Breakdown Management (`apps.incidents`)
* **Source:** `src/apps/incidents/views.py`
* **Web UI Endpoints returning 302 on POST:**
  * `POST /app/incidents/create/` &rarr; `302 Found` (redirects to `incidents:detail`)
  * `POST /app/incidents/<pk>/edit/` &rarr; `302 Found` (redirects to `incidents:detail`)
  * `POST /app/incidents/<pk>/actions/<action>/` &rarr; `302 Found` (triage, approve, reject, resolve, confirm, close)
  * `POST /app/incidents/<pk>/create-work-order/` &rarr; `302 Found` (redirects to `workorders:detail`)
* **Equivalent REST API (Returns 200/201 JSON, Never 302):**
  * `POST /api/v1/service-requests/` &rarr; `201 Created`
  * `POST /api/v1/service-requests/<pk>/triage/` &rarr; `200 OK`
  * `POST /api/v1/service-requests/<pk>/approve/` &rarr; `200 OK`
  * `POST /api/v1/service-requests/<pk>/resolve/` &rarr; `200 OK`

---

### Module 08: Inspection Checklists (`apps.checklists`)
* **Source:** `src/apps/checklists/views.py`
* **Web UI Endpoints returning 302 on POST:**
  * `POST /app/checklists/templates/create/` &rarr; `302 Found` (redirects to `checklists:detail`)
  * `POST /app/checklists/templates/<pk>/edit/` &rarr; `302 Found` (redirects to `checklists:detail`)
  * `POST /app/checklists/templates/<pk>/activate/` &rarr; `302 Found` (redirects to `checklists:detail`)
  * `POST /app/checklists/templates/<pk>/new-version/` &rarr; `302 Found` (redirects to `checklists:detail`)
  * `POST /app/checklists/inspections/create/` &rarr; `302 Found` (redirects to `checklists:inspection`)
  * `POST /app/checklists/inspections/<pk>/run/` &rarr; `302 Found` (redirects to `checklists:inspection` when completed)
  * `POST /app/checklists/findings/<pk>/resolve/` &rarr; `302 Found` (redirects to `checklists:inspection`)
* **Equivalent REST API (Returns 200/201 JSON, Never 302):**
  * `POST /api/v1/checklist-templates/` &rarr; `201 Created`
  * `POST /api/v1/inspections/` &rarr; `201 Created`
  * `POST /api/v1/findings/` &rarr; `201 Created`

---

### Module 09: Spare Parts & Inventory (`apps.inventory`)
* **Source:** `src/apps/inventory/views.py`
* **Web UI Endpoints returning 302 on POST:**
  * `POST /app/inventory/parts/create/` &rarr; `302 Found` (redirects to `inventory:part`)
  * `POST /app/inventory/parts/<pk>/edit/` &rarr; `302 Found` (redirects to `inventory:part`)
  * `POST /app/inventory/warehouses/create/` &rarr; `302 Found` (redirects to `inventory:warehouse`)
  * `POST /app/inventory/warehouses/<pk>/edit/` &rarr; `302 Found` (redirects to `inventory:warehouse`)
  * `POST /app/inventory/stock/<pk>/adjust/` &rarr; `302 Found` (redirects to `inventory:stock_detail`)
  * `POST /app/inventory/movements/receipt/` &rarr; `302 Found` (redirects to `inventory:movements`)
  * `POST /app/inventory/movements/transfer/` &rarr; `302 Found` (redirects to `inventory:movements`)
* **Equivalent REST API (Returns 200/201 JSON, Never 302):**
  * `POST /api/v1/parts/` &rarr; `201 Created`
  * `POST /api/v1/warehouses/` &rarr; `201 Created`
  * `POST /api/v1/stock-movements/` &rarr; `201 Created`
  * `POST /api/v1/part-reservations/` &rarr; `201 Created`
  * `POST /api/v1/work-order-parts/` &rarr; `201 Created`

---

### Module 01: Site & Location Master (`apps.sites`)
* **Source:** `src/apps/sites/views.py`
* **Web UI Endpoints returning 302 on POST:**
  * `POST /app/sites/create/` &rarr; `302 Found` (redirects to `sites:detail`)
  * `POST /app/sites/<pk>/edit/` &rarr; `302 Found` (redirects to `sites:detail`)
  * `POST /app/sites/<pk>/zones/create/` &rarr; `302 Found` (redirects to `sites:detail?tab=locations`)
  * `POST /app/sites/<pk>/calendars/create/` &rarr; `302 Found` (redirects to `sites:detail?tab=calendars`)
  * `POST /app/sites/<pk>/contacts/create/` &rarr; `302 Found` (redirects to `sites:detail?tab=contacts`)
* **Equivalent REST API (Returns 200/201 JSON, Never 302):**
  * `POST /api/v1/sites/` &rarr; `201 Created`
  * `POST /api/v1/zones/` &rarr; `201 Created`
  * `POST /api/v1/calendars/` &rarr; `201 Created`
  * `POST /api/v1/calendar-holidays/` &rarr; `201 Created`
  * `POST /api/v1/site-contacts/` &rarr; `201 Created`

---

### Modules 02 & 03: Asset Registry & Hierarchy (`apps.assets`)
* **Source:** `src/apps/assets/views.py`
* **Web UI Endpoints returning 302 on POST:**
  * `POST /app/assets/create/` &rarr; `302 Found` (redirects to `assets:detail`)
  * `POST /app/assets/<pk>/edit/` &rarr; `302 Found` (redirects to `assets:detail`)
  * `POST /app/assets/<pk>/status/` &rarr; `302 Found` (redirects to `assets:detail`)
  * `POST /app/assets/categories/create/` &rarr; `302 Found` (redirects to `assets:categories`)
  * `POST /app/assets/categories/<pk>/edit/` &rarr; `302 Found` (redirects to `assets:categories`)
* **Equivalent REST API (Returns 200/201 JSON, Never 302):**
  * `POST /api/v1/assets/` &rarr; `201 Created`
  * `POST /api/v1/asset-categories/` &rarr; `201 Created`
  * `POST /api/v1/asset-components/` &rarr; `201 Created`
  * `POST /api/v1/asset-meters/` &rarr; `201 Created`

---

### Module 10: Contracts & Coverage (`apps.contracts`)
* **Source:** `src/apps/contracts/views.py`
* **Web UI Endpoints returning 302 on POST:**
  * `POST /app/contracts/agreements/create/` &rarr; `302 Found` (redirects to `contracts:agreement`)
  * `POST /app/contracts/agreements/<pk>/edit/` &rarr; `302 Found` (redirects to `contracts:agreement`)
  * `POST /app/contracts/agreements/<pk>/renew/` &rarr; `302 Found` (redirects to `contracts:agreement`)
  * `POST /app/contracts/providers/create/` &rarr; `302 Found` (redirects to `contracts:providers`)
* **Equivalent REST API (Returns 200/201 JSON, Never 302):**
  * `POST /api/v1/contract-providers/` &rarr; `201 Created`
  * `POST /api/v1/coverage-agreements/` &rarr; `201 Created`
  * `POST /api/v1/coverage-checks/` &rarr; `201 Created`

---

### Module 11: SLA & Escalation (`apps.sla`)
* **Source:** `src/apps/sla/views.py`
* **Web UI Endpoints returning 302 on POST:**
  * `POST /app/sla/breaches/<pk>/acknowledge/` &rarr; `302 Found` (redirects to `/app/sla/breaches/`)
  * `POST /app/sla/profiles/create/` &rarr; `302 Found` (redirects to `sla:profile`)
  * `POST /app/sla/profiles/<pk>/targets/add/` &rarr; `302 Found` (redirects to `sla:profile`)
  * `POST /app/sla/profiles/<pk>/rules/add/` &rarr; `302 Found` (redirects to `sla:profile`)
* **Equivalent REST API (Returns 200/201 JSON, Never 302):**
  * `POST /api/v1/sla-profiles/` &rarr; `201 Created`
  * `POST /api/v1/sla-trackings/` &rarr; `201 Created`
  * `POST /api/v1/sla-breaches/` &rarr; `201 Created`

---

### Module 12: QR & Barcode Identifiers (`apps.identification`)
* **Source:** `src/apps/identification/views.py`
* **Web UI Endpoints returning 302 on POST:**
  * `POST /app/assets/<pk>/identifiers/generate/` &rarr; `302 Found` (redirects to `/app/assets/<pk>/?tab=labels`)
  * `POST /app/identification/identifiers/<pk>/revoke/` &rarr; `302 Found` (redirects to `/app/assets/<pk>/?tab=labels`)
  * `POST /app/identification/scan/` &rarr; `302 Found` (redirects to asset profile or incident creation)
* **Equivalent REST API (Returns 200/201 JSON, Never 302):**
  * `POST /api/v1/asset-identifiers/` &rarr; `201 Created`

---

### Foundation & Identity Modules
* **Accounts & Auth (`apps.accounts`)**:
  * `POST /accounts/login/` &rarr; `302 Found` (redirects to `LOGIN_REDIRECT_URL` `/app/`)
  * `POST /accounts/profile/` &rarr; `302 Found` (redirects to `accounts:profile`)
* **Tenancy & Multi-Organization (`apps.tenancy`)**:
  * `POST /app/tenancy/organization/` &rarr; `302 Found`
  * `POST /app/tenancy/members/create/` &rarr; `302 Found`
  * `POST /app/switch-organization/` &rarr; `302 Found`
* **RBAC Roles & Permissions (`apps.rbac`)**:
  * `POST /app/rbac/roles/create/` &rarr; `302 Found`
  * `POST /app/rbac/roles/<pk>/edit/` &rarr; `302 Found`
* **Notifications (`apps.notifications`)**:
  * `POST /app/notifications/<pk>/read/` &rarr; `302 Found`
  * `POST /app/notifications/mark-all-read/` &rarr; `302 Found`

---

## 4. How to Ensure API Clients Receive Pure JSON (No 302 Redirects)

1. **Direct requests to `/api/v1/...` instead of `/app/...`:**
   * Incorrect: `POST /app/workspace/jobs/<pk>/travel/` (Web UI form &rarr; 302)
   * Correct: `POST /api/v1/workspace/jobs/<pk>/travel/` (REST API &rarr; 201 JSON)

2. **Always include trailing slashes on URLs:**
   * Incorrect: `POST /api/v1/work-orders` (Missing `/` &rarr; 302 redirect via `CommonMiddleware`)
   * Correct: `POST /api/v1/work-orders/` (Direct execution &rarr; 201 JSON)

3. **Authenticate via Bearer Token & Specify Active Tenant:**
   * Header: `Authorization: Bearer <jwt_access_token>`
   * Header: `X-Organization: <organization_uuid>`
   * Header: `Content-Type: application/json`
