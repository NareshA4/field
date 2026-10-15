# FieldOps Nexus — Non-Technical Technician Workspace Guide (M07)

**Target Audience:** Field Technicians, Mobile Service Crews, Plant Maintenance Engineers, Field Supervisors  
**Document Type:** Practical End-to-End User Guide  
**Module Reference:** M07 Technician Workspace & Execution Cockpit  

---

## 1. The Big Picture: What is M07 in Real Life?

Imagine an airline pilot stepping into the cockpit or an emergency doctor arriving at a trauma room. They don't want to browse through a 50-page desktop software system with dozens of administrative menus. 

When a technician arrives at a factory with a heavy toolbox in hand, they need an **ergonomic, one-tap mobile cockpit** right on their phone or tablet that immediately answers:
1. *"Which building and room do I need to go to?"*
2. *"Which machine is broken, and what is its history?"*
3. *"Start the clock while I turn wrenches."*
4. *"Let me check off safety steps, log parts I used, and take proof photos."*
5. *"Mark the job finished and move to my next task."*

That is **Module M07: The Technician Workspace**.

---

## 2. Quick Access & Credentials

Use these credentials to log in and test the technician experience:

- **System URL (Local):** `http://127.0.0.1:8000/accounts/login/`
- **System URL (Cloud):** `https://fieldops-vpd.onrender.com/accounts/login/`
- **Technician Login Email:** `lead.technician@apexindustrial.com`
- **Universal Password:** `Password123!`
- **Direct Workspace URL:** `/app/workspace/`
- **Personal Daily Work Cockpit:** `/app/dashboards/my-work/`

---

## 3. The End-to-End Technician Journey

```
[1. View "My Jobs"] ───> [2. Log Travel] ───> [3. Tap "Start Job"]
         │                         │                        │
         ▼                         ▼                        ▼
Check today's priorities    Log transit time         Clock ticks (Wrench time)
                                                            │
                                                            ▼
                                                [4. Use Slide-in Drawer]
                                                • Safety checklist (Mandatory)
                                                • Spare parts consumed
                                                • Photos & diagnostic notes
                                                            │
                                                            ▼
                                                [5. Tap "Complete Job"]
                                                • Safety verified
                                                • Machine back in service
                                                • Move to next job!
```

---

### Step 1: Morning Shift & Checking "My Jobs"

When the technician starts their shift and opens `/app/workspace/` on their mobile device:
1. **Clean Color-Coded Categories:**
   - **Active (Green):** What is currently being worked on right now.
   - **Upcoming (Blue):** Scheduled jobs lined up for today, organized by urgency.
   - **Completed (Grey):** Work already finished today or earlier this week.
2. **Clear Job Cards:**
   Every card displays essential information at a glance:
   - **Job Code:** e.g., `WO-000003`
   - **Priority:** *Red for Emergency/Critical*, *Orange for High*, *Blue for Normal*.
   - **Target Machine & Location:** e.g., *Plant Room 2 › Booster Pump 02*.
   - **Scheduled Window:** When the customer expects the repair completed.

---

### Step 2: Driving to the Customer Site (Logging Travel Time)

Before turning a single screw, technicians often have to drive across town or navigate between industrial complexes:
1. On their assigned job card, the technician taps **Log Travel**.
2. They enter:
   - **Duration:** e.g., `45 minutes` (or `0.75 hours`).
   - **Distance:** e.g., `25 km`.
   - **Route Notes (Optional):** e.g., *"Transit from central depot via highway."*
3. **What the system does automatically:**  
   The travel time is automatically added to the job's official labor records. Technicians don't need to save paper fuel receipts or fill out manual travel logs at the end of the week.

---

### Step 3: Arriving on Site & Starting the Clock ("Wrench Time")

Once standing in front of the machinery:
1. **Verify the Location:** The screen displays the exact location breadcrumbs:  
   **`Colombo HQ › Plant Building 4 › Sub-Level 1 › Pump Room 2`**.
2. **Scan the Asset QR Code:**  
   The technician can point their mobile camera at the machine’s QR sticker to instantly confirm they are working on the correct equipment and not an adjacent unit.
3. **Tap "Start Job":**
   - The technician clicks the green **Start Job** button.
   - The job status turns to **`IN_PROGRESS`**.
   - A live digital stopwatch starts running on screen. 
   - Back in the main office, the supervisor's schedule board lights up in real time showing the job is officially underway.

---

### Step 4: The Slide-In "Execution Drawer" (During the Repair)

While working, the technician taps **Quick Drawer** on their screen. A sleek toolbox drawer slides out from the right side of the screen without losing their place.

Here they handle all in-progress details in one touch:

#### A. The Mandatory Safety Checklist (Lockout / Tagout)
Industrial safety is strictly enforced by the system:
- The technician checks off safety verification items:
  - ☑ *Main electrical power isolated and padlocked*
  - ☑ *Hydraulic/pneumatic pressure released*
  - ☑ *Safety glasses, gloves, and steel-toe boots verified*
- **Safety Rule:** If any mandatory safety check is left blank, the system will **physically block** the technician from closing the job later. Safety cannot be skipped!

#### B. Consuming Allocated Inventory Parts & Logging Materials

FieldOps Nexus provides a seamless, dual-track parts tracking system right inside the technician cockpit:

##### 1. Allocated Warehouse Parts (Pre-Issued & Tracked Inventory)
When the maintenance planner or storekeeper has already reserved and issued specific spare parts from a warehouse (e.g., *Mechanical Pump Seal 20mm* or *Heavy Duty Ball Bearings*):
1. **Open the "Allocated Inventory Parts" Card:**
   - On the job screen (`/app/workspace/jobs/<id>/`), scroll to the **Allocated Inventory Parts** section.
   - The green badge clearly shows how many reserved items are available to consume (e.g., `"1 available to consume"`).
2. **Select the Part:**
   - Click the dropdown to see all reserved parts, their codes, and remaining balances:  
     `PUMP-SEAL-20 — Mechanical Pump Seal 20mm (Outstanding: 5.000 PIECES)`.
3. **Verify Warehouse & Available Quantity:**
   - The live card instantly displays:
     - **Warehouse:** e.g., *DSC Site Warehouse*
     - **Available Outstanding:** e.g., *5.000 PIECES*
4. **Enter Quantity or Tap "Use All":**
   - If you used all issued parts, tap the **`[ Use All ]`** button to auto-fill the field in one tap.
   - If you only used a partial amount, type the exact number (e.g., `2.000`).
5. **Confirm Usage:**
   - Tap the primary blue button **`Confirm Quantity Used`**.
   - **What happens automatically:**
     - The used count immediately increases in the **Allocated Parts Ledger**.
     - The remaining balance drops in real time.
     - When all units are consumed, the badge turns green (`CONSUMED`) and the system displays a clear confirmation notice:  
       *✔ All warehouse parts allocated for this job have been consumed or returned.*
     - An official material usage entry is written for accounting without requiring manual re-entry.

##### 2. Quick-Consume via the Slide-In Execution Drawer
Technicians on mobile phones don't need to scroll up and down the full page:
1. Tap **`Execution Drawer`** on the top action bar.
2. In the slide-out toolbox panel, find the **Allocated Parts** card.
3. Select the part, enter the quantity, and tap **`Consume Part`**.
4. The drawer updates dynamically on the fly via HTMX—no page refresh, no lost timers, zero disruption.

##### 3. Ad-Hoc Van Stock & Consumables
If unexpected minor consumables were used from the service van (e.g., electrical tape, grease, zip ties):
- Use the **Parts Used & Materials** section below to record the description, quantity, and unit.

---

#### C. Taking a Break or Waiting for Parts? (Pause & Resume)
If the technician needs to step away for lunch or wait for an overhead crane:
- They tap **Pause Job** and enter a quick reason (e.g., *"Waiting for crane operator"*).
- The active labor clock stops ticking so non-working time isn't billed to the customer. When ready, they tap **Resume Job**.

#### D. Photo Evidence & Work Notes
- **Technician Notes:** Type a quick discovery note (e.g., *"Found severe cavitation on impeller stage 2"*).
- **Photo Evidence:** Snap a photo on their phone camera of the repaired pump or pressure gauge reading and upload it. The photo becomes a permanent, tamper-proof record attached to the work order.

---

### Step 5: Job Completion & Customer Digital Sign-off (M15)

Once on-site repair and testing are complete, field technicians tap **"Complete Job"** in the top action bar or execution drawer. This navigates to the dedicated, mobile-optimized **Job Completion & Customer Sign-off Screen** (`/app/workspace/jobs/<pk>/complete/`).

```
┌────────────────────────────────────────────────────────┐
│        JOB COMPLETION & CUSTOMER SIGN-OFF SCREEN       │
├────────────────────────────────────────────────────────┤
│ 1. Executive Work Summary                              │
│    [ Labor Logged ] [ Parts Consumed ] [ Checklist ]   │
│    - Review hours, consumed materials, & audit records │
│                                                        │
│ 2. Final Photo Evidence (Dropzone)                     │
│    - Drag-and-drop or tap phone camera to upload       │
│    - Instant live thumbnail preview with file size     │
│                                                        │
│ 3. Customer Digital Sign-off                           │
│    - Work Resolution Notes (min 10 characters)         │
│    - Customer / Representative Full Name & Title       │
│    - Customer Acceptance Remarks (Optional)           │
│    - High-DPI Digital Signature Canvas Pad             │
│      [ ✍️ Please sign in box ] -> [ ✓ Captured ]       │
│                                                        │
│ [ Sign & Confirm Job Completion ]                      │
└────────────────────────────────────────────────────────┘
```

#### A. Executive Work Summary & Completion Gates
The system automatically compiles all job metrics for customer review:
- **Recorded Labor Hours:** Total diagnostic and repair time logged.
- **Parts Consumed Ledger:** Itemized inventory parts and materials used.
- **Checklist Engine:** Real-time confirmation that all mandatory safety items are certified.
- **Evidence Files:** Count and thumbnails of on-site diagnostic photos.

> **System Safety Gate:** If mandatory checklists or labor hours are missing, an alert banner identifies the blocker with direct links to resolve it, and submission remains disabled until requirements are met.

#### B. Final Photo Evidence Upload
Technicians can upload a final verification photo of the clean work site and operational equipment:
- Tap the **Dropzone** to trigger mobile camera capture or select a photo.
- Instant client-side preview displays the thumbnail and size in KB before upload.
- One-click **Remove** button to re-take if needed.

#### C. Customer Digital Sign-off Pad
Hand the phone or tablet to the facility representative or customer:
1. **Resolution Notes:** Authoritative summary of the root cause, repairs performed, and tests completed.
2. **Customer Details:** Representative full name (e.g. *Eleanor Rodriguez*) and Title/Role (*Facility Operations Director*).
3. **Acceptance Remarks:** Optional client feedback (e.g. *"System tested under full load and running smoothly"*).
4. **Digital Signature Canvas:**
   - Draw directly with a finger, stylus pen, or mouse.
   - High-DPI auto-scaling ensures sharp, legally defensible signature rendering.
   - Real-time indicator toggles from `✍️ Please sign in box` to green `✓ Signature Captured`.
   - **Clear Signature** button allows easy re-signing if needed.

#### D. Confirmation & Verified Read-Only Certificate
Upon tapping **"Sign & Confirm Job Completion"**:
1. The canvas signature is cryptographically converted into a timestamped PNG attachment.
2. An immutable `WorkOrderSignoff` audit record is created with timestamp and signer credentials.
3. The work order atomically transitions to **`COMPLETED`** status.
4. The equipment in the plant hierarchy turns back to **Healthy / In Service**.
5. Re-visiting `/complete/` permanently locks the screen into a **Customer Sign-off Verified & Completed** summary, preventing any further edits.

---

## 4. Operator Cheat-Sheet: Quick Actions in M07

| To Do This... | On Screen, Click... | What Happens |
| :--- | :--- | :--- |
| **See My Daily Jobs** | Go to `/app/workspace/` | Shows Active, Upcoming, and Completed jobs. |
| **Record Driving Time** | Tap `Log Travel` on job card | Enters travel hours and distance into payroll/billing. |
| **Begin Work** | Tap `Start Job` | Starts live timer; marks job `IN_PROGRESS`. |
| **Open Toolbox Panel** | Tap `Execution Drawer` | Opens slide-in panel for safety, parts, and notes. |
| **Consume Allocated Parts** | Select Part & tap `Confirm Quantity Used` | Decrements warehouse stock, updates ledger, creates material line. |
| **One-Tap "Use All"** | Tap `Use All` in Parts card | Automatically populates the maximum available issued quantity. |
| **Quick-Consume in Drawer** | Enter Qty & tap `Consume Part` in Drawer | Updates allocated inventory stock via real-time HTMX swap. |
| **Pause Timer for Lunch** | Tap `Pause Job` in drawer | Halts clock until you tap `Resume Job`. |
| **Attach Photo Proof** | Tap `Upload Evidence` in drawer | Attaches phone camera photos directly to job. |
| **Finalize & Close Job** | Tap `Complete Job` | Opens completion screen for executive summary, photo proof, and customer digital sign-off. |

---

## 5. Why Technicians and Supervisors Love M07

| For the Ground Technician | For the Operations Supervisor |
| :--- | :--- |
| **Zero Paperwork:** No crumpled paper work orders, handwritten time logs, or lost receipts. | **Total Real-Time Visibility:** Knows exactly which technician is working on which pump right now. |
| **One-Tap Ease:** Start, Pause, Log Travel, and Complete with single taps on mobile. | **Guaranteed Compliance:** Safety checklists must be completed before jobs can be closed. |
| **Everything on Your Phone:** Equipment manuals, previous service records, and contact numbers in pocket. | **Accurate Billing & Payroll:** Travel hours and hands-on wrench time are tracked to the exact minute. |
