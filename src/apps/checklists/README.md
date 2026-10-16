# FON-M08: Inspection & Checklist Engine

Part of the **FieldOps Nexus ERP** suite (`HPE-PRD-2026-FOPS02`).

---

## 1. Module Overview & Purpose

The **Inspection & Checklist Engine (FON-M08)** provides end-to-end management for digital inspections, quality gates, preventive safety checks, and non-conformance findings across industrial assets and work orders.

In an enterprise asset management environment, maintenance cannot simply be "marked done" by clicking a checkbox. Field technicians must follow standardized procedures, take quantitative measurements, capture photographic evidence, and report defects in real time. 

**FON-M08 answers two critical operational questions:**
1. *What specific checks must be executed and recorded before maintenance work can be signed off?*
2. *Did the asset meet safety, calibration, and operating tolerances, and were any non-conformances flagged and resolved?*

---

## 2. Key Capabilities & Functionality

### 2.1 Dynamic Checklist Builder & Template Versioning
* **Question Types Supported**:
  * **Boolean (Pass / Fail)**: Evaluates normal vs non-compliant conditions (`is_exception = not value`).
  * **Numeric (Tolerance Range)**: Captures quantitative readings with engineering units (e.g., PSI, °C, RPM, Bar) and enforces acceptable `[min_value, max_value]` bounds.
  * **Selection (Single-choice Dropdown)**: Selects from configured options; flags responses matching predefined `exception_options`.
  * **Free Text**: Captures qualitative remarks, serial numbers, and technician notes.
* **Version Locking & Freezing**:
  * Once a template transitions from `DRAFT` to `ACTIVE`, its structure is **frozen**.
  * Any edit creates an auto-incremented new version (e.g. `v1` $\rightarrow$ `v2`), preserving historical integrity for past audits.
* **Work Type & Asset Category Applicability**:
  * Templates can be targeted to specific work order types (`PM`, `BREAKDOWN`, `SAFETY`, `COMMISSIONING`) and asset categories.

### 2.2 Execution Engine & Real-Time Scoring
* **Pass / Fail & Exception Flagging**: Every response is evaluated on submission; out-of-spec or failed answers are flagged as exceptions.
* **Scoring & Completion Percentage**: Real-time progress tracking: `progress_pct = (answered_count / total_items) * 100%`.
* **Execution Runner UI (`runner.html`) & Modal (`_runner_modal.html`)**:
  * Mobile-responsive interface designed for rugged field tablets and smartphones.
  * Inline camera capture (`capture="environment"`) for direct photo upload.
  * Instant exception indicators with inline finding creation.

### 2.3 Non-Conformance Findings & Defect Workflow
* When an inspection item fails or registers an exception, technicians can immediately generate a **Finding**:
  * Severity levels: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`.
  * Description, mitigation steps, and linked evidence.
  * Resolution workflow: Finding must be marked `RESOLVED` with corrective notes before supervisor sign-off.

### 2.4 Evidence Vault & Mandatory Photo Checks
* Questions configured with `evidence_required=True` strictly mandate photo or document proof.
* Attempting to complete an inspection without required attachments is blocked by the backend validation gate.

### 2.5 Work Order Gate (Closure Blocker)
* **Zero-Bypass Quality Gate**: Incomplete inspections strictly block work order closure in `FON-M06`. A work order cannot transition to `COMPLETED` or `CLOSED` until all mandatory checklist items are answered, required photos uploaded, and exceptions documented.

### 2.6 Standalone Inspections
* Inspections can be run directly against an Asset (`FON-M02`) independently of a work order (e.g., routine patrol inspections, annual safety audits).

---

## 3. Architecture & File Structure

```
src/apps/checklists/
├── models.py          # ChecklistTemplate, ChecklistItem, Inspection, InspectionResponse, Finding, InspectionEvidence
├── services.py        # Business logic: state transitions, version freeze, scoring, completion issues gate
├── selectors.py       # Scoped queries, active templates, tenant & site filtering
├── forms.py           # Template forms, item builders, finding creation forms
├── views.py           # Web UI views: templates, inspections, interactive runner, photo upload, modal
├── api_views.py       # REST API endpoints (DRF viewsets with OpenAPI schemas)
├── permissions.py     # Scoped RBAC permissions: template.manage, inspection.execute, inspection.view
├── navigation.py      # Shell sidebar integration
└── urls.py            # Route mappings for UI (/app/...) and API (/api/v1/...)

src/templates/checklists/
├── list.html          # Checklist templates list & version table
├── detail.html        # Template builder & item reordering
├── inspections.html   # Active inspections registry
├── inspection.html    # Inspection detail & finding tracker
├── runner.html        # Interactive execution runner with progress bar & photo dropzone
└── _runner_modal.html # Embeddable runner modal for Work Order screens

tests/checklists/
├── test_m08_checklists.py   # Domain logic, state machine, version freeze, RBAC, REST APIs (30 tests)
├── test_m08_integration.py  # Work order gate integration, asset history immutability (2 tests)
└── test_m08_ui_views.py     # UI rendering, runner workflow, photo upload enforcement (4 tests)
```

---

## 4. REST API Reference

| Endpoint | Method | Role | Description |
|---|---|---|---|
| `/api/v1/checklists/` | GET, POST | Planner, Admin | List and create checklist templates |
| `/api/v1/checklists/{id}/` | GET, PUT, PATCH | Planner, Admin | Retrieve or modify draft template |
| `/api/v1/checklists/{id}/items/` | GET, POST | Planner, Admin | List or add checklist items |
| `/api/v1/checklists/{id}/activate/` | POST | Planner, Admin | Activate template (freezes structure) |
| `/api/v1/inspections/` | GET, POST | Technician, Dispatcher | List or instantiate inspections |
| `/api/v1/inspections/{id}/` | GET | All scoped | View inspection execution details |
| `/api/v1/inspections/{id}/submit-answers/` | POST | Assignee, Dispatcher | Submit atomic responses for items |
| `/api/v1/inspections/{id}/complete/` | POST | Assignee, Dispatcher | Complete inspection (validates quality gate) |
| `/api/v1/inspections/{id}/evidence/` | POST | Assignee, Dispatcher | Upload photo or file evidence |
| `/api/v1/inspections/findings/` | GET, POST | Technician, Supervisor | List or report non-conformance findings |
| `/api/v1/inspections/findings/{id}/resolve/` | POST | Supervisor, Admin | Mark non-conformance finding resolved |

---

## 5. Security, RBAC & Multi-Tenancy

1. **Multi-Tenant Isolation**: Every model inherits `TenantModel`. Unscoped cross-tenant access returns HTTP 404 / 403.
2. **Site-Scoped Access**: Technicians and supervisors only see inspections matching their assigned operational sites.
3. **Execution Guards**: Only the assigned technician or service dispatcher can submit answers or complete an inspection.
4. **Immutability Guarantee**: Once completed, inspections and responses are strictly read-only; no post-completion tampering is permitted.

---

## 6. Test Suite & Verification

The module contains **51 automated tests** covering 100% of functional requirements:
```bash
uv run pytest tests/checklists/ -v
# Result: 51 passed, 0 failed (100% pass rate)
```
See `docs/testing/M08_UNIT_TESTS.md`, `docs/testing/M08_INTEGRATION_TESTS.md`, and `docs/testing/M08_REGRESSION_TESTS.md` for full test proof documents.
