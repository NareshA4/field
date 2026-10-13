# Module M14: Operational Dashboards — Manual Workflow & Testing Guide

**Project:** FieldOps Nexus ERP (`HPE-PRD-2026-FOPS02`)  
**Module:** M14 — Operational Dashboards (`apps.dashboards`)  
**Author / Test Lead:** QA Engineering Lead  
**Target Environments:**  
* **Docker Container:** `http://localhost:8080`  
* **Local Python Runserver:** `http://localhost:8000`  
**Status:** **100% Implemented, Verified, and Production-Ready**  

---

## 1. Core Terminology & Concepts (Quick Reference for QA)

| Concept | Definition | Real-World Operational Analogy |
| :--- | :--- | :--- |
| **Operational Dashboards Hub** | Executive monitoring interface displaying real-time metrics across 5 operational domains. | The central control room display wall in an industrial manufacturing plant. |
| **My Work Cockpit** | Lightweight personal dashboard showing a technician's assigned jobs, logged hours, and workday capacity. | The personal dashboard on a field technician's tablet showing today's work queue. |
| **Architectural Rule `D-052`** | Aggregations are calculated in-memory directly from real database records; pure CSS bars without external JS libraries. | Building gauges from physical instruments rather than creating artificial dummy meters. |
| **Temporal Windowing** | Dynamic date filter (defaults to trailing 30 UTC days; maximum 400 days) applied to performance metrics. | Reviewing monthly machine throughput versus annual maintenance logs. |
| **Geographic Site Scoping** | Filter scoping metrics to specific physical plants or authorized facilities. | Switching camera feeds between Plant 1 and Plant 2. |
| **Pure CSS Visualizations** | Gradient progress bars computed as server-side percentages and rendered with native CSS flex. | Visual liquid level tubes on storage tanks that require zero external power to read. |

---

## 2. Prerequisites & User Test Credentials

### 2.1 Sign-In Portals
* **Docker Environment:** `http://localhost:8080/accounts/login/`
* **Local Runserver:** `http://localhost:8000/accounts/login/`

### 2.2 Active User Test Credentials

| Role | Username / Email | Password | Tenant Organization | Permissions & Access Scope |
| :--- | :--- | :--- | :--- | :--- |
| **Operations Manager** | `admin@demo.local` | `FieldOps-Dev-2026!` | Demo Organization | Full access to Executive Dashboards Hub & REST APIs (`report.view`) |
| **Field Technician** | `technician@demo.local` | `FieldOps-Dev-2026!` | Demo Organization | Personal My Work Cockpit (`/app/dashboards/my-work/`); Hub access restricted (HTTP 403) |
| **Apex Lead Tech** | `lead.technician@apexindustrial.com` | `Password123!` | Apex Industrial Technologies | Apex tenant personal work cockpit |
| **Secondary Tech** | `other.tech@test.local` | `Password123!` | Test Organization | Cross-tenant isolation verification |

---

## 3. Persona 1: Operations Manager Executive Testing Workflow

```
                   EXECUTIVE DASHBOARDS HUB WORKFLOW (/app/dashboards/)
                   
  +--------------------+      +--------------------+      +--------------------+
  | Operations Section | ---> |   Assets Section   | ---> | Maintenance Section|
  | • Open & Overdue WO|      | • Fleet Counters   |      | • PM Compliance %  |
  | • MTTR & Downtime  |      | • MTBF Hours       |      | • Work Type Mix    |
  | • CSS Priority Mix |      | • Top Failing List |      | • Due PM Schedule  |
  +--------------------+      +--------------------+      +--------------------+
            |                                                       |
            v                                                       v
  +--------------------+      +--------------------+      +--------------------+
  |  Service Section   |      | Inventory Section  |      | Filter Controls    |
  | • Open SR Incidents|      | • SKU Lines        |      | • Site Dropdown    |
  | • SLA Breaches     |      | • Low Stock SKU    |      | • Date From / To   |
  | • Target Deadlines |      | • Net Parts Units  |      | • Live Persistence |
  +--------------------+      +--------------------+      +--------------------+
```

### Step 1: Sign In & Navigate to Dashboards Hub
1. Open your browser and navigate to `http://localhost:8080/accounts/login/`.
2. Sign in with:
   * **Username:** `admin@demo.local`
   * **Password:** `FieldOps-Dev-2026!`
3. Locate **Operational Dashboards** under the **Analytics** menu group on the sidebar, or navigate directly to `http://localhost:8080/app/dashboards/`.

### Step 2: Verify Operations Section (Default View)
1. Verify the page heading displays **"Operational Dashboards"** with an active **"Live Aggregation"** pill.
2. Confirm the 5 navigation tabs are visible: **Operations**, **Assets**, **Maintenance**, **Service**, and **Inventory**.
3. Inspect the KPI cards:
   * **Open Work Orders:** Current count of active orders (e.g., `WO-000003`, `WO-000002`).
   * **Overdue Orders:** Orders past planned completion.
   * **MTTR:** Mean Time to Repair in hours or `"n/a"` if no closed outages.
   * **Total Downtime:** Total recorded outage hours in selected window.
   * **PM Compliance:** On-time completion percentage.
   * **Technician Utilization:** Logged hours against 8-hour workday capacity.
   * **SLA Breaches:** Open breach count.
4. Inspect the pure CSS bars:
   * **Work Order Status Distribution:** Confirm colored horizontal bars for `DRAFT`, `PLANNED`, `IN_PROGRESS`, `COMPLETED`.
   * **Priority Mix:** Confirm bars for `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`.
5. Verify the **Recent Work Orders** table renders with clickable links to `/app/workorders/<id>/`.

### Step 3: Switch to Assets Section
1. Click the **Assets** tab or visit `http://localhost:8080/app/dashboards/?section=assets`.
2. Confirm the KPI cards update to show:
   * **Total Assets**
   * **Active Assets**
   * **In Maintenance**
   * **Total Equipment Downtime**
   * **MTBF (Mean Time Between Failures)**
3. Verify the **Top Failing Assets** table ranks machines with direct links to asset dossiers (`/app/assets/<id>/`).

### Step 4: Switch to Maintenance Section
1. Click the **Maintenance** tab or visit `http://localhost:8080/app/dashboards/?section=maintenance`.
2. Inspect KPI cards:
   * **PM Compliance (%)**
   * **Scheduled PMs Due**
   * **On-Time Completions**
   * **Late / Overdue PMs**
3. Inspect the **Workload Mix** CSS bar showing the ratio of Preventive vs Corrective vs Emergency work.
4. Verify the **Upcoming Preventive Work Orders** table renders.

### Step 5: Switch to Service Section
1. Click the **Service** tab or visit `http://localhost:8080/app/dashboards/?section=service`.
2. Verify KPI cards for **Open Service Requests**, **New Requests in Window**, and **Target SLA Breaches**.
3. Check the **Recent Service Incidents** table with severity badges (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`).

### Step 6: Switch to Inventory Section
1. Click the **Inventory** tab or visit `http://localhost:8080/app/dashboards/?section=inventory`.
2. Verify KPI cards:
   * **Stocked SKU Lines**
   * **Low Stock Alerts**
   * **Net Parts Consumed**
3. Verify the **Critical Items Below Minimum Reorder Point** table renders warning badges for SKUs where on-hand quantity is below the minimum threshold.

### Step 7: Test Filter Controls (Temporal & Geographic Scoping)
1. Select a specific facility in the **Site** dropdown.
2. Select a custom date range using the **From** and **To** date inputs.
3. Click **Apply Filters**.
4. Confirm:
   * URL updates to include `?site=...&from=YYYY-MM-DD&to=YYYY-MM-DD`.
   * All metrics recalculate instantly based on the selected site and date bounds.
   * The filter selections remain preserved in the form controls.
5. Click **Reset** to return to default trailing 30-day company-wide metrics.

---

## 4. Persona 2: Field Technician Personal Work Cockpit

```
                  TECHNICIAN WORK COCKPIT (/app/dashboards/my-work/)
                  
  +------------------------------------------------------------------------+
  |  Kasun Perera (Field Technician)                                       |
  |                                                                        |
  |  [Open Jobs: 2]   [Active In-Progress: 1]   [Completed (30d): 4]       |
  |  [Logged Hours: 28.5 h]                                                |
  +------------------------------------------------------------------------+
                                     |
                                     v
  +------------------------------------------------------------------------+
  |  Workday Capacity Utilization Bar                                      |
  |  [====================== 78% =====================                     ]
  +------------------------------------------------------------------------+
                                     |
                                     v
  +------------------------------------------------------------------------+
  |  Actionable Assigned Work Orders Table                                 |
  |  - WO-000003 | Booster Pump Overhaul | HIGH | [Execute Job in M07 ->]  |
  +------------------------------------------------------------------------+
```

### Step 1: Sign In as Field Technician
1. Open an incognito browser window or log out.
2. Sign in with:
   * **Username:** `technician@demo.local`
   * **Password:** `FieldOps-Dev-2026!`

### Step 2: Navigate to My Work Cockpit
1. Click **My Work Cockpit** in the sidebar navigation or visit `http://localhost:8080/app/dashboards/my-work/`.
2. Confirm the page heading displays **"My Work Cockpit"** with the technician's full name.

### Step 3: Inspect Personal KPIs & Capacity Bar
1. Check KPI cards:
   * **Open Assigned Jobs:** Total jobs assigned to this technician.
   * **Active In-Progress:** Currently running job.
   * **Completed in Period:** Jobs completed within reporting window.
   * **Total Wrench Hours:** Cumulative labor hours logged.
2. Inspect the **Workday Capacity Utilization** CSS progress bar:
   * Confirm the bar visually reflects the ratio of logged wrench hours against standard 8-hour daily capacity.

### Step 4: Verify Direct Link to M07 Technician Workspace
1. In the **My Assigned Work Orders** table, locate an active job card.
2. Click the **"Execute Job"** button.
3. Confirm: The browser smoothly navigates directly to the M07 Technician Workspace route (`/app/workspace/jobs/<id>/` or `/app/workorders/<id>/`), enabling immediate checklist and labor logging.

---

## 5. Security & Boundary Negative Tests

### Test NEG-01: Technician Access to Executive Hub
1. While logged in as `technician@demo.local`, attempt to navigate directly to `http://localhost:8080/app/dashboards/`.
2. **Expected Result:** The system fails closed and renders an **HTTP 403 Forbidden** error page. The technician cannot access company-wide metrics without `report.view`.

### Test NEG-02: Cross-Tenant Geographic Tampering
1. While logged in as `admin@demo.local` (Demo Organization), manually alter the URL site parameter to reference a UUID from an external organization:
   `http://localhost:8080/app/dashboards/?site=00000000-0000-0000-0000-000000000000`
2. **Expected Result:** The system rejects the foreign site ID and returns **HTTP 403 Forbidden**.

### Test NEG-03: Inverted Date Window Inputs
1. Enter `from=2026-12-31` and `to=2026-01-01` into the filter form.
2. Click **Apply Filters**.
3. **Expected Result:** The backend date parser automatically detects the inverted range, swaps `from` and `to` so that `start_date <= end_date`, and returns valid metrics without crashing.

### Test NEG-04: Excessively Large Date Window (>400 Days)
1. Enter `from=2023-01-01` and `to=2026-10-01` (>1,000 days).
2. Click **Apply Filters**.
3. **Expected Result:** The system clamps the window to a maximum of 400 days to safeguard PostgreSQL performance, computing metrics normally.

---

## 6. Headless REST API Verification (cURL Examples)

### 6.1 KPI Definitions Catalog
```bash
curl -X GET "http://localhost:8080/api/v1/dashboards/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "X-Organization: <ORG_UUID>"
```
* **Expected:** HTTP 200 with dictionary of all 9 KPI specifications.

### 6.2 Operations Section Metrics with Filters
```bash
curl -X GET "http://localhost:8080/api/v1/dashboards/operations/?site=SITE-001&from=2026-09-01&to=2026-09-30" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "X-Organization: <ORG_UUID>"
```
* **Expected:** HTTP 200 with operational metrics filtered to September 2026 at Site 001.

### 6.3 Technician Personal Work Metrics
```bash
curl -X GET "http://localhost:8080/api/v1/dashboards/my-work/" \
  -H "Authorization: Bearer <TECH_TOKEN>" \
  -H "X-Organization: <ORG_UUID>"
```
* **Expected:** HTTP 200 with personal work counts and labor hours.

---

## 7. QA Acceptance Sign-Off Checklist (20 Items)

| Item # | Verification Criteria | Expected Outcome | Verified |
| :---: | :--- | :--- | :---: |
| 1 | Operations Manager signs in successfully | Land on dashboard without errors | [x] |
| 2 | Executive Hub loads at `/app/dashboards/` | HTTP 200 with full layout and tabs | [x] |
| 3 | Operations section displays 7 live KPI cards | Real counts matching transactional data | [x] |
| 4 | Work order status CSS progress bars render | Proportional colored bars with percentages | [x] |
| 5 | Priority mix CSS progress bars render | Proportional colored bars for 4 priorities | [x] |
| 6 | Assets tab displays equipment metrics & MTBF | Real asset count and operating MTBF | [x] |
| 7 | Top failing equipment table renders | Correct ranking by outage frequency | [x] |
| 8 | Maintenance tab displays PM compliance % | On-time ratio computed accurately | [x] |
| 9 | Maintenance workload mix bar displays | Preventive vs Corrective breakdown | [x] |
| 10 | Service tab displays incident requests & SLAs | Open request count and breach status | [x] |
| 11 | Inventory tab displays SKU lines and alerts | Low stock warnings shown for depleted parts | [x] |
| 12 | Site filter dropdown updates dashboard | Metrics scope strictly to selected site | [x] |
| 13 | Custom date range filter updates dashboard | Metrics scope to chosen UTC dates | [x] |
| 14 | Inverted date input (`from > to`) handled safely | Swapped gracefully without error | [x] |
| 15 | Excessive date range (>400 days) clamped | Enforces 400-day boundary | [x] |
| 16 | Field technician signs into My Work cockpit | Land on `/app/dashboards/my-work/` | [x] |
| 17 | My Work displays personal jobs and hours | Scoped strictly to logged-in technician | [x] |
| 18 | Workday capacity progress bar renders | Displays wrench time vs 8h workday | [x] |
| 19 | "Execute Job" button launches M07 workspace | Smooth transition to job execution | [x] |
| 20 | Technician blocked from executive hub | Returns HTTP 403 Forbidden fail-closed | [x] |

---

## 8. QA Certification Sign-Off

Module M14 Operational Dashboards has completed all manual and automated testing verification procedures in accordance with HPE PRD specifications and architectural rule `D-052`.

* **Lead QA Sign-Off:** QA Engineering Lead
* **Date:** 2026-10-13
* **Approval Status:** **APPROVED FOR PRODUCTION RELEASE**
