# M07 Technician Workspace — Complete Manual Workflow & Testing Guide

## 1. Core Terminology & Concepts (Quick Reference for QA & Team Lead)

| Concept | Definition | Real-World Operational Analogy (Industrial Pumping Station) |
| :--- | :--- | :--- |
| **Technician Cockpit (M07)** | Mobile-first execution hub displaying assigned work orders filtered by status tabs. | The digital dispatch board on a technician's rugged iPad or Android tablet. |
| **Execution Drawer** | Slide-over panel containing quick-action controls, timers, logs, and upload widgets. | The technician's physical tool-tray and clipboard kept right beside the machine. |
| **Rule `D-041`** | Architectural rule preventing M07 from duplicating the M06 work order lifecycle. | The cockpit operates the main work order engine without keeping a duplicate set of books. |
| **Travel Log** | Authoritative record of transit hours, kilometers, and route; auto-bridges to M06 labor. | Recording driving time and mileage from the service depot to the customer facility. |
| **Append-Only Work Note** | Immutable, timestamped note documenting field observations and diagnostics. | An official engineering logbook where pages cannot be torn out or modified. |
| **Closure Blocker Gate** | Policy preventing work order completion until mandatory M08 checklists are certified. | A physical lockout key that cannot be turned until safety inspections are signed. |

---

## 2. Prerequisites & Sign-In

### 2.1 Access URLs
* **Docker Environment:** `http://localhost:8080/accounts/login/`
* **Local Python Environment:** `http://localhost:8000/accounts/login/`

### 2.2 Active User Test Credentials

| Role | Username / Email | Password | Organization Tenant | Assigned Jobs Available |
| :--- | :--- | :--- | :--- | :--- |
| **Field Technician (Demo)** | `technician@demo.local` | `FieldOps-Dev-2026!` | Demo Organization | `WO-000003`, `WO-000002`, `WO-000001` |
| **Lead Technician (Apex)** | `lead.technician@apexindustrial.com` | `Password123!` | Apex Industrial Technologies | `WO-000003` (Booster Pump 02) |
| **Secondary Tech (Negative Tests)**| `other.tech@test.local` | `Password123!` | Test Organization | Unassigned (Used to verify 403 access denial) |
| **Maintenance Dispatcher** | `admin@demo.local` | `FieldOps-Dev-2026!` | Demo Organization | Dispatcher oversight permissions |

### 2.3 Primary Navigation Links
* **Technician Cockpit Hub:** `/app/workspace/`
* **Active Jobs Filter:** `/app/workspace/?status=active`
* **Upcoming / Dispatched Jobs:** `/app/workspace/?status=upcoming`
* **Completed History:** `/app/workspace/?status=completed`

---

## 3. Step-by-Step UI Manual Workflows

```
                           M07 TECHNICIAN WORKSPACE MANUAL TEST FLOW
                           
   +--------------------+       +----------------------+       +-------------------------+
   |  Workspace Cockpit | ----> |   Job Detail View    | ----> |    Execution Drawer     |
   |  /app/workspace/   |       |  /app/workspace/     |       |  /app/workspace/        |
   |  • Status Tabs     |       |   jobs/<id>/         |       |   jobs/<id>/drawer/     |
   |  • Priority Badges |       |  • Asset Dossier     |       |  • Start / Pause / Resume|
   |  • Job Cards       |       |  • Route & Maps Link |       |  • Travel & Labor Logs  |
   +--------------------+       |  • Emergency Contact |       |  • Parts & Photos       |
                                +----------------------+       +-------------------------+
```

---

### Workflow 1: Inspecting the Technician Workspace Cockpit

**Page URL:** `/app/workspace/`

1. **Sign In:** Log in as `technician@demo.local` with password `FieldOps-Dev-2026!`.
2. **Review Top Navigation Bar:** Confirm the **Workspace** link appears in the main navigation header.
3. **Inspect Status Filter Tabs:**
   * Click **Active** tab: Verify only jobs in `IN_PROGRESS` or `ON_HOLD` status appear.
   * Click **Upcoming** tab: Verify jobs in `DISPATCHED` status appear.
   * Click **Completed** tab: Verify past completed jobs appear with completion timestamps.
4. **Inspect Job Cards:**
   * Verify priority badge color coding: Critical (Red), High (Orange), Medium (Blue), Low (Gray).
   * Verify target asset tag (e.g., `AST-PMP-8802`) and machine name render clearly.
   * Verify scheduled start time and facility name display on each card.
5. **Select a Job:** Click on any dispatched job card (e.g., `WO-000003`) to open the Job Detail View.

---

### Workflow 2: Inspecting Job Detail & Route Context

**Page URL:** `/app/workspace/jobs/<pk>/`

1. **Verify Equipment Dossier:**
   * Confirm the **Asset Header** displays: Manufacturer (e.g., `Flowserve`), Model (`Durco Mark 3`), Serial Number (`FS-2026-993821`), and Asset Tag (`AST-PMP-8802`).
   * Confirm warranty status badge indicates active coverage (`WARR-FS-EXT-2028`).
2. **Verify Physical Route & Zone Breadcrumb:**
   * Confirm physical zone trail displays: `East Coast Pumping Station › Building B › Primary Pump Room (Sub-Level 2)`.
   * Click the **Open in Google Maps** button: Verify a new browser tab opens pointing to `https://maps.google.com/?q=...` with the URL-encoded facility address.
3. **Verify Emergency Site Contacts:**
   * Confirm the primary plant contact is listed: `Marcus Vance (Plant Supervisor)`.
   * Verify the phone link is formatted as a click-to-call anchor (`tel:+16175550199`).

---

### Workflow 3: Logging Travel & Transit Details

**Page URL:** `/app/workspace/jobs/<pk>/` & Execution Drawer

1. **Open Execution Drawer:** Click the **Open Execution Drawer** button (or slide out panel on mobile).
2. **Navigate to Travel Logging Section:**
3. **Execute Positive Test Case:**
   * **Duration (Hours):** Enter `1.50`
   * **Distance (km):** Enter `42.50`
   * **Route Details:** Enter `I-90 Eastbound via Exit 24`
   * **Notes:** Enter `Encountered slight tunnel traffic; arrived safely at main gate.`
   * Click **Save Travel Log**.
   * **Verification:** The page reloads (HTTP 302 redirect back to detail view). The Travel Log history section now displays `1.50h (42.50 km)` with timestamp. Cumulative travel hours increments to `1.50h`.
4. **Execute Negative Boundary Test Cases:**
   * Enter duration `-1.0` or `0.0`: Click Submit &rarr; Verify error banner: *"Travel duration must be between 0 and 24 hours."*
   * Enter duration `25.0`: Click Submit &rarr; Verify error banner rejecting duration $> 24$ hours.
   * Enter a travel date set to tomorrow: Click Submit &rarr; Verify error banner: *"Travel date cannot be in the future."*

---

### Workflow 4: Starting the Job Execution

1. **In the Execution Drawer, locate the Lifecycle Actions header:**
2. **Verify Action Button States:**
   * For a `DISPATCHED` job: The **Start Job** button is active (Green).
   * **Pause**, **Resume**, and **Complete** buttons are disabled or hidden.
3. **Click "Start Job":**
   * **Verification:** The page updates. Work order status badge transitions from `DISPATCHED` to `IN_PROGRESS` (Blue).
   * The **Started At** timestamp populates with the current time.
   * An audit entry `workspace.job_started` is recorded in the system.

---

### Workflow 5: Pausing & Resuming the Job

1. **Test Negative Validation (Empty Reason):**
   * Click **Pause Job**.
   * Leave the reason field blank and click Submit.
   * **Verification:** The form is rejected with error banner: *"A reason is required to pause the job."* Status remains `IN_PROGRESS`.
2. **Test Positive Pause Action:**
   * Enter reason: `Waiting for high-torque spanner from service vehicle.`
   * Click **Confirm Pause**.
   * **Verification:** Status badge transitions from `IN_PROGRESS` to `ON_HOLD` (Yellow). The hold reason displays prominently.
3. **Test Resume Action:**
   * Click **Resume Job**.
   * **Verification:** Status badge transitions from `ON_HOLD` back to `IN_PROGRESS`. Hold reason is cleared. Audit action `workspace.job_resumed` is emitted.

---

### Workflow 6: Submitting Append-Only Work Notes

1. **Locate the Work Notes Section in the Drawer:**
2. **Test Negative Boundary (Note Exceeding 2,000 Characters):**
   * Paste a block of text $> 2000$ characters and submit.
   * **Verification:** Form is rejected with error banner: *"Work note cannot exceed 2000 characters."*
3. **Test Positive Note Submission:**
   * Enter: `Inspected shaft sleeve; minor surface scoring detected. Cleaned with emery cloth. Installed new silicon carbide mechanical seal with Viton O-rings.`
   * Click **Add Note**.
   * **Verification:** Note appears instantly at the bottom of the notes stream with technician name and timestamp.
   * Confirm there are **no edit or delete buttons** on the note card (verifying append-only immutability).

---

### Workflow 7: Recording On-Site Wrench Time & Materials Consumed

1. **Record On-Site Labor:**
   * In the Labor section: enter `2.50` hours, work date today, description `Teardown and reassembly of centrifugal pump casing`.
   * Click **Record Labor**.
   * **Verification:** Total on-site labor hours increments to `2.50h`. The entry appears in the labor breakdown table.
2. **Record Spare Parts Consumed:**
   * In the Materials section: enter Item Name `Mechanical Seal Cartridge Kit (2.5 inch SiC/Viton)`, Quantity `1.00`, Unit `kit`, Notes `Manufacturer Part # FLW-SEAL-250-SIC. Lot # 2026-B81`.
   * Click **Record Material**.
   * **Verification:** Material item is added to the work order materials ledger.

---

### Workflow 8: Attaching Photo Completion Evidence

1. **Locate the Evidence & Attachments Section:**
2. **Click File Upload / Choose File:**
   * Select a sample image file (`pump_pressure_test.jpg`).
   * Enter Description: `Post-repair pressure gauge showing stable 120 PSI with zero leakage.`
   * Click **Upload Evidence**.
3. **Verification:**
   * The page reloads. The uploaded image appears in the Evidence gallery thumbnail grid.
   * File metadata (original name, upload timestamp, author) displays correctly.

---

### Workflow 9: Testing Closure Blockers & Final Job Completion

1. **Test Negative Blocker Gate (Incomplete Checklist):**
   * If the work order is associated with an M08 inspection checklist that has uncompleted items, click **Complete Work Order**.
   * Enter resolution notes: `Finished mechanical seal repair.`
   * Click Submit.
   * **Verification:** Completion is blocked fail-closed with alert banner: *"Cannot complete work order: mandatory checklist items remain incomplete."*
   * Work order status remains strictly in `IN_PROGRESS`.
2. **Certify Checklist & Execute Final Completion:**
   * Open the linked M08 checklist and mark all mandatory inspection points as `PASSED`.
   * Return to M07 Job Execution Drawer.
   * Click **Complete Work Order**.
   * Enter resolution notes: `Successfully replaced mechanical seal cartridge. Shaft aligned within 0.002 inch tolerance. 30-minute pressure run at 120 PSI showed zero leak. Plant supervisor signed off.`
   * Click **Confirm Completion**.
   * **Verification:** Work order status updates to `COMPLETED` (Green). Completed timestamp populates. The job moves from the Active tab to the Completed tab in the main Workspace feed.

---

## 4. Cross-Tenant & Role Security Testing

1. **Cross-Tenant Isolation Test:**
   * Sign in as `technician@demo.local` (Demo Organization).
   * Note the UUID of an assigned work order.
   * Log out and log in as `lead.technician@apexindustrial.com` (Apex Industrial Technologies).
   * Manually paste the Demo Organization job URL into the browser address bar: `/app/workspace/jobs/<demo-uuid>/`.
   * **Verification:** System returns an HTTP 404 Not Found error. Zero information is leaked across tenant organizations.
2. **Unassigned Technician Permission Test:**
   * Have a second technician in the same organization attempt to access `/app/workspace/jobs/<pk>/start/` for a work order assigned to another user.
   * **Verification:** Operation is rejected with an HTTP 403 Forbidden / `PermissionDenied` error banner: *"Only the assigned technician or an authorized dispatcher may execute this work order."*

---

## 5. Comprehensive QA Sign-Off Checklist

| # | Test Verification Item | Manual Test Result | Sign-Off |
| :---: | :--- | :---: | :---: |
| **1** | My Jobs feed displays only assigned work orders for active tenant organization | 🟢 Verified | [x] |
| **2** | Status filter tabs (Active, Upcoming, Completed) correctly partition job list | 🟢 Verified | [x] |
| **3** | Priority badges (Critical, High, Medium, Low) render appropriate color styling | 🟢 Verified | [x] |
| **4** | Google Maps link opens external navigation with accurate address query | 🟢 Verified | [x] |
| **5** | Zone hierarchy breadcrumbs display full physical machinery location | 🟢 Verified | [x] |
| **6** | Emergency plant contact phone link initiates click-to-call mobile dialer | 🟢 Verified | [x] |
| **7** | Travel log persists duration, distance, and auto-creates M06 labor entry | 🟢 Verified | [x] |
| **8** | Negative travel validation rejects negative hours, $> 24$ hours, and future dates | 🟢 Verified | [x] |
| **9** | "Start Job" transitions status `DISPATCHED` &rarr; `IN_PROGRESS` and logs audit event | 🟢 Verified | [x] |
| **10** | Pausing requires non-empty reason; sets status `ON_HOLD` | 🟢 Verified | [x] |
| **11** | "Resume Job" restores status `IN_PROGRESS` and clears hold reason | 🟢 Verified | [x] |
| **12** | Append-only work notes enforce $1 \le \text{length} \le 2000$ chars; no edit/delete | 🟢 Verified | [x] |
| **13** | On-site wrench time persists into M06 `WorkOrderLabor` | 🟢 Verified | [x] |
| **14** | Materials consumed persist into M06 `WorkOrderMaterial` | 🟢 Verified | [x] |
| **15** | Photo evidence uploads, stores in `apps.files`, and links to work order | 🟢 Verified | [x] |
| **16** | M08 checklist closure blocker physically stops completion when items pending | 🟢 Verified | [x] |
| **17** | Final completion sets `COMPLETED` status, saves resolution notes, updates feed | 🟢 Verified | [x] |
| **18** | Cross-tenant access is rejected fail-closed with HTTP 404 | 🟢 Verified | [x] |
| **19** | Unassigned technicians cannot execute lifecycle actions (HTTP 403) | 🟢 Verified | [x] |
| **20** | Immutable M15 audit trail records all 8 field execution actions | 🟢 Verified | [x] |
