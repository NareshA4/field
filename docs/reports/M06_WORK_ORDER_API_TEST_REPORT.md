# Module 6: Work Order API Verification Report

| Report field | Value |
|---|---|
| Verification date | 6 October 2026 |
| Environment | Local Docker Compose stack |
| Application URL | `http://localhost:8080` |
| Overall result | **PASS** for the Module 6 automated suite and listed smoke checks |
| Automated tests | **12 passed** |
| OpenAPI inventory | **20 unique paths / 22 HTTP operations** under Work Orders |

## 1. Executive summary

The current Docker-hosted API exposes the documented Work Order endpoints and workflow actions. The Module 6 test suite passed on the verification date. Django system and migration checks passed, all six Work Order migrations were applied, and the health, Swagger, and OpenAPI endpoints responded successfully.

The result means the covered automated cases and smoke checks passed. It is not a claim that every endpoint has been exercised against every possible input or failure condition. The cancellation route and external module integrations remain subject to the limitations listed in Section 6.

## 2. API documentation and access

- **Swagger UI:** [http://localhost:8080/api/v1/docs/](http://localhost:8080/api/v1/docs/)
- **OpenAPI JSON / Postman import:** [http://localhost:8080/api/v1/schema/?format=json](http://localhost:8080/api/v1/schema/?format=json)
- **Work Order collection:** [http://localhost:8080/api/v1/work-orders/](http://localhost:8080/api/v1/work-orders/)

Authenticate with `POST /api/v1/auth/token/`. Include the access token and select an active organization for tenant-scoped requests:

```http
Authorization: Bearer <access_token>
X-Organization: <organization-slug>
Content-Type: application/json
```

Token request example:

```http
POST /api/v1/auth/token/
Content-Type: application/json

{"email":"<user-email>","password":"<password>"}
```

The user's active membership and authorized site scope determine which records and actions are available. In the tested role configuration, maintenance planners can create and plan work; assigned technicians perform and submit work; supervisors assign/dispatch and make review decisions; operations managers have read-only Work Order access.

## 3. Endpoint inventory

The live OpenAPI document contains **20 unique Work Order paths**. Two paths support more than one method (collection `GET`/`POST`, record `GET`/`PATCH`), producing **22 HTTP operations**. All paths below are relative to `/api/v1`.

| Method and path | Operation | Example request | Expected success |
|---|---|---|---|
| `GET /work-orders/` | List scoped records; filters: `status`, `priority`, `site`, `assignee`, `work_type`, `source`, `overdue`, `q`, `ordering` | `?status=DISPATCHED&priority=HIGH&overdue=true` | 200 |
| `POST /work-orders/` | Create a DRAFT work order | `{"site":"<site-uuid>","title":"Inspect pump","work_type":"PREVENTIVE","priority":"HIGH","checklist_items":[{"label":"Inspect unit","required":true}]}` | 201 |
| `GET /work-orders/{id}/` | Read one in-scope work order | No body | 200; out of scope or missing: 404 |
| `PATCH /work-orders/{id}/` | Edit permitted fields; status cannot be patched directly | `{"title":"Inspect pump motor"}` | 200; attempted status edit: 400 |
| `POST /work-orders/{id}/checklist/` | Save the Work Order checklist snapshot | `{"items":[{"label":"Inspect unit","required":true,"completed":false}]}` | 200 |
| `POST /work-orders/{id}/plan/` | Plan DRAFT work | `{"planned_start":"<ISO-8601>","planned_end":"<ISO-8601>"}` | 200 |
| `POST /work-orders/{id}/assign/` | Assign a technician; validates membership, schedule, and version | `{"assigned_to":"<membership-uuid>","expected_version":1}` | 200 |
| `POST /work-orders/{id}/dispatch/` | Dispatch assigned work | `{}` | 200 |
| `POST /work-orders/{id}/start/` | Assigned technician starts work | `{}` | 200 |
| `POST /work-orders/{id}/hold/` | Put eligible work on hold | `{"reason":"Waiting for site access"}` | 200 |
| `POST /work-orders/{id}/resume/` | Resume held work | `{}` | 200 |
| `POST /work-orders/{id}/notes/` | Append a progress note | `{"text":"Inspection is underway."}` | 200 |
| `POST /work-orders/{id}/labor/` | Record labor | `{"work_date":"<YYYY-MM-DD>","hours":"1.25","description":"Inspected the unit"}` | 201 |
| `POST /work-orders/{id}/materials/` | Record a material usage note | `{"item_name":"Filter element","quantity":"1","unit":"each"}` | 201 |
| `POST /work-orders/{id}/evidence/` | Link an Attachment already attached to the work order | `{"attachment_id":"<attachment-uuid>"}` | 201 |
| `POST /work-orders/{id}/complete/` | Complete work after required resolution, checklist, and evidence validation | `{"resolution_notes":"Inspection complete; equipment is safe.","checklist_items":[{"label":"Inspect unit","required":true,"completed":true}]}` | 200 |
| `POST /work-orders/{id}/submit-for-review/` | Submit completed work for supervisor review | `{}` | 200 |
| `POST /work-orders/{id}/close/` | Supervisor approves and closes; comment required | `{"comment":"Work completed and verified."}` | 200 |
| `POST /work-orders/{id}/return-for-rework/` | Supervisor returns work to the technician; comment required | `{"comment":"Please add the missing inspection result."}` | 200 |
| `POST /work-orders/{id}/review/` | Compatibility decision action: `approved=true` closes; `false` returns for rework | `{"approved":false,"notes":"Please add the missing inspection result."}` | 200 |
| `GET /work-orders/{id}/events/` | Read append-only work order event history | No body | 200 |
| `POST /work-orders/{id}/cancel/` | Legacy cancellation action; provisional policy | `{"reason":"<reason>"}` | See Section 6 |

For the authoritative schemas, field constraints, and response models, use Swagger or import the OpenAPI JSON into Postman. The examples above show representative requests, not every optional field.

## 4. Workflow and authorization checks

The implemented primary flow is:

```text
DRAFT -> PLANNED -> ASSIGNED -> DISPATCHED -> IN_PROGRESS <-> ON_HOLD
IN_PROGRESS -> COMPLETED -> SUPERVISOR_REVIEW -> CLOSED
SUPERVISOR_REVIEW -> IN_PROGRESS (returned for rework)
```

`COMPLETED` records technician completion; it does not mean supervisor approval. `CLOSED` is the final approved state. State changes go through workflow actions; direct `PATCH` status changes are rejected. The tested assignment action validates the assignee and expected version.

Closing records the supervisor, a closure decision, an event, and an audit entry. Retrying an already-approved close returns the closed record without creating a duplicate approval or close event. Returning for rework requires a comment, records the decision and audit history, and notifies the assigned technician.

Role and tenant-scope checks exercised by the suite include forbidden close attempts, operations-manager restrictions, inaccessible work orders returning 404, and authenticated membership/site scope. Anonymous collection access returned 401 as expected.

## 5. Verification evidence

### Automated tests

Executed in a disposable Docker test container:

```text
python -m pytest -q tests/test_workorders.py
12 passed in 29.41s
```

The test cases covered the main API lifecycle and supporting actions: create/list/detail/edit; status PATCH rejection; checklist; plan/assign/dispatch/start; hold/resume; notes/labor/materials/evidence; completion and submission for review; event history; supervisor close and idempotent retry; return for rework; legacy review compatibility; role/site access; missing records and invalid state; and route/UI schema presence.

### Runtime and schema checks

| Check | Observed result |
|---|---|
| `python manage.py check` | PASS; no issues reported (one configured silence) |
| `python manage.py makemigrations --check --dry-run` | PASS; no changes detected |
| Work Order migrations | PASS; 0001 through 0006 applied |
| `GET /health/live/` through Docker proxy | HTTP 200 |
| `GET /api/v1/docs/` through Docker proxy | HTTP 200 |
| `GET /api/v1/schema/?format=json` through Docker proxy | HTTP 200; 20 Work Order paths and 22 operations |
| Unauthenticated `GET /api/v1/work-orders/` | HTTP 401, expected |
| Docker Compose | web, worker, beat, nginx running; database and Redis healthy |

## 6. Limitations and pending product decisions

- **Cancellation:** `/cancel/` is preserved for compatibility and marked provisional in OpenAPI. Product-owner confirmation is still needed for allowed source states and downstream cleanup/reconciliation rules. It is not counted as a fully approved workflow decision.
- **Checklist integration:** checklist items are stored as a Work Order-owned snapshot. A separate checklist application/API is not present in this checkout.
- **Inventory integration:** materials are recorded as usage notes; this endpoint does not issue stock or create inventory movements. A separate inventory application/API is not present in this checkout.
- **Evidence upload:** the endpoint links an existing shared Attachment; file bytes are uploaded through the shared files service, not this action.
- **Other module consumers:** local event, audit, and notification history are written. Active Asset, SLA, Service Desk, and dashboard integration consumers were not verified as present in this checkout.
- **Browser acceptance:** no interactive browser login/session was performed. UI routes and actions were exercised with Django's test client; API behavior was exercised with authenticated test clients; runtime smoke checks verified HTTP reachability.
