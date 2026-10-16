# Use of Module M03 (Asset Hierarchy) in FieldOps Nexus

## 1. Executive Summary & Core Purpose

**Module M03 (Asset Hierarchy)** is the foundational structural backbone for complex physical equipment and machinery within the **FieldOps Nexus Enterprise Operations & Field Service Management ERP**.

While **M02 (Asset Registry)** manages individual asset identity, specifications, and lifecycle states as standalone entities, **M03 (Asset Hierarchy)** models how physical assets, sub-assemblies, and replaceable parts physically and logically interconnect within parent machines, plant systems, and multi-tier equipment configurations.

### Key Mission Statement
> *"Transform a flat registry of equipment into an intelligent, multi-tier physical topology—enabling precise failure localization, rapid component swapping, unified preventive maintenance rollups, and end-to-end inventory traceability."*

---

## 2. Strategic Role Across the FieldOps Nexus Ecosystem

Module M03 does not operate in isolation; it bridges asset master data with daily field maintenance, incident triage, work execution, and spare parts logistics.

```
                  ┌─────────────────────────────────────┐
                  │    M01: Site & Location Master      │
                  │  (Physical Site Boundaries & Zones) │
                  └──────────────────┬──────────────────┘
                                     │ Enforces Site Scoping
                                     ▼
                  ┌─────────────────────────────────────┐
                  │       M02: Asset Registry           │
                  │    (Authoritative Master Data)      │
                  └──────────────────┬──────────────────┘
                                     │ Provides Assets
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      M03: ASSET HIERARCHY                              │
│   • Directed Parent-Child Edge Graph (AssetComponent)                  │
│   • Assembly / Component / Replaceable Part Roles                      │
│   • Concurrency-Safe Tree Invariant Engine (Advisory Locks)            │
│   • Cycle, Depth, and Cross-Tenant Guardrails                          │
└───────┬──────────────┬───────────────┬────────────────┬──────────────┬─┘
        │              │               │                │              │
        ▼              ▼               ▼                ▼              ▼
┌──────────────┐┌──────────────┐┌──────────────┐┌──────────────┐┌──────────────┐
│  M04: PM &   ││  M05: Incidents││ M06: Work   ││ M07: Tech    ││ M09: Parts   │
│  Schedules   ││  & Failures  ││  Orders      ││  Workspace   ││  Catalog     │
│ (Lineage PM) ││ (Breadcrumbs)││ (Job Trails) ││(Quick-Inspect)││ (1-Click)   │
└──────────────┘└──────────────┘└──────────────┘└──────────────┘└──────────────┘
```

---

## 3. Detailed Inter-Module Integration & Use

### 3.1. Integration with M01 (Site & Location Master)
- **Physical Boundary Enforcement**: A parent machine located at "Site Alpha / Building B" can only incorporate children that physically reside in the exact same site.
- **Cross-Site Containment**: Prevents logistical inconsistencies (e.g., an engine in New York cannot be attached to a chassis in Chicago without first executing an official site transfer).

### 3.2. Integration with M02 (Asset Registry)
- **Zero Duplicate Master Records**: M03 models relationships via directed edges (`AssetComponent`) without cloning asset records.
- **Lifecycle Status Sync**: When a component is removed or replaced during maintenance, M03 automatically transitions the old part's status to `UNDER_MAINTENANCE` and the newly installed part to `ACTIVE`.

### 3.3. Integration with M05 (Incident & Breakdown Management)
- **Upstream Machine Context**: When an incident ticket is raised against a small sensor or valve, technicians instantly see the entire machine lineage at the top of the ticket:
  $$\text{Root Machine (Turbine Gen-4)} \longrightarrow \text{Sub-Assembly (Hydraulic Unit)} \longrightarrow \text{Component (Pressure Valve)}$$
- **Rapid Triage**: Dispatchers immediately understand whether an alert is isolated or threatens a high-criticality parent production line.

### 3.4. Integration with M06 (Work Orders) & M07 (Technician Workspace)
- **Hierarchy Breadcrumbs (`{% hierarchy_trail %}`)**: Rendered automatically inside work order headers and job cards with 1-click navigation to the interactive equipment tree.
- **Field Component Swap Action**: Technicians in the field can perform atomic part replacements directly from the job card or tree view:
  1. Detaches worn component.
  2. Updates old component to `UNDER_MAINTENANCE`.
  3. Links new replacement asset.
  4. Records immutable audit entry with actor, timestamp, and notes.

### 3.5. Integration with M09 (Inventory & Spare Parts Catalog)
- **Direct 1-Click Parts Bridge**: Every node in the tree and component list with an assigned `part_number` includes a direct navigation link to `/app/inventory/parts/?q=<part_number>`.
- **Fast Stock Lookup**: Field engineers can instantly check warehouse stock levels, bin locations, and reorder thresholds for any installed sub-part without manual copy-pasting.

### 3.6. Integration with M10 (Warranty & Contracts) & M15 (Audit Trails)
- **Warranty Cascading**: Identifies if a broken component is covered under its parent machine's Master AMC or its own dedicated OEM warranty.
- **Tamper-Evident Audit Trails**: Every relationship change (`ADD`, `MOVE`, `REPLACE`, `REMOVE`) generates a permanent audit event recording the user, old parent, new parent, and operational reason.

---

## 4. Key Functional Capabilities

| Capability | Description | Business Benefit |
| :--- | :--- | :--- |
| **Interactive Tree Hub** | Visual multi-level equipment hierarchy explorer with search, role filters, and density controls. | Instant visual clarity for complex machinery topologies. |
| **Atomic Part Replacement** | Single-step replacement workflow swapping defective parts with new assets. | Eliminates manual multi-step detach/attach mistakes in the field. |
| **Depth & Cycle Guardrails** | Automated graph validation rejecting cycles ($A \to B \to C \to A$) and limiting depth ($D \le 10$). | Prevents infinite loops, stack overflows, and data corruption. |
| **PostgreSQL Advisory Locking** | Transactional concurrency lock (`pg_advisory_xact_lock`) on hierarchy modifications. | Prevents race conditions during concurrent field operations. |
| **Quick-View Inspection Modal** | High-density pop-up drawer showing specifications, parent link, installed children, and parts catalog links. | Eliminates unnecessary page loads during fast-paced inspections. |

---

## 5. Typical Real-World Industry Scenarios

### Scenario A: Complex Manufacturing Production Line
- **Setup**: A manufacturing plant has a primary Bottling Line (Root Asset) containing 4 Conveyor Sub-Assemblies, each containing 6 Servo Motors, Gearboxes, and Emergency Stop Relays.
- **M03 Value**:
  - Operators can inspect the entire line in one unified tree.
  - Work orders on "Servo Motor #3" automatically flag the parent "Conveyor #2" and the parent "Bottling Line", alerting production managers to potential downtime.

### Scenario B: Hospital Central Sterilization & Medical Device Fleet
- **Setup**: An MRI Suite comprises the Main Magnet Assembly, Chiller Subsystem, Cryogenic Compressor, and RF Coil Attachments.
- **M03 Value**:
  - Technicians replacing an RF Coil can verify OEM part numbers against M09 Inventory in 1 click.
  - Swapping a defective coil automatically updates its asset status for calibration/repair while putting the new coil into active rotation.

### Scenario C: Renewable Energy / Wind Turbine Fleet
- **Setup**: Wind Turbine WTG-08 (Root) $\to$ Nacelle Sub-Assembly $\to$ High-Speed Gearbox $\to$ Bearing Lubrication Sensor.
- **M03 Value**:
  - The sensor failure triggers an alert in M05.
  - The field technician arriving at the remote site reads the exact breadcrumb lineage on mobile, grabs the exact replacement bearing from site inventory, and executes the replacement directly in the system.

---

## 6. Summary of Architectural & Operational Benefits

1. **Elimination of Data Fragmentation**: Assets are no longer disconnected rows in a database; they reflect their real physical assembly structure.
2. **Reduced Mean Time to Repair (MTTR)**: Field technicians instantly know where a component sits within a complex machine and what replacement part is needed.
3. **Rock-Solid Data Integrity**: Mathematical graph invariants prevent invalid topologies, circular dependencies, or cross-tenant data leaks.
4. **Unified Operations**: Seamless integration with Work Orders, Incidents, Maintenance, and Inventory delivers a cohesive, enterprise-grade ERP experience.

---
*Reference Standards: ISO 55000 (Asset Management), ISO 14224 (Reliability and Maintenance Data for Equipment).*
