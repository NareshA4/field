# End-to-End Workflow Guide: From Asset Registry (M02) to Asset Hierarchy (M03)

This document provides the complete, step-by-step workflow covering the entire asset lifecycle: from **registering raw physical equipment** to **building multi-level machine hierarchies**, **inspecting visual trees**, **managing lifecycle transitions**, **testing guardrails**, and **verifying cross-module integrations**.

---

## 📑 Table of Contents
1. [Core Terminology & Mental Model](#1-core-terminology--mental-model)
2. [End-to-End Workflow Architecture Map](#2-end-to-end-workflow-architecture-map)
3. [Phase 1: Asset Creation & Registration (M02)](#3-phase-1-asset-creation--registration-m02)
4. [Phase 2: Building Parent-Child Equipment Hierarchies (M03)](#4-phase-2-building-parent-child-equipment-hierarchies-m03)
5. [Phase 3: Visual Inspection in the Hierarchy Hub](#5-phase-3-visual-inspection-in-the-hierarchy-hub)
6. [Phase 4: Relationship Management (Move, Edit, Replace, Detach)](#6-phase-4-relationship-management-move-edit-replace-detach)
7. [Phase 5: Domain Guardrails & Negative Error Testing](#7-phase-5-domain-guardrails--negative-error-testing)
8. [Phase 6: Downstream Cross-Module Integration Verification](#8-phase-6-downstream-cross-module-integration-verification)
9. [Phase 7: Global Component Catalog & CSV Export](#9-phase-7-global-component-catalog--csv-export)
10. [End-to-End Manual QA Checklist](#10-end-to-end-manual-qa-checklist)

---

## 1. Core Terminology & Mental Model

| Term | What It Means in FieldOps Nexus | Real-World Example (HVAC Unit) |
| :--- | :--- | :--- |
| **Asset (M02)** | A single trackable physical equipment record with its own unique tag, serial number, status, site, category, warranty, and service history. | `AHU-01` (Main Unit) or `MTR-104` (Motor). |
| **Root Assembly (M03)** | The top-level machine installed on the plant floor. It has no parent above it. | `AHU-01` (Air Handling Unit 1). |
| **Sub-Assembly (M03)** | A major self-contained section inside a root assembly containing parts of its own. | `FAN-SECT-01` (Cooling Fan Section). |
| **Component (M03)** | A distinct functional unit or mechanism installed inside an assembly. | `DRV-MTR-01` (Drive Motor). |
| **Replaceable Part (M03)** | A wearable or consumable item designed to be swapped out during routine maintenance. | `FLTR-HEPA-200` (HEPA Air Filter). |
| **Asset Tag vs. Part Number** | **Asset Tag:** Unique identifier of *one specific physical piece of hardware*.<br>**Part Number:** Manufacturer catalog SKU used by Inventory (M09) to re-order spares. | **Asset Tag:** `AST-00941`<br>**Part Number:** `FLTR-HEPA-200` |
| **Upstream Lineage** | The full breadcrumb path from a small part back up to the main parent machine. | `AHU-01 › Fan Section › Motor` |

---

## 2. End-to-End Workflow Architecture Map

```
  +----------------------------------------------------------------------------------------------------+
  |                                 PHASE 1: ASSET REGISTRATION (M02)                                  |
  |  1. Register Root Machine (e.g. AHU-01) at Site A                                                  |
  |  2. Register Sub-Items (e.g. Fan Section, Motor, HEPA Filter) at Site A                            |
  |  3. Set Initial Status (DRAFT -> ACTIVE / COMMISSIONED)                                            |
  +----------------------------------------------------------------------------------------------------+
                                                   |
                                                   v
  +----------------------------------------------------------------------------------------------------+
  |                                 PHASE 2: BUILD HIERARCHY TREE (M03)                                |
  |  Option A: Attach existing assets via Detail > Hierarchy Tab ('Attach Child Component')           |
  |  Option B: Register brand-new child directly under parent ('Register brand new child asset')       |
  |  Define Relationship Type (ASSEMBLY, COMPONENT, REPLACEABLE_PART), Quantity & Part Number          |
  +----------------------------------------------------------------------------------------------------+
                                                   |
                                                   v
  +----------------------------------------------------------------------------------------------------+
  |                                 PHASE 3: INSPECT & VERIFY HIERARCHY                                |
  |  • Hierarchy Hub (/app/assets/hierarchy/): KPI cards, Root selector, Interactive Tree             |
  |  • Tree Features: Expand/Collapse ▼, Role badges, Copy Part #, Parts Catalog link, Quick View ℹ   |
  +----------------------------------------------------------------------------------------------------+
                                                   |
                                                   v
  +----------------------------------------------------------------------------------------------------+
  |                                 PHASE 4: LIFECYCLE & MAINTENANCE ACTIONS                            |
  |  • Move / Re-parent component to a new machine                                                     |
  |  • Edit relationship settings (Quantity, Part Number, Notes)                                       |
  |  • Replace / Swap worn-out part with a fresh replacement asset                                     |
  |  • Detach component safely (asset record preserved in Registry)                                   |
  +----------------------------------------------------------------------------------------------------+
                                                   |
                                                   v
  +----------------------------------------------------------------------------------------------------+
  |                              PHASE 5: DOWNSTREAM ERP CROSS-MODULE INTEGRATIONS                      |
  |  • Work Orders (M06) & Incidents (M05): Upstream Machine Breadcrumbs (ROOT › SUB › COMPONENT)      |
  |  • Inventory (M09): 1-Click Part Number Bridge to Warehouse Spares Catalog                         |
  |  • Global Component Master List (/app/assets/components/): Search, Filter & CSV Export            |
  +----------------------------------------------------------------------------------------------------+
```

---

## 3. Phase 1: Asset Creation & Registration (M02)

### Goal: Create the equipment records that will form your machine hierarchy.

### Scenario Example Equipment to Register:
We will register a 4-level equipment structure located at the **Austin Plant (`ATX-01`)**:
1. `AHU-MAIN-01` (Root Air Handling Unit)
2. `AHU-FAN-SECT` (Fan Sub-Assembly)
3. `AHU-DRV-MTR` (Drive Motor Component)
4. `AHU-HEPA-FLTR` (Replaceable Air Filter)

---

### Step 1.1: Register the Root Machine (`AHU-MAIN-01`)
1. Log in to FieldOps Nexus (`http://localhost:8080/accounts/login/`) as `admin@demo.local` (`FieldOps-Dev-2026!`).
2. Go to **Assets** $\rightarrow$ **Asset Registry** (`/app/assets/`) and click **`+ Register Asset`** (or go to `/app/assets/new/`).
3. Fill out the form:
   - **Asset Tag:** `AHU-MAIN-01`
   - **Name:** `Primary Cleanroom Air Handling Unit`
   - **Category:** `HVAC & Environmental`
   - **Site:** `ATX-01 · Austin Facility`
   - **Zone / Location:** `Building A · Zone 1`
   - **Manufacturer:** `Trane Technologies`
   - **Model:** `Climate-Pro 5000`
   - **Serial Number:** `TRN-2026-9901`
   - **Commission Date:** Select today's date.
   - **Warranty Ref:** `WRN-TRANE-3YR`
   - **Description:** `Main rooftop intake air handler.`
4. Click **Save Asset**.
5. **Verify:** You are redirected to the asset detail page (`/app/assets/<id>/`). The asset status shows `ACTIVE`.

---

### Step 1.2: Register Sub-Components as Assets
Repeat the creation step for the child assets at the **same site (`ATX-01`)**:

1. **Sub-Assembly:**
   - **Tag:** `AHU-FAN-SECT` | **Name:** `Blower Fan Sub-Assembly` | **Site:** `ATX-01` | **Category:** `HVAC & Environmental`
2. **Component:**
   - **Tag:** `AHU-DRV-MTR` | **Name:** `5HP Electric Drive Motor` | **Site:** `ATX-01` | **Category:** `Motors & Drives`
3. **Replaceable Spare:**
   - **Tag:** `AHU-HEPA-FLTR` | **Name:** `High-Efficiency HEPA Filter Unit` | **Site:** `ATX-01` | **Category:** `Filters & Consumables`

---

## 4. Phase 2: Building Parent-Child Equipment Hierarchies (M03)

### Goal: Physically assemble the child assets under their respective parent machines.

```
Target Structure:
AHU-MAIN-01 (Root Assembly)
 ├── AHU-FAN-SECT (Assembly)
 │    └── AHU-DRV-MTR (Component)
 └── AHU-HEPA-FLTR (Replaceable Part · Part #: FLTR-HEPA-200)
```

---

### Step 2.1: Attach Sub-Assembly and Replaceable Part to Root (`AHU-MAIN-01`)
1. Open the detail page of `AHU-MAIN-01` (`/app/assets/<id>/`).
2. Click the **Hierarchy** tab in the sub-navigation bar (`Overview | Documents | Meters | History | Hierarchy | Coverage | Labels`).
3. Look at the right panel card titled **Attach Child Component**.
4. **Attach Fan Section:**
   - **Child Asset:** Select `AHU-FAN-SECT (Blower Fan Sub-Assembly)`.
   - **Relationship Type:** Select `Assembly`.
   - **Quantity:** `1`.
   - **Part Number:** *(Leave blank or enter `FAN-MOD-500`)*.
   - Click **`+ Add to hierarchy`**.
5. **Attach HEPA Filter:**
   - **Child Asset:** Select `AHU-HEPA-FLTR (High-Efficiency HEPA Filter Unit)`.
   - **Relationship Type:** Select `Replaceable Part`.
   - **Quantity:** `2`.
   - **Part Number:** `FLTR-HEPA-200`.
   - **Notes:** `Intake pre-filter chamber.`
   - Click **`+ Add to hierarchy`**.

---

### Step 2.2: Attach Motor to the Fan Section (Creating Multi-Level Depth)
1. In the tree view on the left, click on **`AHU-FAN-SECT`** to navigate to its detail page.
2. Click the **Hierarchy** tab.
3. In the **Attach Child Component** form:
   - **Child Asset:** Select `AHU-DRV-MTR (5HP Electric Drive Motor)`.
   - **Relationship Type:** Select `Component`.
   - **Quantity:** `1`.
   - **Part Number:** `MTR-5HP-460V`.
   - Click **`+ Add to hierarchy`**.

---

### Step 2.3: Alternative Direct Creation Method
You can also register brand-new assets directly into the hierarchy without registering them first:
1. On any parent asset's **Hierarchy** tab, click **`+ Register brand new child asset`** at the bottom of the right panel.
2. Fill in the asset details and the **Hierarchy Relationship to Parent** section in one single screen.
3. Click **Save Asset** $\rightarrow$ It is created and linked in one atomic step.

---

## 5. Phase 3: Visual Inspection in the Hierarchy Hub

### Goal: Inspect the complete tree structure, KPIs, and interactive controls.

**Page URL:** `/app/assets/hierarchy/` (or via top navbar **Assets** $\rightarrow$ **Hierarchy Hub**)

1. **Verify KPI Summary Cards:**
   - **Root Assemblies:** Increases by 1 (`AHU-MAIN-01` is now recognized as a root).
   - **Total Sub-Items:** Reflects the newly attached children.
   - **Replaceable Parts:** Shows the count of installed parts tagged as `Replaceable Part`.
2. **Select the Root Assembly:**
   - In the left sidebar (**Root Assemblies**), click on `AHU-MAIN-01`.
3. **Inspect the Visual Tree (Right Panel):**
   - **Expand/Collapse:** Click `▼` beside `AHU-MAIN-01` and `AHU-FAN-SECT` to toggle branches.
   - **Role Badges:** Confirm distinct badge colors and icons:
     - `Root Assembly` (Hard-drive icon) on `AHU-MAIN-01`
     - `Assembly` (Layers icon) on `AHU-FAN-SECT`
     - `Component` (Package icon) on `AHU-DRV-MTR`
     - `Replaceable Part` (Refresh icon) on `AHU-HEPA-FLTR`
   - **Part Number Pills:**
     - On `AHU-HEPA-FLTR`, click **`📋 Part # FLTR-HEPA-200`** $\rightarrow$ Verify clipboard copy notification.
     - Click the **`↗` (external link icon)** $\rightarrow$ Verify it opens the Inventory Parts Catalog filtered to that part.
   - **Quick Inspection Modal:**
     - Click the **`ℹ` (info icon)** on any tree node.
     - Verify the modal pop-up displays technical specs, serial number, site, parent link, and child components.
   - **Tree Actions Menu (`•••`):**
     - Click `•••` on any node to verify the quick actions dropdown (*Quick Inspection*, *Focus in Hierarchy*, *Register Child Under This*, *Move / Re-parent*, *Replace Part / Swap*, *Detach from Parent*).

---

## 6. Phase 4: Relationship Management (Move, Edit, Replace, Detach)

### Goal: Verify all operational changes supported on installed components.

---

### Step 4.1: Edit Relationship Settings
1. Open the child asset `AHU-HEPA-FLTR` $\rightarrow$ click **Hierarchy** tab.
2. In the right panel under **Parent Asset**, click the **`Edit relationship settings`** accordion.
3. Change **Quantity** from `2` to `4` and update **Notes** to *"Upgraded to 4-stage filter set"*.
4. Click **Save changes**.
5. **Verify:** The parent summary card and tree view update immediately to show `×4`.

---

### Step 4.2: Move / Re-Parent a Component
1. Open `AHU-DRV-MTR` $\rightarrow$ click **Hierarchy** tab.
2. Under the **Parent Asset** card, locate the **"Move to new parent:"** section.
3. In the dropdown, select `AHU-MAIN-01` instead of `AHU-FAN-SECT`.
4. Click the **Move** button.
5. **Verify:** The motor is detached from the fan sub-assembly and attached directly under the root machine `AHU-MAIN-01`.

---

### Step 4.3: Atomic Part Replacement (Swap)
1. On an installed part (e.g. `AHU-HEPA-FLTR`), click `•••` $\rightarrow$ **Replace Part / Swap** (or use the replace action).
2. Select a fresh replacement asset from inventory.
3. Submit the replacement.
4. **Verify:** The old asset is detached and the new replacement asset is installed in its exact hierarchy slot in a single transaction.

---

### Step 4.4: Detach a Component
1. Open `AHU-HEPA-FLTR` $\rightarrow$ **Hierarchy** tab.
2. Click the red **`Detach from parent`** button.
3. Confirm the browser dialog: *"Detach AHU-HEPA-FLTR from AHU-MAIN-01? The asset itself is kept."*
4. **Verify:**
   - The child link is removed from the hierarchy tree.
   - Go to **Asset Registry** (`/app/assets/`) $\rightarrow$ Confirm `AHU-HEPA-FLTR` is still in the database as an active standalone asset.

---

## 7. Phase 5: Domain Guardrails & Negative Error Testing

### Goal: Verify that the application strictly enforces physical and business invariants with descriptive error banners.

| Test Case | Action to Perform | Expected Result |
| :--- | :--- | :--- |
| **1. Self-Parenting** | Open `AHU-MAIN-01` $\rightarrow$ Hierarchy tab $\rightarrow$ select `AHU-MAIN-01` in the **Child Asset** dropdown $\rightarrow$ Click Add. | ❌ Red alert banner: `Cannot attach an asset as a child of itself.` |
| **2. Direct Loop ($A \to B \to A$)** | With `AHU-FAN-SECT` as child of `AHU-MAIN-01`, open `AHU-FAN-SECT` and try attaching `AHU-MAIN-01` as its child. | ❌ Red alert banner: `Hierarchy cycle detected: child is already an ancestor of parent.` |
| **3. Cross-Site Isolation** | Create an asset at `DFW-01` (Dallas) and try attaching it to `AHU-MAIN-01` at `ATX-01` (Austin). | ❌ Red alert banner: `Asset and child must belong to the same physical site.` |
| **4. Retired Parent Guardrail** | Transition an asset status to `RETIRED` or `DISPOSED`, then try attaching a child to it. | ❌ Red alert banner: `Cannot attach components to a retired or disposed asset.` |
| **5. Invalid Quantity** | In the Attach Component form, enter `0` or `-2` in the **Quantity** field. | ❌ Form field error: `Ensure this value is greater than or equal to 1.` |

---

## 8. Phase 6: Downstream Cross-Module Integration Verification

---

### 8.1 Upstream Lineage in Work Orders (M06) & Incidents (M05)
1. Go to **Work Orders** (`/app/work-orders/`) $\rightarrow$ Click **`+ New Work Order`**.
2. Select the nested component `AHU-DRV-MTR` as the target asset.
3. Save and open the Work Order detail page.
4. **Verify Header & Job Card:**
   $$\text{Machine Path: } \mathbf{AHU\text{-}MAIN\text{-}01} \ \mathbf{›} \ \mathbf{AHU\text{-}FAN\text{-}SECT} \ \mathbf{›} \ \mathbf{AHU\text{-}DRV\text{-}MTR}$$
5. Click on `AHU-MAIN-01` in the breadcrumb $\rightarrow$ Verify it jumps directly to the parent machine's hierarchy view.

---

### 8.2 Inventory Parts Catalog Bridge (M09)
1. On any tree node card or component row that has a **Part Number** assigned (e.g. `FLTR-HEPA-200`):
2. Click the **`↗` external link** icon next to the part number.
3. **Verify:** Browser opens `/app/inventory/parts/?q=FLTR-HEPA-200` showing live stock levels, unit pricing, and bin location in the warehouse.

---

## 9. Phase 7: Global Component Catalog & CSV Export

**Page URL:** `/app/assets/components/` (Top navbar: **Assets** $\rightarrow$ **Component Catalog**)

1. **Summary Metrics:** Verify the 3 top cards: **Total Installed Items**, **Replaceable Spare Parts**, and **Missing Part Number**.
2. **Search & Filters:**
   - **Search Box:** Type `HEPA` or `5HP` $\rightarrow$ table filters immediately.
   - **Site Filter:** Filter by `ATX-01` or `All Sites`.
   - **Role Filter:** Filter by `Assembly`, `Component`, or `Replaceable Part`.
   - **Part # Status:** Filter by `Has Part Number` vs `Missing Part Number`.
3. **Export CSV:**
   - Click the green **`Export CSV`** button in the top right.
   - Open the downloaded CSV and verify all filtered component rows, parent tags, roles, quantities, and part numbers are exported.
4. **Row Actions:**
   - Click `ℹ` for the **Quick View Modal**.
   - Click `🌲` (git-branch icon) to jump to that component's position in the hierarchy tree.

---

## 10. End-to-End Manual QA Checklist

| Step | Scope | Verification Item | Status |
| :---: | :--- | :--- | :---: |
| **1** | **M02 Registry** | Create Root Asset & Child Assets with categories, sites, and serial numbers. | [ ] PASS |
| **2** | **M03 Hierarchy** | Attach child components with roles (`ASSEMBLY`, `COMPONENT`, `REPLACEABLE_PART`). | [ ] PASS |
| **3** | **M03 Hierarchy** | Register a brand-new child asset directly under a parent in one step. | [ ] PASS |
| **4** | **Hierarchy Hub** | KPI cards compute accurately; root selector switches trees; visual tree renders. | [ ] PASS |
| **5** | **Tree Controls** | Expand/collapse branches, role badges, 1-click part # copy, Quick View modal. | [ ] PASS |
| **6** | **Management** | Edit relationship settings, re-parent (move) component, and detach component. | [ ] PASS |
| **7** | **Guardrails** | Self-parenting, cyclic loops, cross-site pairing, and retired assets rejected with banners. | [ ] PASS |
| **8** | **M05/M06 Lineage** | Work Orders & Incidents display upstream machine breadcrumb trail. | [ ] PASS |
| **9** | **M09 Bridge** | Part number links redirect smoothly to Inventory Parts Catalog. | [ ] PASS |
| **10** | **Component Catalog**| Filter by site/role/part status, keyword search, and CSV export functioning. | [ ] PASS |
