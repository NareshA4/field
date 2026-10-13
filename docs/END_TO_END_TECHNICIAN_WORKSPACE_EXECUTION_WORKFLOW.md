# End-to-End Technician Workspace Execution Workflow Guide

**Project:** FieldOps Nexus ERP (`HPE-PRD-2026-FOPS02`)  
**Workflow Identifier:** E2E-M07-EXEC-01  
**Target Module:** Module M07 — Technician Workspace (`apps.workspace`)  
**Downstream Authorities:** M01 (Sites), M02/M03 (Assets), M06 (Work Orders), M08 (Checklists), M09 (Inventory), apps.files (Evidence), M15 (Audit)  
**Author / Engineering Lead:** Veeresh  
**Lead QA Reviewer:** Lead QA Test Engineering Specialist  
**Status:** **100% Implemented, Verified, and Production-Ready**  

---

## 📑 Table of Contents
1. [Core Mental Model & Terminology](#1-core-mental-model--terminology)
2. [End-to-End Workflow Architecture Map](#2-end-to-end-workflow-architecture-map)
3. [Stage 1: Work Order Creation, Planning & Dispatch (M06)](#stage-1-work-order-creation-planning--dispatch-m06)
4. [Stage 2: Technician Job Discovery in Workspace Cockpit (M07)](#stage-2-technician-job-discovery-in-workspace-cockpit-m07)
5. [Stage 3: Reviewing Route, Equipment Specs & Emergency Contacts (M01/M02)](#stage-3-reviewing-route-equipment-specs--emergency-contacts-m01m02)
6. [Stage 4: Transit Logging & Automatic Travel Labor Rollup (M07 &rarr; M06)](#stage-4-transit-logging--automatic-travel-labor-rollup-m07--m06)
7. [Stage 5: Arriving On-Site & Starting Job Execution (M07 &rarr; M06)](#stage-5-arriving-on-site--starting-job-execution-m07--m06)
8. [Stage 6: Handling Field Pauses & On-Hold States (M07 &rarr; M06)](#stage-6-handling-field-pauses--on-hold-states-m07--m06)
9. [Stage 7: Resuming Active Job Execution (M07 &rarr; M06)](#stage-7-resuming-active-job-execution-m07--m06)
10. [Stage 8: Capturing Real-Time Diagnostic Notes (M07 WorkNotes)](#stage-8-capturing-real-time-diagnostic-notes-m07-worknotes)
11. [Stage 9: Executing Mandatory Safety Inspection Checklists (M08)](#stage-9-executing-mandatory-safety-inspection-checklists-m08)
12. [Stage 10: Recording On-Site Wrench Time & Labor (M07 &rarr; M06)](#stage-10-recording-on-site-wrench-time--labor-m07--m06)
13. [Stage 11: Recording Consumed Spare Parts & Materials (M07 &rarr; M09)](#stage-11-recording-consumed-spare-parts--materials-m07--m09)
14. [Stage 12: Uploading Photo Completion Evidence (M07 &rarr; apps.files)](#stage-12-uploading-photo-completion-evidence-m07--appsfiles)
15. [Stage 13: Closure Blocker Verification & Negative Testing (M08 Gate)](#stage-13-closure-blocker-verification--negative-testing-m08-gate)
16. [Stage 14: Finalizing Work Order Completion (M07 &rarr; M06)](#stage-14-finalizing-work-order-completion-m07--m06)
17. [Stage 15: Post-Completion Immutable Audit Verification (M15)](#stage-15-post-completion-immutable-audit-verification-m15)
18. [End-to-End Operational Checklist for QA & Field Supervisors](#18-end-to-end-operational-checklist-for-qa--field-supervisors)

---

## 1. Core Mental Model & Terminology

| Concept | Definition in FieldOps Nexus | Operational Example |
| :--- | :--- | :--- |
| **Dispatched Job** | A work order planned, scheduled, and formally allocated to a specific field technician. | Emergency Mechanical Seal Leak on Centrifugal Booster Pump 02. |
| **Technician Cockpit** | Mobile-first execution hub (`/app/workspace/`) showing assigned jobs filtered by status. | Technician's active job board on a rugged field tablet. |
| **Execution Drawer** | Slide-in drawer component providing stopwatch controls, travel tracking, notes, and parts. | HTMX drawer containing action buttons, timers, and upload forms. |
| **Rule `D-041`** | Architectural rule stating M07 orchestrates actions without creating a second lifecycle. | M07 calls `wo_services.start_work_order()` directly; does not store duplicate status. |
| **Travel Log** | Authoritative record of technician travel time, distance, and route; auto-bridges to labor. | 1.5 hours travel (42.5 km) from Regional Depot to Alpha Plant. |
| **Append-Only Note** | Immutable, timestamped note capturing observations and field diagnostic findings. | "Disassembled pump casing; discovered cracked silicon carbide seal ring." |
| **Closure Blocker** | Policy gate preventing job completion until mandatory safety checklists are certified. | Attempting completion fails with HTTP 409 until Lockout/Tagout checklist is signed. |

---

## 2. End-to-End Workflow Architecture Map

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 1: PLANNER DISPATCH (M06)                                                        │
│ Maintenance Planner creates, plans schedule, assigns technician & dispatches WO-000003  │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │ Dispatched Work Order
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ STAGES 2 & 3: JOB DISCOVERY & ROUTE TRIAGE (M07, M01, M02)                             │
│ Tech views /app/workspace/ ──► Opens Detail ──► Reviews Zone, Specs, Contacts & Maps    │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │ Departure
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 4: TRANSIT LOGGING (M07 TravelLog ──► M06 WorkOrderLabor)                        │
│ Tech logs 1.5h transit (42.5 km) ──► Persists TravelLog & auto-creates M06 Labor entry │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │ Arrival On-Site
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 5: START JOB EXECUTION (M07 ──► M06 Lifecycle)                                   │
│ Tech taps "Start Job" ──► Status transitions DISPATCHED ──► IN_PROGRESS                │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │ Interruption (e.g., Missing Part)
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ STAGES 6 & 7: PAUSE & RESUME (M07 ──► M06 Lifecycle)                                   │
│ Tech pauses job (Reason required) ──► ON_HOLD ──► Tech resumes ──► IN_PROGRESS         │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │ Diagnostic Observations
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 8: WORK NOTES STREAM (M07 WorkNote)                                              │
│ Tech logs immutable append-only note (up to 2,000 characters)                          │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │ Safety Inspection
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 9: SAFETY CHECKLIST EXECUTION (M08 Checklists)                                    │
│ Tech executes mandatory M08 Lockout/Tagout checklist runner items                      │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │ Repair Execution
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ STAGES 10, 11 & 12: WRENCH TIME, PARTS & PHOTO EVIDENCE (M06, M09, apps.files)         │
│ • Record 2.5h on-site labor (M06)                                                      │
│ • Record 1x Mechanical Seal Kit consumed (M09)                                         │
│ • Upload photo of completed pump gauge showing stable pressure (apps.files)            │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │ Completion Verification
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ STAGES 13 & 14: CLOSURE BLOCKER GATE & JOB FINALIZATION (M08 ──► M06)                  │
│ • Pre-check validates 100% of mandatory checklist items certified clean               │
│ • Tech enters resolution notes and taps "Complete Work Order"                          │
│ • Status transitions IN_PROGRESS ──► COMPLETED                                         │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │ Post-Execution Audit
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 15: IMMUTABLE AUDIT TRAIL VERIFICATION (M15)                                     │
│ System verifies full sequence of tamper-evident audit records in M15                   │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Stage 1: Work Order Creation, Planning & Dispatch (M06)

* **Actor:** Maintenance Planner (`planner@apexindustrial.com`)
* **Objective:** Create and dispatch an emergency corrective maintenance work order.

```python
# 1. Create work order
wo = wo_services.create_work_order(
    org, site_id=site.pk, asset=pump_asset, actor=planner_user,
    title="Emergency: High Pressure Pump 02 Mechanical Seal Failure",
    work_type="CORRECTIVE", priority=WorkOrder.Priority.CRITICAL,
    description="Pump leaking 15 L/min fluid; casing vibrating above threshold.",
    membership=planner_membership,
)

# 2. Plan estimated schedule
wo = wo_services.plan_work_order(
    wo, membership=planner_membership, actor=planner_user,
    planned_start=timezone.now() + timedelta(hours=1),
    planned_end=timezone.now() + timedelta(hours=4),
    estimated_hours=Decimal("3.0"),
    due_at=timezone.now() + timedelta(hours=8),
)

# 3. Assign to qualified technician
wo = wo_services.assign_work_order(
    wo, membership=planner_membership, actor=planner_user,
    assignee_id=technician_membership.pk,
    scheduled_start=timezone.now() + timedelta(hours=1),
    scheduled_end=timezone.now() + timedelta(hours=4),
)

# 4. Dispatch to field crew
dispatched_wo = wo_services.dispatch_work_order(wo, membership=planner_membership, actor=planner_user)
# Status: DISPATCHED
```

---

## Stage 2: Technician Job Discovery in Workspace Cockpit (M07)

* **Actor:** Field Technician (`lead.technician@apexindustrial.com`)
* **Objective:** Access the mobile cockpit and review assigned jobs.
* **Web UI Endpoint:** `GET /app/workspace/?status=upcoming`
* **REST API Endpoint:** `GET /api/v1/workspace/jobs/?status=upcoming`

```http
GET /api/v1/workspace/jobs/?status=upcoming HTTP/1.1
Host: localhost:8000
Authorization: Bearer <JWT_ACCESS_TOKEN>
```

**JSON Response (HTTP 200 OK):**
```json
{
  "count": 1,
  "results": [
    {
      "id": "e4f80210-9b81-42cb-bdf1-d308f2302319",
      "order_number": "WO-000003",
      "title": "Emergency: High Pressure Pump 02 Mechanical Seal Failure",
      "status": "DISPATCHED",
      "status_display": "Dispatched",
      "priority": "CRITICAL",
      "priority_display": "Critical",
      "site_name": "East Coast Pumping Station",
      "asset_tag": "AST-PMP-8802",
      "asset_name": "High Pressure Centrifugal Booster Pump 02",
      "scheduled_start": "2026-10-13T12:00:00Z"
    }
  ]
}
```

---

## Stage 3: Reviewing Route, Equipment Specs & Emergency Contacts (M01/M02)

* **Web UI Endpoint:** `GET /app/workspace/jobs/{id}/`
* **REST API Endpoint:** `GET /api/v1/workspace/jobs/{id}/`

The technician reviews the complete operational dossier:
1. **Physical Routing & GPS Navigation:**
   * **Facility Address:** 4500 Coastal Highway, Building B, Unit 4, Boston, MA 02101
   * **Google Maps Navigation:** `https://maps.google.com/?q=4500+Coastal+Highway+Building+B+Unit+4+Boston+MA+02101`
   * **Zone Hierarchy:** `East Coast Pumping Station › Building B › Primary Pump Room (Sub-Level 2)`
2. **Machinery Specifications:**
   * **Asset Tag:** `AST-PMP-8802`
   * **Manufacturer / Model:** Flowserve / Durco Mark 3
   * **Serial Number:** `FS-2026-993821`
   * **Active Warranty:** `WARR-FS-EXT-2028` (Valid until 2028-12-31)
3. **Emergency Escalation Contacts:**
   * **Plant Supervisor:** Marcus Vance (`+1-617-555-0199`)

---

## Stage 4: Transit Logging & Automatic Travel Labor Rollup (M07 &rarr; M06)

* **Objective:** Record 1.5 hours of transit (42.5 km) from the regional service depot to the plant.
* **REST API Endpoint:** `POST /api/v1/workspace/jobs/{id}/travel/`
* **Web UI Endpoint:** `POST /app/workspace/jobs/{id}/travel/` (Redirects with HTTP 302 back to job detail)

```http
POST /api/v1/workspace/jobs/e4f80210-9b81-42cb-bdf1-d308f2302319/travel/ HTTP/1.1
Content-Type: application/json
Authorization: Bearer <JWT_ACCESS_TOKEN>

{
  "duration_hours": "1.50",
  "distance_km": "42.50",
  "route_details": "I-90 Eastbound via Exit 24",
  "notes": "Moderate traffic near tunnel; arrived at main gate at 11:45"
}
```

**System Behavior & Verification:**
1. A `TravelLog` record is created and scoped to the active tenant.
2. Invariant Check: $0 < 1.50 \le 24.00$ satisfies check constraint `workspace_travel_duration_1_24`.
3. Because the job is in progress or transit, `wo_services.record_labor()` is invoked automatically: an authoritative M06 `WorkOrderLabor` entry is generated and linked via `travel_log.labor_entry`.
4. Audit action `workspace.travel_logged` is recorded in M15.

---

## Stage 5: Arriving On-Site & Starting Job Execution (M07 &rarr; M06)

* **Objective:** Technician arrives at the machinery and initiates active wrench time.
* **REST API Endpoint:** `POST /api/v1/workspace/jobs/{id}/start/`
* **Web UI Endpoint:** `POST /app/workspace/jobs/{id}/start/`

```http
POST /api/v1/workspace/jobs/e4f80210-9b81-42cb-bdf1-d308f2302319/start/ HTTP/1.1
Authorization: Bearer <JWT_ACCESS_TOKEN>
```

**System Behavior & Verification:**
1. Verifies caller is assigned technician (`technician_id == membership.pk`).
2. Transitions work order status: `DISPATCHED` $\to$ `IN_PROGRESS`.
3. Sets `started_at = timezone.now()`.
4. Emits `workspace.job_started` audit record.

---

## Stage 6: Handling Field Pauses & On-Hold States (M07 &rarr; M06)

* **Scenario:** Technician discovers the replacement mechanical seal requires specialized high-torque spanners located in the service van. The job must be paused.
* **REST API Endpoint:** `POST /api/v1/workspace/jobs/{id}/pause/`

```http
POST /api/v1/workspace/jobs/e4f80210-9b81-42cb-bdf1-d308f2302319/pause/ HTTP/1.1
Content-Type: application/json
Authorization: Bearer <JWT_ACCESS_TOKEN>

{
  "reason": "Waiting for specialized torque spanner and secondary flange gasket from service truck"
}
```

**Guardrail Enforcement:**
* Attempting to submit an empty or blank reason fails with `ValidationFailed("A reason is required to pause the job.")`.
* On valid input: transitions status `IN_PROGRESS` $\to$ `ON_HOLD`.
* Persists hold reason and emits `workspace.job_paused` audit record.

---

## Stage 7: Resuming Active Job Execution (M07 &rarr; M06)

* **Scenario:** Tools retrieved; technician resumes active work.
* **REST API Endpoint:** `POST /api/v1/workspace/jobs/{id}/resume/`

```http
POST /api/v1/workspace/jobs/e4f80210-9b81-42cb-bdf1-d308f2302319/resume/ HTTP/1.1
Authorization: Bearer <JWT_ACCESS_TOKEN>
```

**System Behavior:**
1. Transitions status `ON_HOLD` $\to$ `IN_PROGRESS`.
2. Clears hold reason field.
3. Emits `workspace.job_resumed` audit record.

---

## Stage 8: Capturing Real-Time Diagnostic Notes (M07 WorkNotes)

* **Objective:** Capture field observations for engineering records.
* **REST API Endpoint:** `POST /api/v1/workspace/jobs/{id}/notes/`

```http
POST /api/v1/workspace/jobs/e4f80210-9b81-42cb-bdf1-d308f2302319/notes/ HTTP/1.1
Content-Type: application/json
Authorization: Bearer <JWT_ACCESS_TOKEN>

{
  "note": "Inspected shaft sleeve; minor surface scoring detected. Cleaned with emery cloth. Installed new silicon carbide mechanical seal with Viton O-rings."
}
```

**Guardrail Enforcement:**
* Rejects empty notes ($< 1$ character) or notes exceeding 2,000 characters.
* Creates immutable `WorkNote` record. Deletions and updates are prohibited.
* Emits `workspace.work_note_added` audit record.

---

## Stage 9: Executing Mandatory Safety Inspection Checklists (M08)

* **Objective:** Complete required Lockout/Tagout and electrical isolation safety checks.
* **Target Module:** Module M08 Checklist Runner (`apps.checklists`)
* **Execution:** Technician certifies all mandatory checklist items:
  1. Main 480V electrical breaker locked and tagged (`PASSED`).
  2. Suction and discharge isolation valves locked closed (`PASSED`).
  3. Casing depressurized and drained of toxic effluent (`PASSED`).
  4. Personal Protective Equipment (PPE) certified (`PASSED`).

---

## Stage 10: Recording On-Site Wrench Time & Labor (M07 &rarr; M06)

* **Objective:** Record 2.5 hours of on-site mechanical repair labor.
* **REST API Endpoint:** `POST /api/v1/workspace/jobs/{id}/labor/`

```http
POST /api/v1/workspace/jobs/e4f80210-9b81-42cb-bdf1-d308f2302319/labor/ HTTP/1.1
Content-Type: application/json
Authorization: Bearer <JWT_ACCESS_TOKEN>

{
  "hours": "2.50",
  "work_date": "2026-10-13",
  "description": "Disassembly of pump casing, mechanical seal replacement, shaft alignment and reassembly"
}
```

**System Behavior:**
* Persists authoritative M06 `WorkOrderLabor` record.
* Increments cumulative labor hours on the work order.
* Emits `workspace.labor_recorded` audit record.

---

## Stage 11: Recording Consumed Spare Parts & Materials (M07 &rarr; M09)

* **Objective:** Record parts consumed from service truck inventory.
* **REST API Endpoint:** `POST /api/v1/workspace/jobs/{id}/materials/`

```http
POST /api/v1/workspace/jobs/e4f80210-9b81-42cb-bdf1-d308f2302319/materials/ HTTP/1.1
Content-Type: application/json
Authorization: Bearer <JWT_ACCESS_TOKEN>

{
  "item_name": "Mechanical Seal Cartridge Kit (2.5 inch SiC/Viton)",
  "quantity": "1.00",
  "unit": "kit",
  "notes": "Manufacturer Part # FLW-SEAL-250-SIC. Lot # 2026-B81"
}
```

**System Behavior:**
* Creates authoritative `WorkOrderMaterial` record in M06.
* Links material usage directly to the work order without mutating central warehouse stock balances directly.
* Emits `workspace.material_recorded` audit record.

---

## Stage 12: Uploading Photo Completion Evidence (M07 &rarr; apps.files)

* **Objective:** Upload post-repair photo showing zero leak under pressure test.
* **REST API Endpoint:** `POST /api/v1/workspace/jobs/{id}/evidence/` (Multipart Form)

```http
POST /api/v1/workspace/jobs/e4f80210-9b81-42cb-bdf1-d308f2302319/evidence/ HTTP/1.1
Content-Type: multipart/form-data; boundary=----Boundary
Authorization: Bearer <JWT_ACCESS_TOKEN>

------Boundary
Content-Disposition: form-data; name="file"; filename="pump_pressure_test_120psi.jpg"
Content-Type: image/jpeg

[BINARY PHOTO DATA]
------Boundary
Content-Disposition: form-data; name="description"

Post-repair pressure gauge showing stable 120 PSI with zero mechanical seal leakage
------Boundary--
```

**System Behavior:**
* Stores file in `apps.files` storage backend.
* Creates `WorkOrderEvidence` attachment link in M06.
* Emits `workspace.evidence_uploaded` audit record.

---

## Stage 13: Closure Blocker Verification & Negative Testing (M08 Gate)

* **Quality Assurance Verification:** If a technician attempts to complete the work order while any mandatory M08 checklist item is pending, the system halts execution fail-closed:

```http
POST /api/v1/workspace/jobs/e4f80210-9b81-42cb-bdf1-d308f2302319/complete/ HTTP/1.1
Content-Type: application/json
Authorization: Bearer <JWT_ACCESS_TOKEN>

{
  "resolution_notes": "Completed mechanical seal repair."
}
```

**System Response on Pending Checklist (HTTP 409 Conflict):**
```json
{
  "error": "conflict",
  "message": "Cannot complete work order: mandatory checklist items remain incomplete."
}
```
* **Status Invariant:** The work order remains strictly in `IN_PROGRESS` status; premature closure is mathematically prevented.

---

## Stage 14: Finalizing Work Order Completion (M07 &rarr; M06)

* **Precondition:** All mandatory M08 checklist items certified clean.
* **REST API Endpoint:** `POST /api/v1/workspace/jobs/{id}/complete/`
* **Web UI Endpoint:** `POST /app/workspace/jobs/{id}/complete/`

```http
POST /api/v1/workspace/jobs/e4f80210-9b81-42cb-bdf1-d308f2302319/complete/ HTTP/1.1
Content-Type: application/json
Authorization: Bearer <JWT_ACCESS_TOKEN>

{
  "resolution_notes": "Successfully replaced damaged mechanical seal cartridge. Realigned pump shaft within 0.002 inch tolerance. Completed 30-minute pressure run at 120 PSI with zero leakage. Plant supervisor Marcus Vance signed off on return to service."
}
```

**System Behavior & Verification (HTTP 200 OK):**
1. M08 checklist verification passes clean.
2. M06 `complete_work_order()` executes:
   * Status transitions `IN_PROGRESS` $\to$ `COMPLETED`.
   * Sets `completed_at = timezone.now()`.
   * Stores resolution notes.
3. Emits `workspace.job_completed` audit record in M15.

---

## Stage 15: Post-Completion Immutable Audit Verification (M15)

The entire operational journey is recorded across 8 distinct audit records in M15:

| Audit Action | Target Entity | Metadata Snapshots |
| :--- | :--- | :--- |
| `work_order.dispatched` | `WorkOrder (WO-000003)` | Assignee: Lead Technician |
| `workspace.travel_logged` | `WorkOrder (WO-000003)` | Duration: 1.50h, Distance: 42.50 km |
| `workspace.job_started` | `WorkOrder (WO-000003)` | Execution Drawer opened; Started at timestamp |
| `workspace.job_paused` | `WorkOrder (WO-000003)` | Reason: Waiting for torque spanner |
| `workspace.job_resumed` | `WorkOrder (WO-000003)` | Resumed from ON_HOLD |
| `workspace.work_note_added`| `WorkOrder (WO-000003)` | Note ID, 80-char note preview |
| `workspace.labor_recorded` | `WorkOrder (WO-000003)` | Hours: 2.50h, On-site repair description |
| `workspace.material_recorded`| `WorkOrder (WO-000003)`| Item: Mechanical Seal Kit, Qty: 1.00 |
| `workspace.evidence_uploaded`| `WorkOrder (WO-000003)`| Attachment ID, photo filename, file size |
| `workspace.job_completed` | `WorkOrder (WO-000003)` | Status: COMPLETED, Resolution notes snapshot |

---

## 18. End-to-End Operational Checklist for QA & Field Supervisors

- [x] **Check 1: Multi-Tenant Scoping:** Technician sees only work orders belonging to their authorized tenant organization.
- [x] **Check 2: Assignee Boundary:** Non-assigned technicians receive `PermissionDenied` when attempting to operate on the job.
- [x] **Check 3: Route & Maps Accuracy:** Site address and zone hierarchy breadcrumbs correctly format Google Maps navigation queries.
- [x] **Check 4: Travel Log Duration Bounds:** Negative hours, 0 hours, and hours $> 24.0$ are rejected by database and model validators.
- [x] **Check 5: Travel Labor Auto-Rollup:** Verified that logging travel creates an authoritative `WorkOrderLabor` entry in M06.
- [x] **Check 6: Mandatory Pause Reason:** Pausing without a non-empty explanation is rejected with `ValidationFailed`.
- [x] **Check 7: Append-Only Notes Immutability:** Notes stream displays chronologically; no edit or delete controls exist.
- [x] **Check 8: Checklist Blocker Gate:** Work order completion fails with HTTP 409 if mandatory M08 checklists are pending.
- [x] **Check 9: Photo Evidence Attachment:** Photos upload cleanly and link to M06 `WorkOrderEvidence`.
- [x] **Check 10: Final Closure State:** Completed work orders transition to `COMPLETED` status with resolution notes and audit records intact.
