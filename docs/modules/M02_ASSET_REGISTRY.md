# M02 Asset Registry

FieldOps Nexus --- Asset Registry Module
Project: FieldOps Nexus
HPE Code: HPE-PRD-2026-FOPS02
Module: Asset Registry
Recommended Django App: apps.assets
1. Purpose
The Asset Registry maintains the authoritative master record for
physical assets in FieldOps Nexus, including identity, classification,
model/serial details, dates, location, ownership-related data, warranty
reference, lifecycle status, documents, meters, and status history.
2. Scope
In Scope
- Asset records and categories
- Asset documents
- Asset meters and readings where implemented
- Asset lifecycle status and status history
- Controlled status transitions
- Search/filtering
- REST APIs
- RBAC/object-scope enforcement
- Audit integration
Out of Scope
Owned by other modules: - Asset Hierarchy - Warranty/AMC terms and
claims - Preventive Maintenance schedules - Work Orders - Incidents -
Inventory - QR generation - Operational Dashboards
3. Core Entities
- Asset
- AssetCategory
- AssetDocument
- AssetMeter
- AssetMeterReading where implemented
- AssetStatusHistory
Asset data includes Asset ID, category, model, serial number,
purchase/commission dates, Site/Zone, owner, warranty reference, status,
documents, notes and audit-related fields where supported.
4. Lifecycle
Confirmed broad lifecycle:
ACTIVE → UNDER_MAINTENANCE → OUT_OF_SERVICE → ACTIVE / RETIRED / DISPOSED
Statuses: - ACTIVE - UNDER_MAINTENANCE - OUT_OF_SERVICE -
RETIRED - DISPOSED
The complete transition matrix remains subject to HPE/team-lead
confirmation unless already established. Status should be changed only
through the controlled transition service, not ordinary asset editing.
5. Backend Architecture
Recommended structure:
apps/assets/
├── models.py
├── forms.py
├── views.py
├── urls.py
├── services.py
├── transitions.py
├── selectors.py
├── filters.py
├── validators.py
├── permissions.py
├── api/
│   ├── serializers.py
│   ├── views.py
│   └── urls.py
└── tests/
Follow the repository's existing conventions where the actual layout
differs.
6. Service Layer
Backend operations should cover: - Create asset - Update asset -
Controlled status transition - Document operations - Meter operations -
Meter readings
Lifecycle transitions should preserve database, history and audit
consistency.
7. API Surface
Confirmed/specification API groups include: - GET /api/v1/assets/ -
POST /api/v1/assets/ - Asset detail/update -
GET /api/v1/assets/{id}/history/ - Meter endpoints
Recommended implementation may also expose controlled transition,
category, meter-reading, document, and later token/QR lookup endpoints.
Exact URLs must follow the repository router.
8. Permissions and Security
Use existing FieldOps Nexus authentication, RBAC, tenancy, Site/Location
and Audit modules.
Verify: - Authentication - Write/read scopes - Organization/tenant
isolation - Site/object scope - Controlled status changes - Secure
uploads - Append-only status history - Audit logging -
Cross-organization IDOR prevention
9. Validation
Cover applicable rules for: - Asset ID presence/uniqueness - Category -
Site/Zone relationship - Dates - Lifecycle transitions - Upload
type/size policy - Meter readings - Organization/object scope
Do not invent unresolved business rules.
10. Audit and History
Lifecycle transitions should record applicable: - From status - To
status - Actor - Timestamp - Reason
AssetStatusHistory should remain append-only.
11. Frontend Integration
Recommended frontend: Django Templates + HTMX.
Expected screens: 1. Asset Registry Home 2. Asset List 3. Asset Detail
4. Asset Create/Edit 5. Change Status 6. Document Upload 7. Meter
Management 8. Category Management 9. Status History 10. Optional Import,
subject to clarification
The Registry dashboard/home cards are a technical recommendation, not a
separately mandated PRD dashboard.
12. Dependencies
Required/existing: - Authentication/RBAC - Organization/Tenancy - Site &
Location - Audit
External/later integrations: - Asset Hierarchy - PM - Incident/Service
Request - Work Orders - Warranty/AMC - QR/Barcode - Client Portal -
Operational Dashboards
Do not implement teammate-owned modules inside Asset Registry.
13. Testing Requirements
Test: - Asset create/edit - Duplicate Asset ID - Site/Zone mismatch -
Document upload validation - Meter/readings - Valid/invalid
transitions - Direct status-update prevention - RBAC -
Cross-organization isolation - Append-only history - APIs - Audit -
Transaction rollback - Concurrency where supported - Work Order
integration boundary - Full regression suite
14. Acceptance Criteria
- Assets persist successfully.
- Required initial history is created according to current design.
- Invalid transitions are rejected.
- Successful transitions record appropriate history.
- Unauthorized operations are rejected.
- Invalid uploads are rejected.
- Status/change history is available.
- APIs are registered and reachable.
- Tenant/object isolation is enforced.
- Asset-specific tests pass.
- Regression testing shows Asset changes do not break teammate
  modules.
15. HPE / Team-Lead Clarifications
Do not silently invent: 1. Exact Asset ID format and uniqueness scope 2.
Meaning of owner 3. Complete lifecycle transition matrix/triggers 4.
Warranty storage/reference ownership 5. Exact create/edit/retire/read
roles 6. Retirement behavior with open WO/PM 7. Meter source rules 8.
Serial-number rules 9. Bulk import/export requirements 10. Final
document limits/types 11. Category hierarchy/custom fields
16. Completion Checklist
Database
- [ ] Models verified
- [ ] Migrations applied
- [ ] Constraints/indexes verified
- [ ] Append-only history verified
Backend
- [ ] Asset app enabled
- [ ] Services verified
- [ ] Transition service verified
- [ ] Permissions/object scope verified
- [ ] Upload validation verified
- [ ] DRF endpoints registered
- [ ] OpenAPI verified
- [ ] Audit integration verified
QA
- [ ] Unit tests pass
- [ ] API tests pass
- [ ] RBAC negative tests pass
- [ ] Tenant isolation passes
- [ ] Lifecycle tests pass
- [ ] Document/meter tests pass
- [ ] Regression suite passes or unrelated failures are documented
Completion rule: Mark the Asset Registry backend complete only when
all currently confirmed backend requirements are implemented and
verified. HPE/team-lead clarification items should remain documented
rather than being treated as implementation defects.