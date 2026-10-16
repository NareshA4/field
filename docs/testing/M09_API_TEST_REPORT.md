# Module 9: REST API Test Report

**Module:** FON-M09 Spare Parts & Inventory (RESTful API endpoints, Serializers, RBAC, IDOR protection, and Site Scoping)

**Environment:** Python 3.14 / Django 5.2.17, DRF 3.15, OpenAPI / drf-spectacular, PostgreSQL 16. Branch: `feature/m09-inventory`.

---

## 1. API Endpoints Under Test

| HTTP Method | Route | Description | Required Permission |
|---|---|---|---|
| `GET` / `POST` | `/api/v1/inventory/parts/` | List and create spare parts | `inventory.view` / `inventory.manage` |
| `GET` / `PATCH` | `/api/v1/inventory/parts/{id}/` | Retrieve / update part | `inventory.view` / `inventory.manage` |
| `GET` / `POST` | `/api/v1/inventory/warehouses/` | List and create warehouses | `inventory.view` / `inventory.manage` |
| `GET` / `PATCH` | `/api/v1/inventory/warehouses/{id}/` | Retrieve / update warehouse | `inventory.view` / `inventory.manage` |
| `GET` | `/api/v1/inventory/stock-balances/` | Query real-time stock balances | `inventory.view` |
| `POST` | `/api/v1/inventory/stock-balances/receive/` | Receive inbound vendor stock | `inventory.receive` |
| `POST` | `/api/v1/inventory/stock-balances/adjust/` | Adjust stock count | `inventory.manage` |
| `POST` | `/api/v1/inventory/stock-balances/scrap/` | Scrap damaged inventory | `inventory.manage` |
| `POST` | `/api/v1/inventory/stock-balances/transfer/` | Inter-warehouse stock transfer | `inventory.manage` |
| `POST` | `/api/v1/inventory/stock-balances/levels/` | Set min/max and reorder levels | `inventory.manage` |
| `GET` | `/api/v1/inventory/stock-movements/` | View append-only ledger movements | `inventory.view` |
| `GET` / `POST` | `/api/v1/inventory/work-order-parts/` | Query and request parts on WO | `work_order.view` / `work_order.record` |
| `POST` | `/api/v1/inventory/work-order-parts/{id}/reserve/` | Reserve parts for work order | `inventory.issue` |
| `POST` | `/api/v1/inventory/work-order-parts/{id}/issue/` | Issue parts to technician | `inventory.issue` |
| `POST` | `/api/v1/inventory/work-order-parts/{id}/consume/` | Record part consumption | `work_order.record` |
| `POST` | `/api/v1/inventory/work-order-parts/{id}/return-stock/` | Return unused parts | `inventory.issue` |
| `POST` | `/api/v1/inventory/work-order-parts/{id}/reconcile/` | Reconcile completed part line | `inventory.manage` |

---

## 2. API Security & Access Control Verification

### A. Authentication & Session Security
- **Unauthenticated Access**: Requests without valid JWT / Session tokens are rejected with `HTTP 401 Unauthorized`.
- **Suspended Users**: Requests from inactive or suspended tenant members are immediately blocked with `HTTP 403 Forbidden`.

### B. Role-Based Access Control (RBAC)
- **Stores Manager / Storekeeper**: Has full access to receive, adjust, scrap, transfer, reserve, and issue stock.
- **Field Technician**: Restricted to viewing parts catalog and requesting / consuming parts on assigned work orders. Attempts to access `/api/v1/inventory/stock-balances/` or perform receipts return `HTTP 403 Forbidden`.
- **Auditor**: Read-only access to `/api/v1/inventory/stock-movements/` and balances; mutation actions blocked with `HTTP 403 Forbidden`.

### C. Multi-Tenancy & IDOR Prevention
- **Cross-Tenant IDOR**: Attempting to query or manipulate Tenant B's warehouse, part, or stock balance using Tenant A's credentials yields `HTTP 404 Not Found` (zero existence leakage).
- **Site-Scoped RBAC**: Storekeepers scoped to Site A cannot issue or receive stock for warehouses located at Site B.

### D. Input Validation & Schema Compliance
- Zero, negative, or malformed numeric quantities are rejected with `HTTP 400 Bad Request`.
- Scrap operations require a non-empty `reason` field.
- OpenAPI schema validation passes with 0 warnings via `drf-spectacular`.
