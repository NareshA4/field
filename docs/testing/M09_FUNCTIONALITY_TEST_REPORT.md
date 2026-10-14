# Module 9: Functionality Test Report

**Module:** FON-M09 Spare Parts & Inventory (Business workflows, stock actions, warehouse operations, part lifecycle state machine)

**Environment:** Python 3.14 / Django 5.2.17, pytest 9.1.1, PostgreSQL 16 (Docker container; dedicated `test_*` DB). Branch: `feature/m09-inventory`.

---

## 1. Scope of Functionality Verified

| Functional Domain | Business Capabilities Tested | Status |
|---|---|:---:|
| **Part Catalog Management** | Part definition, unit assignment, min/reorder levels, active/inactive toggles, case-insensitive uniqueness. | **PASS** |
| **Warehouse Operations** | Site association, capacity rules, deactivation protection for active stock. | **PASS** |
| **Inbound Receipts** | Vendor receipt entry, batch stock intake, positive on-hand increments, audit attribution. | **PASS** |
| **Cycle Adjustments** | Inventory count discrepancies (+/- deltas), protection against dipping into reserved quantities. | **PASS** |
| **Scrap / Decommissioning** | Damaged/expired stock write-offs, immutable `SCRAP` movement records, reason capture. | **PASS** |
| **Inter-Warehouse Transfers** | Stock transfers between site warehouses, balanced paired ledger movements (`TRANSFER_OUT` / `TRANSFER_IN`). | **PASS** |
| **Work Order Part Lifecycle** | 6-stage lifecycle: `REQUESTED` $\rightarrow$ `RESERVED` $\rightarrow$ `ISSUED` $\rightarrow$ `CONSUMED` / `RETURNED` $\rightarrow$ `RECONCILED`. | **PASS** |
| **Reorder Replenishment Alerts** | Min-max calculation, Celery notification dispatch, duplicate alert suppression. | **PASS** |

---

## 2. Functional Scenarios & Detailed Verification

### Scenario 1: Stock Inbound Receipt & Balance Creation
- **Action**: Storekeeper receives 10 units of `SEAL-100` into Warehouse `WH-MAIN`.
- **Expected Outcome**: `StockBalance` created with `on_hand=10`, `reserved=0`, `available=10`. Immutable `StockMovement` of type `RECEIPT` with `on_hand_delta=+10` recorded.
- **Verification**: `test_receive_creates_balance_and_append_only_movement` **PASSED**.

### Scenario 2: Cycle Count Correction (Adjustment)
- **Action**: Physical inventory audit reveals 12 units on shelf instead of 10. Storekeeper adjusts by `+2`.
- **Expected Outcome**: `on_hand` becomes `12`. Next, auditor attempts negative adjustment that cuts into reserved parts.
- **Verification**: `test_adjust_count_correction_and_cannot_cut_into_reserved` **PASSED**. Over-adjustment rejected with `Conflict: Insufficient available stock to reduce`.

### Scenario 3: Scrap & Decommissioning
- **Action**: 2 units discovered damaged during transit. Stores scraps 2 units with reason `"Damaged seal gasket"`.
- **Expected Outcome**: `on_hand` reduced by 2; `available` drops; immutable `SCRAP` movement written; audit record logged under `stock.scrapped`.
- **Verification**: `test_scrap_deducts_on_hand_and_creates_scrap_movement` **PASSED**.

### Scenario 4: Inter-Warehouse Stock Transfer
- **Action**: Transfer 5 units from `WH-MAIN` to `WH-REMOTE`.
- **Expected Outcome**: Source warehouse balance decreases by 5 (`TRANSFER_OUT`). Destination warehouse balance increases by 5 (`TRANSFER_IN`). Overall organization stock quantity remains unchanged.
- **Verification**: `test_transfer_between_warehouses_writes_dual_movements` **PASSED**.

### Scenario 5: Full 6-Stage Work Order Part Lifecycle
- **Step 1 (Request)**: Field technician requests 4 units on active work order. Status becomes `REQUESTED`.
- **Step 2 (Reserve)**: Storekeeper reserves 4 units. `reserved` rises from 0 to 4; `available` drops from 10 to 6; `on_hand` remains 10. Status becomes `RESERVED`.
- **Step 3 (Issue)**: Storekeeper issues 4 units to technician. `on_hand` drops from 10 to 6; `reserved` drops from 4 to 0; `outstanding` becomes 4. Status becomes `ISSUED`.
- **Step 4 (Consume)**: Technician uses 3 units on asset. Recorded into `WorkOrderMaterial`. `outstanding` drops to 1.
- **Step 5 (Return)**: Technician returns 1 unused unit to warehouse. `on_hand` rises from 6 to 7. `outstanding` becomes 0. Status becomes `RETURNED`.
- **Step 6 (Reconcile)**: Storekeeper reconciles the line ($4 = 3 + 1$). Status becomes `RECONCILED`.
- **Verification**: `test_full_work_order_part_lifecycle` **PASSED**.

### Scenario 6: Prevention of Stock Depletion / Overselling
- **Action**: Store has 10 units. Technician requests 12. Stores attempts to issue 10 after 2 were issued and 1 returned.
- **Expected Outcome**: Service blocks over-issuing with HTTP 409 Conflict.
- **Verification**: `test_stock_10_issue_2_return_1_then_issue_10_is_rejected` **PASSED**.
