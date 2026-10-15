# FieldOps Nexus — Non-Technical Operational Dashboards & Analytics Guide (M14)

**Target Audience:** Plant General Managers, Operations Supervisors, Asset Managers, Maintenance Planners  
**Document Type:** Practical End-to-End User Guide  
**Module Reference:** M14 Operational Dashboards & Real-Time Analytics Hub  

---

## 1. The Big Picture: What is M14 in Real Life?

Think of driving an automobile or flying an aircraft. While driving, you don't open the hood to inspect the fuel injectors or oil levels manually. Instead, you look at your **dashboard**—the speedometer, fuel gauge, engine temperature light, and tire pressure warning.

In a running factory or facility management operation, hundreds of physical actions happen every day:
- Field technicians turn wrenches, pause timers, and install spare parts.
- Machines run, accumulate operating hours, or experience sudden breakdowns.
- Warehouses issue replacement filters and bearings.
- Customers call in emergency service requests.

A company director or plant manager cannot read through hundreds of separate service tickets to know if the facility is running smoothly.

**Module M14 (Operational Dashboards & Analytics)** is the **Mission Control Center**. It automatically gathers every ground action and turns it into simple, real-time visual gauges, color-coded health meters, and trend charts. In just a few seconds, it answers:
1. *"Are our machines running or broken down?"*
2. *"Are our technicians working at capacity or waiting for assignments?"*
3. *"Are we performing routine maintenance on time?"*
4. *"Which machine is our biggest chronic problem right now?"*
5. *"Are we running dangerously low on critical spare parts?"*

---

## 2. Quick Access & Credentials

Use these credentials to view the executive and management dashboards:

- **System URL (Local):** `http://127.0.0.1:8000/accounts/login/`
- **System URL (Cloud):** `https://fieldops-vpd.onrender.com/accounts/login/`
- **Universal Password:** `Password123!`
- **Executive Mission Control Hub:** `/app/dashboards/`
- **Technician Personal Cockpit:** `/app/dashboards/my-work/`

| Role | Login Email | What They Monitor on M14 |
| :--- | :--- | :--- |
| **Organization Owner / Admin** | `admin@apexindustrial.com` | Entire company performance, overdue job alerts, SLA violations, crew utilization. |
| **Plant Operations Supervisor** | `supervisor@apexindustrial.com` | Today's active job pipeline, emergency breakdowns, technician workload. |
| **Senior Asset Manager** | `asset.manager@apexindustrial.com` | Machine reliability (MTBF), repair speed (MTTR), top failing equipment. |
| **Maintenance Planner** | `planner@apexindustrial.com` | Preventive maintenance compliance %, stock room safety inventory levels. |
| **Lead Field Technician** | `lead.technician@apexindustrial.com` | Personal hours logged today, assigned jobs in progress. |

---

## 3. The 5 Mission Control Screens Explained in Plain English

When a manager navigates to `/app/dashboards/`, they can click through five clear tabs across the top:

```
┌────────────────────────────────────────────────────────────────────────┐
│ [Operations]  [Assets & Health]  [Maintenance]  [Service]  [Inventory]│
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
       Simple visual cards, color-coded health bars, and live dials
```

---

### Tab 1: Operations Overview (The Pulse of Today's Work)

This screen gives supervisors a real-time snapshot of ongoing field activities:

* **Open Work Orders:** Total active jobs currently in the system.
* **Overdue Orders (Red Alert):** Any jobs that were scheduled to be completed by now but are running late.
* **Technician Utilization (%):** Shows how much of your team's standard 8-hour workday is spent turning wrenches vs. sitting idle (e.g., *85% utilization across the crew*).
* **Work Order Priority Mix:** A clear visual bar showing how much work is **Emergency (Red)** vs **High Priority (Orange)** vs **Normal Scheduled (Blue)**.

---

### Tab 2: Assets & Reliability (Which Machines Give Us Trouble?)

This screen shows how dependable your physical equipment is over time:

* **Total Assets vs. Active Assets:** Out of your total machinery fleet, how many are operating right now vs. stopped for repairs?
* **Downtime Hours:** The total number of hours equipment sat idle and broken this month.
* **The Two Golden Reliability Numbers (MTBF & MTTR):**
  - **MTBF (Mean Time Between Failures):** *"How long does a machine run before it breaks down again?"*  
    *(Higher is better! It means your equipment is stable and reliable).*
  - **MTTR (Mean Time To Repair):** *"When a machine breaks down, how many hours does it take our crew to fix it and get it running again?"*  
    *(Lower is better! It means your team responds quickly and fixes problems efficiently).*
* **Top Failing Equipment:** A ranked list showing the *"troublemakers"* in your plant (e.g., *Chiller Pump 02 broke down 4 times this month*). This highlights exactly when an old machine should be replaced rather than repaired again.

---

### Tab 3: Maintenance Compliance (Are We Changing the Oil on Time?)

Just like changing your car's oil every 5,000 miles to prevent engine seizure, industrial machinery requires scheduled preventive maintenance (PM) to avoid expensive blowouts.

* **PM Compliance Rate (%):** Did our team finish our scheduled preventive maintenance on time? *(Target is typically 95%+)*.
* **Scheduled vs. On-Time vs. Missed:** Shows exactly how many PM inspections were done on schedule vs. how many were missed or delayed.
* **Preventive vs. Corrective Mix:** A visual bar that reveals whether your maintenance culture is **proactive** (doing planned servicing) or **firefighting** (running around repairing equipment after it blows up).

---

### Tab 4: Service & Breakdowns (Customer & Emergency Response)

When a machine fails unexpectedly and an operator files an emergency call:

* **Open Service Requests:** How many unresolved breakdown tickets are currently waiting for a supervisor to triage.
* **New Requests This Week:** Tracks whether breakdown incidents are increasing or decreasing over time.
* **SLA Breaches (Service Level Agreement):** If your service contract promises *"We will respond to chiller outages within 2 hours,"* this counter turns **Red** whenever a promise is broken.

---

### Tab 5: Inventory & Spare Parts (Do We Have the Parts We Need?)

Technicians cannot repair machines if the parts room is empty:

* **Total Managed Parts:** Total catalog lines stored in your central and mobile warehouses (filters, belts, gaskets, motors).
* **Low Stock Alerts (Warning):** Instantly highlights any items that have dropped below safety levels (e.g., *"Only 1 HEPA filter left! Reorder immediately"*).
* **Net Parts Consumed:** The total number of spare parts pulled from shelves and installed into machines this month.

---

## 4. The Technician's Personal Cockpit ("My Work")

Field engineers don't need to see factory-wide financial budgets or plant-wide totals. They have their own dedicated view at `/app/dashboards/my-work/`:

```
┌────────────────────────────────────────────────────────┐
│  My Assigned Jobs: 4      |  Currently In Progress: 1  │
│  Logged Labor Today: 6.5h |  Shift Utilization: 81%   │
└────────────────────────────────────────────────────────┘
```

1. **My Open Jobs:** Just the jobs assigned to me today.
2. **Currently In Progress:** The specific machine I am working on right now.
3. **Hours Logged Today:** Total wrench time and travel hours recorded today against an 8-hour shift.
4. **No Guesswork:** Technicians always know their progress without having to ask their supervisor.

---

## 5. Summary Checklist: What to Monitor Daily & Monthly

| When? | Who? | What to Check on M14 | Why? |
| :--- | :--- | :--- | :--- |
| **Every Morning (08:00 AM)** | Plant Supervisor | **Operations Overview** &rarr; *Overdue Orders* | Catch delayed jobs early and reallocate technicians. |
| **During Shift** | Supervisor / Planner | **Service & Breakdowns** &rarr; *SLA Breaches* | Make sure customer breakdown calls don't exceed time promises. |
| **End of Day** | Individual Technician | **My Work** &rarr; *Logged Labor Hours* | Verify that all wrench and driving hours were recorded. |
| **Weekly Meeting** | Maintenance Planner | **Maintenance** &rarr; *PM Compliance %* | Keep preventive maintenance on schedule to avoid breakdowns. |
| **Weekly Inventory Run** | Stockroom Manager | **Inventory** &rarr; *Low Stock Alerts* | Reorder parts before technicians run out in the middle of a repair. |
| **Monthly Management Review** | General Manager | **Assets & Health** &rarr; *MTTR, MTBF, Top Failing* | Decide which old machines need capital replacement. |

---

## 6. Business Impact: Traditional Spreadsheets vs. M14

| Operational Area | Traditional Excel & Paper Process | With FieldOps Nexus M14 |
| :--- | :--- | :--- |
| **Monthly Reporting** | Managers spend 3 to 5 days pulling data from paper job sheets. | **Zero Seconds:** Real-time dashboards update the moment a job is completed. |
| **Equipment Reliability** | Nobody knows which machine breaks down the most until a catastrophic failure. | **Ranked Transparency:** Top failing machines are highlighted automatically with exact downtime hours. |
| **Stock Shortages** | Technicians walk into the storeroom only to find a required filter is out of stock. | **Early Warnings:** Automatic low-stock flags prevent repair delays. |
| **SLA Penalties** | Late jobs are discovered only after angry customer phone calls or financial penalties. | **Live Red Alerts:** Supervisors see approaching deadlines before they breach. |
