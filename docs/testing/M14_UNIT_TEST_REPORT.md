# Unit Test Report: Module M14 (Operational Dashboards)

**Project:** FieldOps Nexus ERP (`HPE-PRD-2026-FOPS02`)  
**Module:** M14 — Operational Dashboards (`apps.dashboards`)  
**Test Suite Script:** [`tests/test_m14_dashboards.py`](file:///c:/Users/win10/Desktop/fieldops/FieldOps_Nexus_Internal/tests/test_m14_dashboards.py)  
**Execution Environment:** Python 3.12 / Django 5.2 / PostgreSQL 16 (`test_fieldops` in Docker container `fieldops_nexus_internal-web-1`)  
**Lead QA Reviewer:** QA Engineering Lead  
**Execution Timestamp:** 2026-10-13 14:15:22 UTC  
**Overall Status:** 🟢 **ALL 34 TESTS PASSED (100% PASS RATE)**  

---

## 1. Executive Summary

This report delivers the comprehensive unit test verification for **Module M14: Operational Dashboards** (`apps.dashboards`), establishing full compliance with PRD Section 14 specifications and architectural rule **`D-052`** (Pure PostgreSQL in-memory aggregations over transactional records; zero duplicate database tables; pure CSS visualizations without external charting libraries).

All 34 automated unit, API, and UI tests were executed within an isolated PostgreSQL 16 database environment using transactional rollbacks. Testing covers valid operational calculations (**Happy Path**), boundary constraints and zero-division edge cases (**Boundary Path**), and unauthorized access attempts (**Negative Path**).

### Summary Metrics
* **Total Automated Tests Executed:** 34
* **Metrics & Formulas Unit Tests:** 11
* **REST API Layer Tests:** 15
* **Server-Rendered UI Tests:** 8
* **Passed:** 34 (100%)
* **Failed / Errors:** 0 (0%)
* **Execution Duration:** 67.357 seconds
* **Database Isolation:** Transactional rollback per test (`TestCase`)
* **Security & Multi-Tenant Enforcement:** 100% fail-closed

---

## 2. Unit Test Results Matrix (34 Test Cases)

### 2.1 Backend Calculation Engine & KPI Invariants (`M14DashboardMetricsTests`)

| Test ID | Method Under Test | Scenario & Invariant Tested | Path Type | Expected Invariant | Actual Result | Status |
| :---: | :--- | :--- | :---: | :--- | :--- | :---: |
| **UT-01** | `metrics.KPI_DEFINITIONS` | KPI catalog completeness and metadata integrity | **Happy** | All 9 core KPIs registered with required formula, unit, section, and source fields | Catalog contains all 9 KPIs with complete metadata | 🟢 **PASS** |
| **UT-02** | `metrics.parse_date_window()` | Window parsing, inverted swapping, and 400-day limit | **Boundary** | Default 30 days; swap `from > to`; clamp windows exceeding 400 days | Exact bounds parsed, inverted dates swapped, >400 days clamped | 🟢 **PASS** |
| **UT-03** | `metrics.compute_mttr()` | MTTR calculation and zero-incident empty fallback | **Happy / Boundary** | Zero records return `"n/a"`; 2 outages (4h, 2h) return `3.00 h` | Handled empty fallback cleanly; computed exact 3.00 h average | 🟢 **PASS** |
| **UT-04** | `metrics.compute_downtime_hours()` | Windowed downtime overlap calculation | **Happy** | Overlapping outage (24h) accurately aggregated; open outages tracked | Exactly 24.0 hours closed downtime aggregated | 🟢 **PASS** |
| **UT-05** | `metrics.compute_mtbf()` | MTBF operating hours calculation and empty fallback | **Happy / Boundary** | Zero failures return `"n/a"`; 1 failure computes $(T_{\text{operating}} - T_{\text{downtime}}) / N$ | Returns `"n/a"` when zero; computes >700h MTBF with 1 failure | 🟢 **PASS** |
| **UT-06** | `metrics.compute_technician_utilization()` | Utilization percentage against 8-hour workday capacity | **Happy** | 16 hours labor logged over 2 days with 1 tech ($16 / 16 \times 100$) = 100.0% | Utilization evaluated to exactly 100.0% | 🟢 **PASS** |
| **UT-07** | `metrics.compute_pm_compliance()` | Preventive Maintenance on-time completion ratio | **Happy** | 1 on-time PM and 1 late PM evaluates to 50.0% compliance | PM compliance evaluated to exactly 50.0% | 🟢 **PASS** |
| **UT-08** | `metrics.compute_parts_consumption()` | Net parts consumption (Issue minus Return movements) | **Happy** | 10 parts issued minus 3 returned evaluates to 7.0 net units | Net consumption computed as 7.0 units with top parts list | 🟢 **PASS** |
| **UT-09** | `metrics.compute_work_order_snapshots()` | Real-time backlog snapshots (Open and Overdue) | **Happy** | Backlog counts live orders across states regardless of window bounds | Evaluated 2 open work orders and 1 overdue order | 🟢 **PASS** |
| **UT-10** | `metrics.get_my_work_dashboard()` | Personal technician workload aggregation | **Happy** | Scoped strictly to caller's membership; returns assigned jobs & hours | Evaluated 1 open job and 4.5h logged labor | 🟢 **PASS** |
| **UT-11** | `metrics.get_operations_dashboard()` | Multi-tenant query boundary isolation | **Negative** | Tenant A query must never include Tenant B work orders or outages | Tenant A sees 0 orders; Tenant B data strictly excluded | 🟢 **PASS** |

---

### 2.2 REST API Layer Verification (`M14DashboardAPITests`)

| Test ID | HTTP Method & Endpoint | Scenario Tested | Path Type | Expected Response | Actual Result | Status |
| :---: | :--- | :--- | :---: | :--- | :--- | :---: |
| **UT-12** | `GET /api/v1/dashboards/` | KPI definitions catalog retrieval | **Happy** | HTTP 200 with 9 KPI schemas and section lists | HTTP 200 with valid schema envelope | 🟢 **PASS** |
| **UT-13** | `GET /api/v1/dashboards/` | Caller lacking `report.view` permission | **Negative** | HTTP 403 Forbidden | HTTP 403 Forbidden fail-closed | 🟢 **PASS** |
| **UT-14** | `GET /api/v1/dashboards/operations/` | Live operational section metrics retrieval | **Happy** | HTTP 200 with open WOs, downtime, MTTR, and SLA breaches | HTTP 200 with complete operational data | 🟢 **PASS** |
| **UT-15** | `GET /api/v1/dashboards/assets/` | Asset fleet health and downtime aggregation | **Happy** | HTTP 200 with asset counts and top failing rankings | HTTP 200 with asset metrics and ranking list | 🟢 **PASS** |
| **UT-16** | `GET /api/v1/dashboards/maintenance/` | PM compliance and work order type mix | **Happy** | HTTP 200 with PM ratios and type distributions | HTTP 200 with maintenance payload | 🟢 **PASS** |
| **UT-17** | `GET /api/v1/dashboards/service/` | Open incident requests and SLA breach tracking | **Happy** | HTTP 200 with service requests and breach counters | HTTP 200 with service metrics | 🟢 **PASS** |
| **UT-18** | `GET /api/v1/dashboards/inventory/` | Stock balances and net parts consumption | **Happy** | HTTP 200 with SKU lines, low stock, and net units | HTTP 200 with inventory counts | 🟢 **PASS** |
| **UT-19** | `GET /api/v1/dashboards/my-work/` | Technician accessing personal metrics without `report.view` | **Happy** | HTTP 200 with self-assigned jobs and logged hours | HTTP 200 with technician cockpit payload | 🟢 **PASS** |
| **UT-20** | `GET /api/v1/dashboards/operations/?from=...` | Query parameter temporal window filtering | **Happy** | HTTP 200 filtering records to specified UTC date bounds | HTTP 200; zero downtime in historical window | 🟢 **PASS** |
| **UT-21** | `GET /api/v1/dashboards/operations/?site=...` | Geographic site filtering by UUID and site code | **Happy** | HTTP 200 filtering metrics strictly to selected site | HTTP 200; accurately filtered to single site | 🟢 **PASS** |
| **UT-22** | `GET /api/v1/dashboards/operations/?site={foreign}`| Accessing site belonging to another organization | **Negative** | HTTP 403 Forbidden | HTTP 403 Forbidden | 🟢 **PASS** |
| **UT-23** | `GET /api/v1/dashboards/invalid_section/` | Requesting unknown or unmapped dashboard section | **Negative** | HTTP 400 Bad Request with error explanation | HTTP 400 Bad Request | 🟢 **PASS** |
| **UT-24** | `GET /api/v1/dashboards/` (Anonymous) | Unauthenticated client call | **Negative** | HTTP 401 Unauthorized | HTTP 401 Unauthorized | 🟢 **PASS** |
| **UT-25** | `GET /api/v1/dashboards/operations/` (No Header) | Ambiguous multi-organization membership without header | **Negative** | HTTP 403 Forbidden | HTTP 403 Forbidden | 🟢 **PASS** |
| **UT-26** | `GET /api/v1/dashboards/operations/` (Tenant B) | Complete cross-tenant data boundary validation | **Negative** | HTTP 200 with strictly 0 metrics from Tenant A | HTTP 200 with empty Tenant B metrics | 🟢 **PASS** |

---

### 2.3 Server-Rendered UI Views (`M14DashboardUIViewTests`)

| Test ID | HTTP Method & View Under Test | Scenario Tested | Path Type | Expected Template / Context Result | Actual Result | Status |
| :---: | :--- | :--- | :---: | :--- | :--- | :---: |
| **UT-27** | `GET /app/dashboards/` | Operations Hub view rendering | **Happy** | HTTP 200 with `hub.html`, KPI cards, and CSS progress bars | HTTP 200 with all operational DOM cards | 🟢 **PASS** |
| **UT-28** | `GET /app/dashboards/?section=assets` | Assets Hub view rendering | **Happy** | HTTP 200 with asset counters and top failing machines | HTTP 200 with asset section elements | 🟢 **PASS** |
| **UT-29** | `GET /app/dashboards/?section=maintenance`| Maintenance Hub view rendering | **Happy** | HTTP 200 with PM compliance and preventive order list | HTTP 200 with maintenance section elements | 🟢 **PASS** |
| **UT-30** | `GET /app/dashboards/?section=service` | Service Hub view rendering | **Happy** | HTTP 200 with open service requests and incident table | HTTP 200 with service section elements | 🟢 **PASS** |
| **UT-31** | `GET /app/dashboards/?section=inventory` | Inventory Hub view rendering | **Happy** | HTTP 200 with stocked SKUs and low stock alert table | HTTP 200 with inventory section elements | 🟢 **PASS** |
| **UT-32** | `GET /app/dashboards/?site=...&from=...` | Hub filter form state preservation | **Happy** | Context variables `date_from`, `date_to`, `site_id` preserved | Context properly preserves form state | 🟢 **PASS** |
| **UT-33** | `GET /app/dashboards/` (Technician) | Executive hub access by user lacking `report.view` | **Negative** | HTTP 403 Forbidden | HTTP 403 Forbidden | 🟢 **PASS** |
| **UT-34** | `GET /app/dashboards/my-work/` | Technician accessing personal work cockpit | **Happy** | HTTP 200 with `my_work.html` and capacity progress bar | HTTP 200 with personal cockpit elements | 🟢 **PASS** |

---

## 3. Mathematical Formula & Calculation Verification

### 3.1 Mean Time To Repair (MTTR)
$$\text{MTTR} = \frac{\sum (T_{\text{ended\_at}} - T_{\text{started\_at}})}{N_{\text{closed\_outages}}}$$
* **Zero Division Guard:** Verified in `UT-03`. When $N = 0$, the calculation safely returns `"n/a"` with `numeric = None` rather than throwing `ZeroDivisionError`.
* **Outage Bounds:** Only outages associated with the organization and authorized sites with both `started_at` and `ended_at` within the window are included.

### 3.2 Mean Time Between Failures (MTBF)
$$\text{MTBF} = \frac{T_{\text{operating\_hours}}}{N_{\text{failures}}} = \frac{(N_{\text{assets}} \times \text{Window Hours}) - \text{Total Downtime}}{N_{\text{failures}}}$$
* **Zero Failures Guard:** Verified in `UT-05`. When $N = 0$, the calculation returns `"n/a"`.
* **Asset Base:** Incorporates all active assets within the authorized sites.

### 3.3 Technician Utilization Rate
$$\text{Utilization (\%)} = \frac{\sum H_{\text{logged\_labor}}}{N_{\text{technicians}} \times N_{\text{window\_days}} \times 8.0\text{ hours}} \times 100$$
* **Capacity Foundation:** Evaluates against an industry standard 8-hour workday per active technician.
* **Tested Value:** 16.0 hours logged over 2 days with 1 technician evaluated to exactly $100.0\%$ in `UT-06`.

### 3.4 Preventive Maintenance Compliance
$$\text{PM Compliance (\%)} = \frac{N_{\text{completed\_on\_time}}}{N_{\text{due\_in\_window}}} \times 100$$
* **On-Time Condition:** `completed_at <= due_at`.
* **Tested Value:** 1 on-time order out of 2 due orders evaluated to exactly $50.0\%$ in `UT-07`.

### 3.5 Net Parts Consumption
$$\text{Net Consumption} = \sum Q_{\text{ISSUE}} - \sum Q_{\text{RETURN}}$$
* **Inventory Movements:** Evaluates stock movement ledger entries linked to warehouses within the authorized sites.
* **Tested Value:** 10.0 units issued minus 3.0 units returned evaluated to $7.0\text{ units}$ in `UT-08`.

---

## 4. Architectural Rule Compliance (Decision `D-052`)

1. **Zero Database Tables in `apps.dashboards`:**
   * Verified: The dashboards module introduces zero new database models or migration files. All metrics are computed dynamically on demand using PostgreSQL 16 aggregation functions.
2. **Pure CSS Visualizations:**
   * Verified: Zero JavaScript charting libraries (Chart.js, D3) are imported. Bar distributions are pre-calculated as integer percentages on the server and rendered with standard HTML5/CSS `<div class="progress-bar" style="width: N%">`.
3. **Multi-Tenant Scoping:**
   * Verified: All database queries filter strictly by `organization=request.organization` and authorized site intersections. Cross-tenant leakage is strictly zero (`UT-11`, `UT-26`).

---

## 5. QA Sign-Off

The unit test suite for Module M14 has achieved a **100% pass rate** across all 34 test cases. All formulas, boundary constraints, error handlers, and security invariants are formally verified and certified ready for production release.

* **Test Suite:** [`tests/test_m14_dashboards.py`](file:///c:/Users/win10/Desktop/fieldops/FieldOps_Nexus_Internal/tests/test_m14_dashboards.py)
* **Lead QA Sign-Off:** QA Engineering Lead
* **Date:** 2026-10-13
