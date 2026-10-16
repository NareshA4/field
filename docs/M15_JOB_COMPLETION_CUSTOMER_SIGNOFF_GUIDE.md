# FieldOps Nexus — Job Completion & Customer Digital Sign-off Guide (M15)

## 1. Overview & Purpose
When a field technician completes on-site service, customer satisfaction and operational auditability require verified sign-off before closing the work order. The **Job Completion & Customer Digital Sign-off Screen** provides a unified, mobile-optimized experience where technicians review job metrics, attach final photo verification, capture customer feedback, and record a legally-binding customer signature directly on any touch-enabled or desktop device.

---

## 2. Key Capabilities & Workflow

```
[ Work Order: IN_PROGRESS ]
          │
          ▼
[ Technician: "Complete Job" Button ]
          │
          ▼
┌────────────────────────────────────────────────────────┐
│              JOB COMPLETION SCREEN                     │
│                                                        │
│  1. Executive Work Summary                             │
│     - Labor Hours Logged (M06)                         │
│     - Parts & Materials Consumed (M05)                 │
│     - Checklist Completion Status (M07)                │
│     - Photo Evidence Count (M08)                       │
│                                                        │
│  2. Final Photo Evidence Upload                        │
│     - Instant live preview                             │
│     - Client-side validation                           │
│                                                        │
│  3. Customer Digital Sign-off                          │
│     - Work Resolution Notes (min 10 chars)             │
│     - Signer Full Name & Title                         │
│     - Customer Acceptance Remarks                      │
│     - High-DPI HTML5 Digital Signature Canvas          │
└────────────────────────────────────────────────────────┘
          │
          ▼
[ POST /app/workspace/jobs/<pk>/complete/ ]
          │
          ├── Atomically records WorkOrderSignoff
          ├── Converts base64 canvas PNG into WorkOrderAttachment
          ├── Transitions WorkOrder status to COMPLETED
          └── Appends immutable audit log entry
          │
          ▼
[ Work Order: COMPLETED ]
(Screen locks into Read-Only Verified Sign-off Summary)
```

---

## 3. Completion Prerequisites (Business Gates)

Before a work order can be confirmed for completion, FieldOps Nexus validates four essential gates:
1. **Status Gate**: Work order must currently be in `IN_PROGRESS` status.
2. **Labor Gate**: At least one technician labor entry (`WorkOrderLabor`) must be recorded.
3. **Evidence Gate**: If work type is `CORRECTIVE`, at least one evidence file (or final completion photo) must be attached.
4. **Checklist Gate**: If checklist is required (`checklist_required=True`), all mandatory checklist items must be completed and signed off.

If any blocker exists, the completion screen highlights the missing item in a warning banner with direct links to resolve it, and the submit button remains disabled until requirements are satisfied.

---

## 4. Digital Signature Canvas Features
- **High DPI Crisp Rendering**: Automatically compensates for device pixel ratio (`window.devicePixelRatio`) to render razor-sharp lines on Retina and high-resolution displays.
- **Cross-Input Compatibility**: Handles stylus, finger touch, and mouse input seamlessly with zero lag.
- **Visual Feedback**: Real-time status badge indicates `✍️ Please sign in box` until drawn upon, switching to `✓ Signature Captured` with a green border.
- **Signature Reset**: 1-click **Clear Signature** button resets the pad for retries.
- **Client-Side Validation**: Form submission is blocked if the canvas is empty, scrolling the signature pad into view with an explicit alert.

---

## 5. Data Model (`WorkOrderSignoff`)

Located in `src/apps/workorders/models.py`:

```python
class WorkOrderSignoff(TenantModel):
    work_order = models.OneToOneField(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="signoff",
    )
    signer_name = models.CharField(max_length=150)
    signer_title = models.CharField(max_length=120, blank=True)
    customer_notes = models.TextField(blank=True)
    signed_at = models.DateTimeField(default=timezone.now)
    signed_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    signature_attachment = models.ForeignKey(
        "WorkOrderAttachment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
```

---

## 6. API Reference

### Complete Job API Endpoint
- **URL**: `POST /api/v1/workspace/jobs/<pk>/complete/`
- **Authentication**: Session / Token (Requires assigned technician or `workorders.complete_workorder` permission)
- **Request Body (JSON / Multipart)**:
  ```json
  {
    "resolution_notes": "Replaced faulty centrifugal pump seal. Pressure tested to 45 PSI with zero leaks.",
    "signer_name": "Eleanor Vance",
    "signer_title": "Facility Director",
    "customer_notes": "Confirmed operational.",
    "signature_data": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA...",
    "photo_evidence": null
  }
  ```
- **Response (200 OK)**:
  ```json
  {
    "success": true,
    "work_order_id": "55f095fb-fe9f-4b99-be70-605112f668a2",
    "order_number": "WO-000003",
    "status": "COMPLETED",
    "signer_name": "Eleanor Vance",
    "signed_at": "2026-10-15T15:52:00Z"
  }
  ```
