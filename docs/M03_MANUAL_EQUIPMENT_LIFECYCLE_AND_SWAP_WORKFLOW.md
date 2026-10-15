# FieldOps Nexus — Non-Technical Equipment Lifecycle & Part Swap Guide

**Target Audience:** Plant Operators, Maintenance Supervisors, Facility Technicians, Asset Coordinators  
**Document Type:** Practical End-to-End User Guide  
**Module Reference:** M03 Equipment Registry & Component Hierarchy  

---

## 1. The Big Picture: How Equipment Works in Real Life

Think of your plant or building like a fleet of commercial vehicles. You have big primary machines (such as a **Central Chiller Unit**, an **Air Handling Unit (AHU)**, or a **High-Pressure Booster Pump**). 

Each big machine contains smaller working parts inside it:
- **Major Sub-Assemblies:** Motors, Compressors, Fan Housings.
- **Wearable / Replaceable Parts:** Bearings, Oil Filters, Drive Belts, Gaskets, Valves.

FieldOps Nexus gives every piece of equipment—from the 10-ton chiller down to a 2-inch oil filter—its own digital identity so you always know where it is, what machine it is running inside, and when it needs service.

---

## 2. Quick Access & Demo Credentials

Use these accounts to test and operate the system:

- **System URL (Local):** `http://127.0.0.1:8000/accounts/login/`
- **System URL (Cloud):** `https://fieldops-vpd.onrender.com/accounts/login/`
- **Universal Password (All Accounts):** `Password123!`

| Role | Login Email | What You Use It For |
| :--- | :--- | :--- |
| **Organization Owner / Admin** | `admin@apexindustrial.com` | Full system access, all dashboards, reports, and audit logs. |
| **Senior Asset Manager** | `asset.manager@apexindustrial.com` | Registering equipment, managing hierarchy trees, warranties. |
| **Operations Supervisor** | `supervisor@apexindustrial.com` | Triaging incident breakdown calls, scheduling work orders. |
| **Maintenance Planner** | `planner@apexindustrial.com` | Planning preventive maintenance, checking spare parts stock. |
| **Lead Field Technician** | `lead.technician@apexindustrial.com` | Mobile job cockpit, executing repairs, scanning QR codes. |

---

## 3. Step 1: Equipment Registration (The "Birth")

When a new machine or batch of spare parts arrives at your facility loading dock:

1. **Add the Equipment:**
   - In the navigation menu, go to **Assets & Locations** &rarr; **Assets** &rarr; Click **`+ New Asset`**.
   - Fill in the basic identity:
     - **Asset Name:** e.g., *"Centrifugal Water Chiller 01"*
     - **Asset Tag:** A unique code, e.g., `AST-CHL-001`.
     - **Category:** e.g., *HVAC Equipment* or *Pumps*.
     - **Physical Location:** Select the building and plant room (e.g., *Colombo HQ - Plant Room B*).
     - **Manufacturer & Serial:** e.g., *Carrier / Trane*, Serial `SN-2026-CHL-8910`.
2. **Affix the Physical Label:**
   - Print the asset label with its QR code / barcode.
   - Stick the weather-resistant label onto the machine frame.
3. **Commission the Machine:**
   - The initial status is set to **`IN_SERVICE`** (Active).

---

## 4. Step 2: Building the Equipment Family Tree

A large machine is made up of smaller parts. In the system, you link them together so they look like a family tree:

```
[AST-CHL-001] Centrifugal Water Chiller (Root Machine)
  ├── [COMP-01] Compressor Assembly (Sub-Assembly)
  │     ├── [FLTR-01] Oil Filter Cartridge (Replaceable Part)
  │     └── [BRG-01] Magnetic Bearing Set (Replaceable Part)
  └── [PUMP-01] Coolant Circulation Pump (Sub-Assembly)
```

### How to assemble it in the screen:
1. Open the root machine (`AST-CHL-001`) and click the **Hierarchy** tab.
2. In the right-hand panel, find **Attach Child Component**:
   - **Child Asset:** Select the compressor or motor from the list.
   - **Relationship Type:** Choose `Sub-Assembly`, `Component`, or `Replaceable Part`.
   - **Quantity:** e.g., `1` (or `4` for a set of filters/bearings).
   - **Part Number (Optional):** Enter the catalog spare part SKU (e.g., `SKF-MB-6000`).
3. Click **Add to hierarchy**.
4. The tree view updates immediately to show the part securely attached inside the machine.

---

## 5. Step 3: Day-to-Day Maintenance & "Replace Part / Swap"

This is the most common real-world maintenance task: **A part wears out, and the technician replaces it with a fresh spare from the stock room.**

### What Happens in the Real World vs. In the System

```
[OLD WORN PART] ─────── Click "Replace Part / Swap" ───────> [NEW SPARE PART]
      │                                                             │
      ▼                                                             ▼
Uninstalled from machine                                      Installed into machine
Becomes standalone asset                                      Inherits position & role
(Sent for scrap, cleaning, or rebuild)                       (Now active in hierarchy)
```

### Step-by-Step User Instructions:

1. **Find the Worn Part:**
   - Open the **Hierarchy Hub** (`/app/assets/hierarchy/`) or open the machine's detail page.
   - Locate the worn part in the tree (e.g., `Child Component 2` or `Oil Filter Cartridge`).
2. **Open the Action Menu:**
   - Click the three small dots (`•••`) on the right side of that part's card.
   - From the dropdown menu, click **`Replace Part / Swap`**.
3. **Choose the Replacement:**
   - The system opens the swap interface for that component.
   - **Replacement Asset:** Pick the fresh spare part that is currently sitting on your storage shelf.  
     *(Note: The system automatically protects you by only showing spare parts that belong to the same physical building and are not already installed inside another machine).*
   - **Replacement Reason:** Enter a clear reason (e.g., *"Routine PM: Clogged filter replaced with fresh OEM unit"*).
4. **Submit the Swap:**
   - Click **Save / Submit**.
5. **What the System Does Automatically in One Second:**
   - **The Old Part is Unplugged:** The worn component is detached from the machine. It is now marked as an unattached standalone asset so it can be cleaned, repaired, or disposed of.
   - **The New Part Takes Its Place:** The fresh spare is installed in the exact same spot in the machine tree, keeping the same quantity and role.
   - **Audit Record Created:** An indelible log entry is recorded:  
     `"Kasun Perera replaced AST-FLTR-OLD with AST-FLTR-NEW. Reason: Routine PM Clogged Filter."`

---

## 6. Step 4: Maintenance Incidents, Work Orders & Scanning

1. **Spotting a Problem (Incidents):**
   - If an operator notices a leak or abnormal noise, they go to **Incidents & Requests** &rarr; **`Report Incident`**.
   - They choose the exact component that is acting up.
   - If someone else already reported a problem on that machine today, the system flags a **Possible Duplicate Warning** to avoid double work.
2. **Dispatching a Work Order:**
   - The supervisor reviews the incident and clicks **`Create Work Order`**.
   - The work order automatically inherits the full machine location path:  
     **`Chiller 01 › Compressor › Magnetic Bearing`**
   - The supervisor assigns the job to a field technician.
3. **On-Site Mobile Scanning:**
   - The technician walks up to the physical machine on the factory floor.
   - They open the FieldOps mobile camera scanner (`/app/scan/`) and scan the QR code sticker on the machine.
   - The system immediately opens their assigned job card, checklist, and safety instructions with zero manual searching.

---

## 7. Step 5: Equipment Retirement & Archiving (The "End")

When a machine or component reaches the end of its design life (e.g., after 20 years or catastrophic failure):

1. **Take it out of Service:**
   - In the asset detail screen, the status is moved from `IN_SERVICE` &rarr; `IN_MAINTENANCE` &rarr; `RETIRED` or `DISPOSED`.
   - The user must enter a decommissioning reason code.
2. **Salvaging Usable Parts:**
   - Before disposing of the main chassis, any good reusable components inside it can be unlinked using **`Detach from Parent`** or moved to another machine using **`Move to new parent`**.
3. **Permanent Record Lock:**
   - Once marked `RETIRED` or `DISPOSED`, the machine cannot be scheduled for new jobs or incidents.
   - **Nothing is ever deleted:** Every inspection checklist, work order, technician labor hour, and spare part replacement from the machine's entire lifetime remains permanently archived and searchable for safety audits and historical compliance.

---

## 8. Summary Checklist for Operators

| Action | Where to Go | Button / Action |
| :--- | :--- | :--- |
| **Register New Machine** | `/app/assets/new/` | `+ New Asset` |
| **View Full Equipment Tree** | `/app/assets/hierarchy/` | Select equipment on left sidebar |
| **Attach a Part to Machine** | Equipment Detail &rarr; `Hierarchy` tab | `Attach Child Component` |
| **Swap a Broken Part** | Equipment Tree &rarr; Click `•••` on part | `Replace Part / Swap` |
| **Move Part to New Machine** | Equipment Tree &rarr; Click `•••` on part | `Move / Re-parent` |
| **Detach Part Completely** | Equipment Tree &rarr; Click `•••` on part | `Detach from Parent` |
| **Scan Label with Phone** | `/app/scan/` | Point camera at asset QR code |
