# Domain model (Phase 0)

```
User (identity; is_platform_admin)
Organization (tenant; status ACTIVE|SUSPENDED)
  |- Membership (user x org; INVITED|ACTIVE|SUSPENDED)  -- MembershipRole --> Role
  |- Role (per org; is_owner, system_key) -- RolePermission --> Permission (global catalog)
  |- Notification (recipient, level, link, read_at)
  |- Attachment (generic target, sha256, read_permission)
  `- AuditLog (append-only; organization nullable for platform events)
```
- Tenant-owned (`TenantOwnedModel`): Membership, Role, Notification, Attachment. Indirect (via parent): RolePermission, MembershipRole. Global: Permission, User, Organization. AuditLog: nullable org, own manager.
- Constraints: unique lower(email); unique lower(org name) and slug; unique (user, org); unique role name per org (case-insensitive); one system role per key per org; unique (role, permission) and (membership, role).
- Invariants (services): an org always keeps >= 1 Owner; only Owners grant/revoke Owner; roles must belong to the same org; users cannot change their own access.

Future entities from HPE section 8.2 are added by their phase (Site, Zone, Asset, ... see `TRACEABILITY.md`); each extends `TenantOwnedModel`.

# Phase 1 additions (M01, M02, M03)
```
Organization
  `- Site (code unique/org, status ACTIVE|INACTIVE, timezone, contact info)
       |- Zone (BUILDING | ZONE | SERVICE_AREA; parent -> tree inside the site, max depth 6)
       |- OperatingCalendar (working days 1-7, hours or 24x7, one default per site) -- CalendarHoliday
       |- SiteContact (escalation_order unique per site)
       `- Asset (asset_tag unique/org; (manufacturer, serial) unique/org; status; zone optional; owner = Membership)
            |- AssetCategory (per org)            |- AssetStatusHistory (append-only)
            |- AssetLocationHistory (append-only) |- AssetDocument -> files.Attachment
            |- AssetMeter -- AssetMeterReading (monotonic)
            `- AssetComponent (parent -> child OneToOne; ASSEMBLY|COMPONENT|REPLACEABLE_PART)
rbac.MembershipRole.site (nullable): organization-wide when NULL, else limited to that site
```
- All new tables extend `TenantOwnedModel` (organization FK, fail-closed manager). Referenced objects are checked for the same organization in services (zone must belong to the asset's site; hierarchy edges must share organization and site).
- Constraints: lower(site code) per org; zone name per parent and code per site; one default calendar per site; holiday per (calendar, date); contact order per site; lower(asset_tag) per org; commission_date >= purchase_date; meter reading >= 0; component parent != child and quantity >= 1.
- Status values: ACTIVE, UNDER_MAINTENANCE, OUT_OF_SERVICE, RETIRED, DISPOSED (see D-027).

## Phase 2 additions (M05 / M06)
```
Asset ---< ServiceRequest (number INC-/SR-; kind; severity; service_impact; status; reported_by = Membership; site = asset.site)
              |- Downtime (one per request; started_at, ended_at)
              |- ServiceRequestHistory (append-only)
              |- Attachment (evidence)
              `---< WorkOrder (source_request nullable; at most one LIVE per request)
Asset ---< WorkOrder (number WO-; work_type; priority; status; assigned_to = Membership; planned window)
              |- WorkOrderEvent (append-only: transitions, re-assignments)
              |- WorkOrderLabor (technician, date, hours 0<h<=24)   |- WorkOrderMaterial (free text; stock is M09)
              `- Attachment (evidence)
core.DocumentSequence (organization, key, last_value): number allocation
```
- Constraints: unique number per organization; `uniq_live_work_order_per_request` (partial, excludes CANCELLED/CLOSED); downtime end >= start; plan window ordered; labor hours in (0, 24]; material quantity > 0.

## Phase 3 additions (M08 / M07)
```
ChecklistTemplate (key = family, version, status DRAFT/ACTIVE/INACTIVE, work_type blank=any, is_required)
   `---< ChecklistItem (position, prompt, item_type TEXT/NUMERIC/BOOLEAN/SELECTION, required, options, exception_options, min/max/unit, evidence_required)
   `---< Inspection (template version, work_order nullable, asset, site, status IN_PROGRESS/COMPLETED, started_by / completed_by = Membership)
              |---< InspectionResponse (item, value_text | value_number | value_bool, is_exception) - one per item
              |       `- Attachment (evidence)
              `---< Finding (item?, work_order?, asset, site, severity, status OPEN/RESOLVED, resolution)  `- Attachment (evidence)
WorkOrder ---< WorkNote (M07, append-only technician note)
```
- Constraints: one ACTIVE version per checklist key; unique (key, version); unique item position per template (deferred); min <= max; one inspection per (work order, template version); completed inspection has `completed_at`; one response per (inspection, item); note body not empty.

## Phase 4 additions (M09)
```
Site ---< Warehouse (code unique/org, is_active)
Part (part_number unique/org, unit, min/max/reorder defaults, is_active)
Warehouse x Part ---< StockBalance (on_hand, reserved, optional min/max/reorder override)
                         `---< StockMovement (append-only ledger: type, quantity, on_hand/reserved delta + after, work_order?, part_line?, transfer_ref?, actor)
WorkOrder ---< WorkOrderPart (part requirement: requested / issued / consumed / returned, status, warehouse) -- 1:1 -- PartReservation (held quantity)
WorkOrderPart ---< WorkOrderMaterial (M06 consumption rows written by M09 consume; NULL = free-text line)
```
- Constraints: on_hand >= 0, reserved >= 0, reserved <= on_hand; movement quantity > 0, a movement changes on-hand or reserved, after-values >= 0; one line per (work order, part); consumed <= issued - returned; reservation quantity >= 0, ACTIVE implies > 0.
- Invariants (services): balance = sum of its movement deltas; reserved = sum of ACTIVE reservations; every balance change writes exactly one movement; warehouse site = work order site.

## Phase 5 additions (M04)
```
Asset ---< MaintenancePlan (site = asset site, priority, checklist_key -> M08 template family, is_active)
              `---< MaintenanceSchedule (TIME: frequency x interval from start_date | METER: meter + interval_value + start_value;
                       lead_days, window start/hours, reminder_days, is_active, next_sequence, next_due_date|value, last_run_at, last_error)
                      `---< MaintenanceCycle (sequence UNIQUE per schedule; due date|value; skipped; trigger) -- 1:1 --> WorkOrder (source_type PREVENTIVE_MAINTENANCE, source_id = cycle)
WorkOrder.source_type / source_id: SERVICE_REQUEST (id = request) | PREVENTIVE_MAINTENANCE (id = cycle) | blank (created in M06)
```
- Constraints: plan name unique per asset; schedule fields consistent with its trigger; window ranges; one time schedule per (plan, frequency, interval) and one meter schedule per (plan, meter, interval); `(schedule, sequence)` unique; work order source type + id together, one work order per PM source.
- Invariants (services): every cycle has exactly one work order (created in the same transaction); the PM state of a cycle is derived from the work order; disabled / terminal-asset / suspended-tenant schedules never generate.

## Phase 6 (M11 SLA & Escalation) - HPE section 7.2
- `SLAProfile` (organization / site scope, `applies_to` REQUEST or WORK_ORDER, optional work type, `pause_states`) -> `SLATarget` (one per priority: response / resolution minutes, warning %) and `EscalationRule` (target, trigger WARNING / BREACH / ESCALATION, `after_minutes`, level 1-3, role and / or assignee; max 6).
- `SLATracking`: exactly one of request / work order (unique each); persisted absolute `response_due_at` / `resolution_due_at`, per-target state, status ACTIVE / PAUSED / COMPLETED / CANCELLED, paused time.
- `SLAEvent`: append-only, unique `(tracking, dedupe_key)`. `SLABreach`: unique `(tracking, target_kind)`, status OPEN / ACKNOWLEDGED / CLOSED, escalation level.
- Invariants (services): due times never rewritten by profile edits; one active profile per scope; a work order raised from a request has no tracking of its own; notifications only after the event row is persisted; M11 never changes M05 / M06 state.

## Phases 7-9 (M10, M12, M13, M14, M15)
- M10 `contracts`: `ServiceProvider` (org-wide, unique name); `CoverageAgreement` (WARRANTY / AMC / SERVICE_CONTRACT, one site, start/end dates with DB check, terms, exclusion notes, renewal alert days, `renewed_from` one-to-one, `is_active`); `CoveredAsset` (unique agreement+asset, asset at the agreement's site); `CoverageExclusion` (unique agreement+work type); `CoverageCheck` (append-only decision per work order: asset, agreement, work type, reference date, eligible, reason). Invariants: same-kind active agreements of one asset never overlap in time; eligibility = active AND date in range AND asset linked AND work type not excluded; agreements are deactivated, never deleted.
- M12 `identification`: `AssetIdentifier` (asset, kind QR / BARCODE, opaque globally-unique `token`, `is_active`, revocation fields; partial unique index: one active per asset+kind; check: active iff not revoked); `ScanEvent` (identifier, asset, scanner, optional service request; never deleted).
- M13 `portal`: `PortalAccount` (one per membership, client-only members, enabled flag, company); `PortalAssetGrant` (unique account+asset). A client's request is an M05 `ServiceRequest` (`reported_by` = membership); there is no portal-specific request table.
- M14 `dashboards`: no tables; aggregates over WorkOrder, WorkOrderLabor, Downtime, Asset, MaintenanceCycle, ServiceRequest, SLATracking / SLABreach, StockMovement / StockBalance, CoverageAgreement.
- M15 `audit`: `AuditLog.site_id` (nullable UUID, derived from the audited record); permissions `audit.view` (site-scoped) and `audit.export`.
