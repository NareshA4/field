# Authentication and RBAC foundation -- HPE review document

**Project:** FieldOps Nexus -- Multi-tenant Enterprise Asset, Maintenance & Field Service ERP
**Customer:** VPD Technologies **HPE reference:** HPE-PRD-2026-FOPS02
**Branch reviewed:** `feature/m06-work-order` at `e530db0` plus the uncommitted M06 work-order module
**Purpose:** show HPE who can access what, under which organization and site, how the backend enforces it, and
what evidence proves it.

Every number, table and line reference in this document was **extracted from the running code, a live PostgreSQL
database and a live test run** on the branch named above. Where the implementation is silent on something, it is
labelled rather than filled in. Read `docs/PROJECT_SOURCE_OF_TRUTH.md` for the project's source-priority rules.

## How to read the labels

| Label | Meaning |
|---|---|
| **HPE CONFIRMED** | Stated in the HPE PRD we were given. Quoted or paraphrased from section 7.2 / 8.x. |
| **OUR IMPLEMENTATION DECISION** | HPE is silent. We chose this and recorded it as `D-0NN` in `docs/DECISIONS.md`. |
| **CLARIFICATION REQUIRED** | We could not determine the intent, or two of our own documents disagree. **Not resolved by us.** |

**HPE names no roles, no permission codes and no site-scoping rule anywhere in the material we received.** Roles
(section 3), the permission catalogue (section 4), the role catalogue (section 5) and site scoping (section 6) are
therefore almost entirely OUR IMPLEMENTATION DECISIONS. Only the *existence* of role-based, per-organization access
with organization and site boundaries is HPE-driven. This is stated plainly so no design decision of ours is read
back to us as an HPE requirement.

---

## 1. HPE-confirmed requirements this document answers

| # | HPE CONFIRMED statement | Answered in |
|---|---|---|
| 1 | Multi-tenant: every tenant record is isolated by organization | sections 6, 7, 8 |
| 2 | One account per person; access is granted per organization | section 2, 6 |
| 3 | Role-based access control over module functions | sections 3, 4, 5, 6 |
| 4 | Authorization is enforced on the backend, never only in the UI | sections 6, 7 |
| 5 | Clients see only authorised records (external portal user) | sections 5, 6, 7 |
| 6 | No sensitive identifiers exposed externally (M12) | section 6.6 |

**HPE CONFIRMED (7.2, quoted intent):** users must be able to be given access to the system with roles matching
their job, and a portal user must see *only authorised records*. **HPE gives no role names, no permission list,
no site concept and no portal-user definition** -- see section 12.

---

## 2. What is actually implemented -- component map

Everything below exists in the repository today and is registered in `INSTALLED_APPS`
(`src/config/settings/base.py:95-137`).

| Component | Path | Responsibility |
|---|---|---|
| Identity | `src/apps/accounts/` | `User`, login/logout, invitation, activation, password lifecycle |
| Tenancy | `src/apps/tenancy/` | `Organization`, `Membership`, active-org resolution, member management |
| RBAC | `src/apps/rbac/` | permission catalogue, `Role`, `RolePermission`, `MembershipRole`, site scope |
| Tenant isolation | `src/apps/core/tenant.py` | fail-closed tenant-scoped default manager |
| HTML enforcement | `src/apps/ui/mixins.py` | `TenantPermissionMixin` |
| API enforcement | `src/apps/tenancy/api.py` | `TenantAPIMixin`, `HasOrgPermission`, `MEMBER_ONLY` |
| Object enforcement | `src/apps/core/apiutils.py:23` | `require_permission()` |
| Attachment enforcement | `src/apps/files/access.py` | per-target download checkers |
| Audit | `src/apps/audit/` | immutable record of every access change |
| Login throttling | `src/apps/core/net.py` + django-axes | lockout, trusted-proxy client IP |

**Not implemented, deliberately:** no Django admin is mounted (`src/config/urls.py`); `is_staff` and `is_superuser`
exist on the model for tooling only (`src/apps/accounts/models.py:50`). There is no SSO, no SCIM, no MFA, no
delegated administration, no permission expiry and no custom role templates -- see section 12.

---

## 3. User -> organization -> membership -> role: the relationship

### 3.1 Entity relationship

```
                     +---------------------------------------------+
                     |  accounts.User            (IDENTITY ONLY)    |
                     |  pk UUID, email UNIQUE (Lower) , is_active,  |
                     |  is_platform_admin                         |
                     +----------------------+----------------------+
                                            | 1
                                            |
                    +-----------------------*-----------------------+
                    |  tenancy.Membership                            |
                    |  UNIQUE (user_id, organization_id)             |
                    |  status: INVITED | ACTIVE | SUSPENDED          |
                    +-----------------------+-----------------------+
                                            | 1
                                            |
                    +-----------------------*-----------------------+
                    |  rbac.MembershipRole                           |
                    |  site_id NULL = organization-wide              |
                    |  site_id SET   = that site only               |
                    +-----------------------+-----------------------+
                                            | n
                    +-----------------------*-----------------------+
                    |  rbac.Role   (TENANT-OWNED, per organization) |
                    |  UNIQUE (organization, Lower(name))           |
                    |  UNIQUE (organization, system_key) WHERE <> '' |
                    |  is_owner -> implicitly holds every code      |
                    +-----------------------+-----------------------+
                                            | n
                    +-----------------------*-----------------------+
                    |  rbac.RolePermission                         |
                    |  UNIQUE (role_id, permission_id)             |
                    +-----------------------+-----------------------+
                                            | n
                    +-----------------------*-----------------------+
                    |  rbac.Permission     (GLOBAL, no tenant FK)  |
                    |  code UNIQUE -- mirror of rbac.catalog        |
                    +----------------------------------------------+
```

**Key design facts (all OUR IMPLEMENTATION DECISION unless marked):**

- `User` carries **no** roles and **no** organization. Identity and access are fully separated
  (`src/apps/accounts/models.py:40-44`). This is what makes "one account per person, many organizations" work.
- A user reaches data **only** through a `Membership`. There is no path from `User` to a tenant table.
- `rbac.Permission` is **global** and has no `organization` column. It is a mirror of the code catalogue
  (`src/apps/rbac/models.py:7-8`), populated by `post_migrate` (`src/apps/rbac/apps.py:17-19`).
- `Role` is tenant-owned, so the same role name can mean different things in two organizations.
- `RolePermission` and `MembershipRole` reach the tenant **indirectly** (via `role.organization` and
  `membership.organization`), which is why they carry no `organization` column.

### 3.2 Three independent status gates

None of these implies another; **all three** must pass.

| Gate | Field | Where checked | Effect when false |
|---|---|---|---|
| Account enabled | `User.is_active` | Django `authenticate()` | credentials refused |
| Membership active | `Membership.status == ACTIVE` | `rbac/services.py:91-92` | permission set is the **empty** set |
| Organization live | `Organization.status == ACTIVE` | `tenancy/selectors.py:40-41` | `403 organization_suspended` |

### 3.3 Site assignment sits on the grant, not on the role

`MembershipRole.site` is nullable (`src/apps/rbac/models.py:66-68`). `NULL` means the role is held
**organization-wide**; a site value means the role's *site-scopable* permissions apply to **that site only**.
This is what lets one technician hold a role for Site A and not Site B without a second account. See section 6.

---

## 4. The permission catalogue -- all 38 codes that are live on this branch

Codes are declared in code, one per line, in `<app>/permissions.py` via `rbac.catalog.register(...)`
(`src/apps/rbac/catalog.py:19`). `RbacConfig.ready()` autodiscovers every app's `permissions` module.

**Live count on the reviewed branch: 38 codes -- 29 site-scoped, 9 organization-wide.**
Verified by executing the real registry; also verified against the live `rbac_permission` table (38 rows).

| Code | Scope | Declared in |
|---|---|---|
| `organization.view` | org | `src/apps/rbac/permissions.py:4` |
| `organization.update` | org | `src/apps/rbac/permissions.py:5` |
| `user.view` | org | `src/apps/rbac/permissions.py:6` |
| `user.invite` | org | `src/apps/rbac/permissions.py:7` |
| `user.update` | org | `src/apps/rbac/permissions.py:8` |
| `user.deactivate` | org | `src/apps/rbac/permissions.py:9` |
| `role.view` | org | `src/apps/rbac/permissions.py:10` |
| `role.manage` | org | `src/apps/rbac/permissions.py:11` |
| `audit.view` | **site** | `src/apps/rbac/permissions.py:12` |
| `audit.export` | **site** | `src/apps/audit/permissions.py` |
| `site.view` | **site** | `src/apps/sites/permissions.py:4` |
| `site.create` | org | `src/apps/sites/permissions.py:5` |
| `site.update` | **site** | `src/apps/sites/permissions.py:6` |
| `site.deactivate` | **site** | `src/apps/sites/permissions.py:7` |
| `site.contact.manage` | **site** | `src/apps/sites/permissions.py:8` |
| `zone.view` / `zone.create` / `zone.update` / `zone.deactivate` | **site** | `src/apps/sites/permissions.py:9-12` |
| `calendar.view` / `calendar.create` / `calendar.update` / `calendar.delete` | **site** | `src/apps/sites/permissions.py:13-16` |
| `work_order.view` | **site** | `src/apps/workorders/permissions.py:4` |
| `work_order.view_assigned` | **site** | `src/apps/workorders/permissions.py:4` |
| `work_order.create` | **site** | `src/apps/workorders/permissions.py:5` |
| `work_order.update` | **site** | `src/apps/workorders/permissions.py:5` |
| `work_order.plan` | **site** | `src/apps/workorders/permissions.py:6` |
| `work_order.assign` | **site** | `src/apps/workorders/permissions.py:6` |
| `work_order.dispatch` | **site** | `src/apps/workorders/permissions.py:7` |
| `work_order.start` | **site** | `src/apps/workorders/permissions.py:7` |
| `work_order.hold` | **site** | `src/apps/workorders/permissions.py:7` |
| `work_order.complete` | **site** | `src/apps/workorders/permissions.py:8` |
| `work_order.review` | **site** | `src/apps/workorders/permissions.py:9` |
| `work_order.close` | **site** | `src/apps/workorders/permissions.py:9` |
| `work_order.cancel` | **site** | `src/apps/workorders/permissions.py:10` |
| `work_order.record` | **site** | `src/apps/workorders/permissions.py:10` |
| `work_order.attach` | **site** | `src/apps/workorders/permissions.py:11` |

### 4.1 Organization-wide vs site-scoped -- the complete split

**All 9 organization-wide codes (D-025).** These can *never* be granted for a single site, by design: they are
the permissions that would let a site-limited user escalate to organization-wide control.

`organization.view`, `organization.update`, `user.view`, `user.invite`, `user.update`, `user.deactivate`,
`role.view`, `role.manage`, `site.create`

**The 29 site-scoped codes** are everything else: all `audit.*`, `site.*` except `site.create`, `zone.*`,
`calendar.*`, and all `work_order.*`.

**Consequence, verified in code** (`src/apps/rbac/services.py:118-126`): `site_permissions()` filters the
per-site grant through `catalog.site_scoped_codes()`, so a site assignment can only ever yield a code from the
29. A site assignment **cannot widen** organization-level rights.

### 4.2 Implied permissions

`register(..., implies=("work_order.view_assigned",))` is declared on `work_order.view`
(`src/apps/workorders/permissions.py:13-14`). Expansion is **one level only** and never chains
(`src/apps/rbac/catalog.py:42-49`).
**IMPORTANT for reading section 5: implied codes are expanded at check time, not stored.**
`membership_permissions()` calls `catalog.expand(perms)` on the stored set
(`src/apps/rbac/services.py:104`), and `site_permissions()` does the same (`src/apps/rbac/services.py:126`). No
`RolePermission` row is written for an implied code. This is why a role's *stored* permission count and its
*effective* permission count differ, and why the role screen (`src/apps/rbac/api_views.py:29-32`) shows the stored
set. `RoleSerializer.get_permissions()` special-cases the owner to return the whole catalogue.

### 4.3 Guard rail: an unregistered code is a crash, not a denial

`has_permission()` raises `ValueError` for a code that is not in the registry (`src/apps/rbac/services.py:140-141`).
A typo in a `permission_map` therefore produces a **500 in CI**, never a silent 403 that would look like correct
behaviour. **OUR IMPLEMENTATION DECISION** (fail loud on configuration error).

---

## 5. The roles -- what is implemented, what each is for, and its permissions

**11 role templates** ship in `src/apps/rbac/role_templates.py:23-84`, seeded per organization by
`rbac.seed_system_roles()` on `post_migrate`. **HPE names none of them (see section 12, C-R1).**

### 5.1 Responsibilities

| Key | Name | Responsibility (what the role is *for*) |
|---|---|---|
| `owner` | Organization Owner | Full control of the organization. Holds every code implicitly; sole role that may grant or revoke Owner. |
| `admin` | Organization Admin | Administers users, roles, sites and configuration. Read-only on operational records. |
| `operations_manager` | Operations Manager | Oversees maintenance and field operations across the sites they cover. |
| `asset_manager` | Asset Manager | Owns the asset registry, hierarchy, meters and documents. |
| `maintenance_planner` | Maintenance Planner | Plans, schedules, assigns and dispatches maintenance work. |
| `supervisor` | Maintenance Supervisor | Reviews, closes and holds completed work. |
| `technician` | Technician | Executes **only their own** assigned field jobs. |
| `stores_manager` | Stores Manager | Manages warehouses, stock and part issues. |
| `service_manager` | Service Manager | Owns service levels, requests and contracts. |
| `client_requester` | Client / Requester | External requester using the client portal. |
| `auditor` | Auditor / Report Consumer | Read-only access to records, audit trail and exports. |

### 5.2 Role -> permission matrix

Two columns, because they genuinely differ (section 4.2). **"Stored"** = `RolePermission` rows actually created
in the database. **"Effective"** = what `has_permission()` sees at runtime after one level of implication
expansion. Both were produced by running the real seeding code against a live PostgreSQL database.

| Role | Stored perms (live DB) | Effective perms | Site-scoped | Organization-wide |
|---|---|---|---|---|
| `owner` | 38 | **38 (all)** | 29 | 9 |
| `admin` | 23 | **24** | 15 | 9 |
| `operations_manager` | 22 | **22** | 19 | 3 |
| `asset_manager` | 8 | **9** | 8 | 1 |
| `maintenance_planner` | 11 | **12** | 12 | 0 |
| `supervisor` | 8 | **9** | 9 | 0 |
| `technician` | 8 | **8** | 8 | 0 |
| `auditor` | 9 | **10** | 7 | 3 |
| `stores_manager` | 2 | **3** | 3 | 0 |
| `service_manager` | 2 | **3** | 3 | 0 |
| `client_requester` | **0** | **0** | 0 | 0 |

### 5.3 Full per-role permission listing (current branch)

Expanded from the real catalogue by executing `role_templates.py` against `catalog.py`.
`S` = site-scoped, `O` = organization-wide, `(+)` = granted only via `implies`.

**`owner` -- all 38 codes.** `S` audit.export, audit.view, calendar.create, calendar.delete, calendar.update,
calendar.view, site.contact.manage, site.deactivate, site.update, site.view, work_order.assign, work_order.attach,
work_order.cancel, work_order.close, work_order.complete, work_order.create, work_order.dispatch, work_order.hold,
work_order.plan, work_order.record, work_order.review, work_order.start, work_order.update, work_order.view,
work_order.view_assigned, zone.create, zone.deactivate, zone.update, zone.view.
`O` organization.update, organization.view, role.manage, role.view, site.create, user.deactivate, user.invite,
user.update, user.view.

**`admin` (24).** `S` audit.view, calendar.create, calendar.delete, calendar.update, calendar.view,
site.contact.manage, site.deactivate, site.update, site.view, work_order.view, work_order.view_assigned `(+)`,
zone.create, zone.deactivate, zone.update, zone.view. `O` organization.update, organization.view, role.manage,
role.view, site.create, user.deactivate, user.invite, user.update, user.view.
*No operational write rights:* no `work_order.create/plan/assign/close`, no `user.deactivate` on other roles.

**`operations_manager` (22).** `S` audit.view, calendar.view, site.view, work_order.assign, work_order.attach,
work_order.cancel, work_order.close, work_order.complete, work_order.create, work_order.dispatch, work_order.hold,
work_order.plan, work_order.record, work_order.review, work_order.start, work_order.update, work_order.view,
work_order.view_assigned, zone.view. `O` organization.view, role.view, user.view.
*Full operational control, but no user/role administration.*

**`asset_manager` (9).** `S` calendar.view, site.update, site.view, work_order.view, work_order.view_assigned `(+)`,
zone.create, zone.update, zone.view. `O` site.create.
*On this branch `asset.*` codes are not registered (see section 11, C-R2); with M02/M03 installed the effective
set is 18 including asset.view/create/update/change_status/history.view/document.manage/meter.record/hierarchy.manage.*

**`maintenance_planner` (12).** `S` calendar.view, site.view, work_order.assign, work_order.attach,
work_order.cancel, work_order.create, work_order.dispatch, work_order.plan, work_order.update, work_order.view,
work_order.view_assigned `(+)`, zone.view.

**`supervisor` (9).** `S` site.view, work_order.attach, work_order.close, work_order.hold, work_order.record,
work_order.review, work_order.view, work_order.view_assigned `(+)`, zone.view.

**`technician` (8).** `S` site.view, work_order.attach, work_order.complete, work_order.hold, work_order.record,
work_order.start, work_order.view_assigned, zone.view.
***Note:*** the technician holds `work_order.view_assigned`, **not** `work_order.view`. Visibility is therefore
row-level as well as site-level -- a technician sees only work orders assigned to them, even within their sites
(`src/apps/workorders/views.py:51-56`, `src/apps/workorders/selectors.py:13-15`). Other technicians' work orders
answer **404**.

**`auditor` (10).** `S` audit.export, audit.view, calendar.view, site.view, work_order.view,
work_order.view_assigned `(+)`, zone.view. `O` organization.view, role.view, user.view.
*Read-only by construction: every code is a `.view` code or `audit.export`.*

**`stores_manager` (3).** `S` site.view, work_order.view, work_order.view_assigned `(+)`.

**`service_manager` (3).** `S` site.view, work_order.view, work_order.view_assigned `(+)`.

**`client_requester` (0).** ***Currently grants nothing -- see section 11, C-R3.***

### 5.4 Template resolution is wildcard-based and additive

Templates use `fnmatch` patterns evaluated against the **registered** catalogue
(`src/apps/rbac/role_templates.py:1-6`, resolution in `src/apps/rbac/services.py:40-45`), so a later module's
`work_order.*` codes are picked up automatically on the next sync.

- **Additive only** (D-005, D-030, D-057): `seed_system_roles()` creates missing template permissions and **never
  removes** what an administrator granted or deliberately removed (`src/apps/rbac/services.py:60-62`).
- **`excludes`** removes wildcard matches: `admin`, `operations_manager` and `auditor` do **not** receive
  client-portal permissions through their `*.view` wildcard (D-057). An exact, wildcard-free pattern is never
  excluded.
- **Cross-check, verified by execution: every one of the 38 registered codes is matched by at least one template.**
  No permission is unreachable.

### 5.5 Role-management invariants

All implemented in `src/apps/rbac/services.py` and covered by `tests/test_rbac.py`.

| Rule | Failure code | HTTP | Test |
|---|---|---|---|
| Owner role is locked against rename/delete | `role_locked` | 403 | `test_owner_role_is_locked_and_system_role_undeletable` |
| System roles cannot be renamed or deleted | `system_role` | 400/403 | same |
| Only an Owner may grant or revoke Owner | `owner_only` | 403 | `test_only_owner_can_grant_owner_role` |
| An organization always keeps at least one Owner | `last_owner` | 409 | `test_last_owner_cannot_be_deactivated_or_demoted` |
| Owner can never be site-scoped | `owner_not_site_scoped` | 400 | `tenancy/services.py:168` |
| A role assigned to a member cannot be deleted | `role_in_use` | 409 | `test_role_in_use_cannot_be_deleted` |
| Unknown permission code on a role | `validation_failed` + `details.unknown` | 400 | `test_unknown_permission_rejected` |
| A member must keep at least one role | `roles_required` | 400 | `tenancy/services.py:234-236` |
| You cannot deactivate yourself | `self_change` | 409 | `test_cannot_deactivate_self` |
| A pending invitation is activated by the user, not an admin | `not_activated` | 409 | `tenancy/services.py:247-248` |
| An organization always keeps at least one Owner | `last_owner` | 409 | `test_last_owner_cannot_be_deactivated_or_demoted` |

**Organizations may create custom roles and edit the seeded defaults** (`src/apps/rbac/api_views.py:71-88`, UI at
`/app/roles/`). Seeded roles are *defaults, not restrictions* (D-005). Verified by
`test_org_can_define_custom_roles_and_edit_defaults`, which edits the Technician role down to a single permission
and confirms the technician immediately gains exactly that.

---

## 6. Enforcement: how "who can access what" is actually decided

### 6.1 The authorization decision has six terms, each owned by one place

```
1. IDENTITY        session / JWT verifies who is calling
2. MEMBERSHIP      an ACTIVE Membership in a chosen organization    tenancy/selectors.py
3. PERMISSION      the code is held                                 rbac/services.py
4. TENANT OBJECT   the row belongs to that organization             core/tenant.py
5. SITE SCOPE      the row's site is permitted                      rbac/services.py:site_scope
6. VALID STATE     the transition is legal                          core/workflow.py
```

Removing any term is a security defect. **HPE CONFIRMED:** authorization must be backend-enforced (critical
rule 3, `docs/PROJECT_SOURCE_OF_TRUTH.md`). **OUR IMPLEMENTATION DECISION:** the specific six-term decomposition
and the single-owner-per-term rule.

### 6.2 Backend, API and service layer

| Layer | Mechanism | Code |
|---|---|---|
| HTML view | `TenantPermissionMixin`, subclass sets `required_permission` | `src/apps/ui/mixins.py:12-34` |
| API viewset | `TenantAPIMixin` + `permission_map`; **an unmapped action is denied** | `src/apps/tenancy/api.py:23-58` |
| API object | `require_permission(membership, code, site)` after the row is found | `src/apps/core/apiutils.py:23-27` |
| Service layer | re-checks `has_permission` independently of its caller | e.g. `src/apps/workorders/services.py:36`, `:213`, `:259` |
| Attachments | `files.access.register("app.Model", checker)` per target | `src/apps/files/access.py`, `src/apps/assets/apps.py:14` |
| Navigation | sidebar filtered by `has_permission_anywhere` | `src/apps/ui/context_processors.py:17` |
| Celery / command | `tenant_context(org)` -- no request cycle binds a tenant | `src/apps/core/tenant.py:55-58` |

**`MEMBER_ONLY` -- the one sentinel value in the API permission map.** Most `permission_map` entries are
permission codes such as `"site.view"`. Four entries use the literal string `MEMBER_ONLY = "__member__"`
(`src/apps/tenancy/api.py:20`), and it is consumed at `src/apps/tenancy/api.py:72`. It exists for resources
whose rows are already scoped to the member rather than to a permission. Its only current consumer is
`NotificationViewSet`, whose `list`, `retrieve`, `read` and `read_all` all map to it
(`src/apps/notifications/api_views.py:22`) -- a member may read and mark read **their own** notifications
without holding any `notifications.*` code.

The full check, verbatim from `HasOrgPermission.has_permission`:

```python
# src/apps/tenancy/api.py:64-74
def has_permission(self, request, view):
    membership = getattr(request, "membership", None)
    if membership is None:
        self.message = "Select an organization (X-Organization header) with an active membership."
        raise DRFPermissionDenied(self.message, code="organization_required")
    code = view.get_required_permission()
    if code is None:
        return False  # unmapped action -> deny
    if code == MEMBER_ONLY:
        return membership.is_active  # self-scoped resources (own notifications)
    return rbac.has_permission_anywhere(membership, code)
```

Three consequences a reviewer should note. First, `MEMBER_ONLY` returns `membership.is_active`, so it is
weaker than a permission code but still stronger than "any authenticated user" -- a SUSPENDED or INACTIVE
member is refused. Second, it authorizes the **action**, not the row: `NotificationViewSet.get_queryset()`
still filters `recipient=self.request.user` within the tenant organization
(`src/apps/notifications/api_views.py:28`), so the sentinel is safe only because the queryset is
self-scoped. Third, organization and member management deliberately do **not** use it --
`OrganizationViewSet` requires `organization.view`/`organization.update` and `MemberViewSet` requires
`user.view`/`user.invite`/`user.update`/`user.deactivate`
(`src/apps/tenancy/api_views.py:30`, `:101-105`), so a member cannot read or edit other members through the
sentinel.

**Fail-closed by omission.** `get_required_permission()` (`src/apps/tenancy/api.py:52-58`) looks up
`f"{action}:{method}"` first, so a single action may demand different codes for GET and POST (e.g.
`"documents:post"`); it then falls back to `permission_map[action]`, and only if `permission_map` is
entirely empty does it fall back to the view's `required_permission`. Any action missing from a **non-empty**
map yields `None`, and `None` is not `MEMBER_ONLY`, so the branch above returns `False` and the request is
refused. This is why a newly added viewset action is closed by default until someone declares it, and it is
asserted by `test_unmapped_action_is_denied` (`tests/test_rbac.py:34`).

**Frontend is presentation only.** 48 HTML view declarations declare a permission requirement -- 44 class-level
`required_permission` attributes (`assets` 16, `sites` 13, `audit` 5, `tenancy` 4, `rbac` 3, `workorders` 3) plus
4 computed per request in M06 (`src/apps/workorders/ui_views.py:94`, `:192`, `:279`, `:326`). Every nav item is
filtered by the same catalogue (`src/apps/ui/context_processors.py:17`). **No authorization decision exists in
JavaScript** -- `src/static/js/app.js` contains no permission logic, and the M12 scanner JS only posts a token.

**One documented override of the standard mixin.** `src/apps/notifications/views.py:10` (`MemberOnlyMixin`)
overrides `dispatch()` instead of setting a code, because a user's own notifications are self-scoped data: any
ACTIVE member may read their own. It still refuses anonymous users and still redirects when no membership is
selected, and the queryset filters `recipient=request.user` within the tenant organization
(`src/apps/notifications/views.py:22-23`). It is the UI counterpart of the `MEMBER_ONLY` pseudo-permission and is
covered by `test_notifications_scoped_to_org_and_recipient` and `test_technician_can_use_own_notifications`.

### 6.3 Tenant isolation is fail-closed (`src/apps/core/tenant.py`)

Four contextvar modes, all verified by `tests/test_tenant_isolation.py`:

| Mode | When | `Model.objects` behaviour |
|---|---|---|
| `UNSET` | management command, migration, shell | unscoped |
| `NO_TENANT` | **every request starts here** (`tenancy/middleware.py:20`) | **`.none()` -- empty** |
| the Organization | after a membership is verified | filtered to that organization |
| `PLATFORM` | `/platform/` for `is_platform_admin` | unscoped, explicit |

- `TenantOwnedModel.save()` **rejects cross-tenant writes** with `ValueError("Cross-tenant ...")`
  (`src/apps/core/models.py`). Test: `test_cross_tenant_write_blocked`.
- `.unscoped()` is the only bypass, and it is allow-listed by a test that greps the source tree:
  `test_unscoped_is_only_used_in_trusted_modules`. It is used in exactly three trusted places
  (`tenancy/selectors.py`, `tenancy/services.py`, `rbac/services.py`) -- the identity-to-tenant bridge.

### 6.4 Active-organization selection grants nothing (D-009)

The `X-Organization` header **only selects among the caller's own ACTIVE memberships**. It cannot create
membership. Verified twice, by slug and by UUID: `test_header_cannot_grant_membership`,
`test_header_with_org_uuid_also_cannot_grant` -- both assert `403 not_a_member`.

### 6.5 Platform admin has no tenant rights (D-008)

`is_platform_admin` is independent of organization roles. Verified: `test_platform_admin_has_no_tenant_permissions`
(`403 not_a_member` even with the header set), `test_org_owner_is_not_platform_admin`, and
`test_super_admin_has_no_tenant_access_after_login`.

### 6.6 Row-level scoping, not just site-level

Site scope is the outer bound; some data is narrower still. The technician case is implemented and is the clearest
example: visibility is `work_order.view` for the site **OR** (`work_order.view_assigned` for the site **AND**
assigned to the caller). `src/apps/workorders/views.py:51-56`:

```python
if not rbac.has_permission_anywhere(m, "work_order.view"):
    qs = qs.filter(assigned_to=m)                      # technician: own jobs only
site_ids = [s.pk for s in Site.objects.for_organization(m.organization)
            if rbac.has_permission(m, "work_order.view", site)
            or rbac.has_permission(m, "work_order.view_assigned", site)]
return qs.filter(site_id__in=site_ids)                # then site scope
```

Scoping is applied **server-side in selectors**, never in a template. `audit.selectors.visible_logs()`
(`src/apps/audit/selectors.py:58-63`) hides organization-level rows from site-scoped reviewers entirely.

**HPE CONFIRMED (M12):** identifier tokens are opaque and carry no organization, asset or secret data (D-050), and
a token is only ever looked up inside organizations where the scanner already holds a membership.

---

## 7. What happens in each failure case

Every API error uses one envelope (`src/apps/core/api.py:23-31`):
`{"error": {"code", "message", "details", "request_id"}}`.

| Scenario | UI result | API result | Implemented at | Test |
|---|---|---|---|---|
| No credentials | 302 to `accounts:login` | **401** `not_authenticated` | `settings/base.py:242` | `test_unauthenticated_gets_401`, `test_api_rbac_matrix_for_sites` |
| Correct user, no organization chosen | 302 to `ui:choose_org` | **403** `organization_required` | `tenancy/api.py:67-68` | -- |
| `X-Organization` for an org you are not in | 403 tenant error page | **403** `not_a_member` | `tenancy/selectors.py:35` | `test_header_cannot_grant_membership`, `test_api_header_for_non_member_org_is_rejected` |
| Organization suspended | 403 `organization_suspended` page | **403** `organization_suspended` | `tenancy/selectors.py:41` | `test_suspended_org_denied` |
| Membership suspended | 403 (empty permission set) | **403** | `rbac/services.py:91-92` | `test_suspended_membership_denied`, `test_inactive_membership_has_no_permissions` |
| **Wrong role** on a permitted endpoint | 403 page | **403** `permission_denied` | `tenancy/api.py:74`, `ui/mixins.py:28` | `test_owner_allowed_technician_denied_matrix` |
| **Action missing from `permission_map`** | n/a | **403** (even for Owner) | `tenancy/api.py:70-71` | `test_unmapped_action_is_denied` |
| **Wrong organization** -- another tenant's id | **404** | **404** `not_found` | `core/tenant.py:88-90` | `test_api_member_idor`, `test_api_role_idor`, `test_api_audit_idor_and_listing_scoped`, `test_ui_member_detail_idor`, `test_ui_role_idor` |
| **Wrong site** -- site outside your scope | **404** | **404** | `site_scope()` in selectors | `test_cross_tenant_idor_for_every_m01_route`, `test_zone_with_foreign_parent_via_api_is_not_found` |
| Visible row, right site, wrong permission | 403 | **403** | `core/apiutils.py:26` | `test_api_rbac_matrix_for_sites` |
| Assigning a role from another organization | **400** `cross_tenant_role` | **400** | `rbac/services.py:233` | `test_assigning_foreign_role_rejected` |
| Creating a site-scoped grant for another org | **400** `cross_tenant_site` | **400** | `rbac/services.py:235` |
| 5 failed logins for the same username+IP | 429 friendly lockout page | **429** | axes, `base.py:140-144` | `test_lockout_blocks_correct_password_with_429_and_friendly_page` |
| Forge `active_org_id` in the session | falls back to a valid membership | -- | `tenancy/middleware.py:42` | `test_session_org_cannot_be_forged` |
| Locked out user resets password | `--` `--` | `--` | `test_activation_link_is_single_use` |

**Design rule, repeatedly tested:** *out-of-tenant and out-of-scope answer **404**, not 403* -- the system does
not confirm that another tenant's record exists. *Visible but not permitted answers **403***.

### 7.1 The incident that shaped this (D-023, 2026-10-03)

Behind Nginx every request arrives with `REMOTE_ADDR` = the proxy, so per-IP throttling treated all users as one
client and one noisy user locked out an organization. Three fixes, each with a dedicated test:

1. `client_ip()` trusts `X-Forwarded-For` for exactly `TRUSTED_PROXY_COUNT` hops **from the right**;
   `0` ignores the header so it cannot be spoofed (`src/apps/core/net.py:19-30`).
   Tests: `test_behind_trusted_proxy_throttling_uses_the_real_client_ip`,
   `test_client_supplied_forwarded_for_cannot_spoof_identity`, `test_client_ip_ignores_garbage_headers`.
2. Axes locks on the **combined** username + IP key, not either (`base.py:144`).
   Tests: `test_lockout_is_per_username_and_ip_combination`, `test_one_users_failures_do_not_lock_other_users_on_same_ip`.
3. The username is normalised before counting (`test_case_variants_cannot_dodge_the_failure_counter`).

---

## 8. Database schema -- real DDL from the live database

Generated from the running models against a live PostgreSQL 16 database. UUID primary keys throughout (D-003).

```sql
-- Identity
CREATE TABLE "accounts_user" (
  "id" uuid NOT NULL PRIMARY KEY,  "email" varchar(254) NOT NULL,  "full_name" varchar(150) NOT NULL,
  "password" varchar(128) NOT NULL,  "is_active" boolean NOT NULL,  "is_platform_admin" boolean NOT NULL,
  "is_staff" boolean NOT NULL,  "is_superuser" boolean NOT NULL,  "last_login" timestamptz NULL, ...);

-- Tenant
CREATE TABLE "tenancy_organization" (
  "id" uuid NOT NULL PRIMARY KEY,  "slug" varchar(60) NOT NULL UNIQUE,  "name" varchar(150) NOT NULL,
  "status" varchar(12) NOT NULL,  "timezone" varchar(64) NOT NULL,  "suspended_reason" varchar(300) NOT NULL, ...);

CREATE TABLE "tenancy_membership" (
  "id" uuid NOT NULL PRIMARY KEY,  "organization_id" uuid NOT NULL,  "user_id" uuid NOT NULL,
  "status" varchar(10) NOT NULL,  "invited_by_id" uuid NULL,  "activated_at" timestamptz NULL,
  CONSTRAINT "uniq_membership" UNIQUE ("user_id", "organization_id"));

-- RBAC
CREATE TABLE "rbac_permission" (
  "id" uuid NOT NULL PRIMARY KEY,  "code" varchar(80) NOT NULL UNIQUE,  "module" varchar(40) NOT NULL,
  "description" varchar(200) NOT NULL);                       -- GLOBAL: no organization column

CREATE TABLE "rbac_role" (
  "id" uuid NOT NULL PRIMARY KEY,  "organization_id" uuid NOT NULL,  "name" varchar(80) NOT NULL,
  "system_key" varchar(40) NOT NULL,  "is_owner" boolean NOT NULL);

CREATE TABLE "rbac_rolepermission" (
  "id" uuid NOT NULL PRIMARY KEY,  "role_id" uuid NOT NULL,  "permission_id" uuid NOT NULL,
  CONSTRAINT "uniq_role_permission" UNIQUE ("role_id", "permission_id"));

CREATE TABLE "rbac_membershiprole" (
  "id" uuid NOT NULL PRIMARY KEY,  "membership_id" uuid NOT NULL,  "role_id" uuid NOT NULL,
  "site_id" uuid NULL);                                        -- NULL = organization-wide
```

### 8.1 Constraints and indexes that enforce isolation (from the PostgreSQL catalogue)

| Constraint | Table | Enforces |
|---|---|---|
| `uniq_user_email_ci` | `accounts_user` | case-insensitive email uniqueness |
| `uniq_org_name_ci` | `tenancy_organization` | case-insensitive organization name |
| `uniq_membership` | `tenancy_membership` | one membership per (user, organization) |
| `uniq_role_name_per_org` | `rbac_role` | role names unique per organization, case-insensitively |
| `uniq_system_role_per_org` | `rbac_role` | one seeded role per `system_key` per organization |
| `uniq_role_permission` | `rbac_rolepermission` | no duplicate grant |
| `uniq_membership_role_orgwide` | `rbac_membershiprole` | one org-wide grant of a role per membership |
| `uniq_membership_role_site` | `rbac_membershiprole` | one grant of a role per (membership, site) |

Foreign keys use `ON DELETE PROTECT` for users, roles, permissions and sites: **access-granting rows cannot be
deleted out from under an active member.** Deleting an organization is impossible by design; it is suspended
(`D-012`). Membership/organization and membership/site are indexed for the per-request resolution queries.

---

## 9. Authentication and authorization workflow

### 9.1 Sign-in (browser)

```
POST /accounts/login/  (CSRF-enforced)
  -> EmailAuthenticationForm normalises email (strip + lower)
  -> django-axes  AxesStandaloneBackend   5 failures / 15 min on (username + IP) -> 429 + lockout page
  -> AxesBackend records the REAL username and client IP        -> audit auth.lockout if locked
  -> ModelBackend verifies the PBKDF2 password
  -> session created; login signal -> audit auth.login (one row per active organization)
  -> TenantContextMiddleware resolves active_org_id against the user's ACTIVE memberships
  -> redirect to /app/
```

Password rules: **minimum 12 characters** plus similarity, common-password and numeric validators
(`base.py:126-131`). Generic error text for unknown-user and wrong-password alike, so the form is not a user
enumeration oracle (`test_unknown_user_gets_same_generic_error`).

### 9.2 Invitation and activation

```
platform admin -> create_organization()  -> Organization + seed_system_roles() + Owner invitation
owner/admin    -> invite_member()        -> Membership + role assignments + queued Celery email
new account    -> /accounts/activate/<uidb64>/<token>/  -> sets its own password
                -> activate_pending_memberships()  INVITED -> ACTIVE across organizations
                -> audit auth.password_set + user.activated
existing account invited to a 2nd org -> ACTIVE immediately, no acceptance step (D-006)
```

Activation links are signed, **single-use** and expire after 3 days (`PASSWORD_RESET_TIMEOUT`,
`base.py:132`). Verified end-to-end by `test_full_onboarding_invite_activate_login` and
`test_activation_link_is_single_use`. No plaintext password is ever stored or transmitted (D-007).

### 9.3 API authentication

JWT (simplejwt): 15-minute access token, 1-day **rotating** refresh token, `Bearer`
(`base.py:255-261`). Session auth is also accepted. Login is throttled at 10/min
(`ScopedRateThrottle`, `base.py:253`). Verified by `test_jwt_flow_and_me` and `test_jwt_bad_password_rejected`.

### 9.4 Every API request

```
RequestIdMiddleware  -> assign X-Request-ID (log correlation)
ContentSecurityPolicy-> per-request nonce, no inline handlers
TenantContextMiddleware -> NO_TENANT  (tenant querysets now EMPTY)
DRF authentication  -> JWT or session
TenantAPIMixin.perform_authentication
  -> resolve_membership(user, X-Organization, session active_org_id)  -> 403 not_a_member / organization_suspended
  -> bind tenant.set_current(organization)
HasOrgPermission -> permission_map[action] -> rbac.has_permission_anywhere()  -> 403
view queryset    -> tenant manager filters by organization -> 404 for foreign ids
                   -> site_scope() reduces to permitted sites
                    -> row-level rules (e.g. assigned_to=me)
object action     -> require_permission(m, code, site) -> 403
service           -> transaction.atomic + state machine + audit in the SAME transaction
```

### 9.5 Every access change is audited

| Action | Written by |
|---|---|
| `auth.login`, `auth.logout` | `src/apps/accounts/signals.py:14-27` -- one row **per active organization** |
| `auth.lockout` | `src/apps/accounts/signals.py:30-32` via the axes signal |
| `auth.password_set` / `auth.password_reset` / `auth.password_changed` | `src/apps/accounts/views.py:32`, `:51`, `:64` |
| `user.invited` / `invitation_resent` / `updated` / `deactivated` / `reactivated` / `activated` | `src/apps/tenancy/services.py`, `src/apps/accounts/services.py:46` |
| `user.profile_updated` | `src/apps/accounts/views.py:75` |
| `membership.roles_changed` | `src/apps/rbac/services.py:263` -- only when the set actually changed |
| `role.created` / `role.updated` / `role.deleted` | `src/apps/rbac/services.py:288`, `:309`, `:339` |

Audit rows carry actor, organization, site, before/after JSON, IP and request id. Immutability is enforced in
three layers including a **PostgreSQL trigger** blocking UPDATE and DELETE (D-012,
`src/apps/audit/migrations/0002_audit_immutable_trigger.py`). `audit.view` is site-scoped, so a site-scoped
reviewer sees only their sites' rows (D-053).

---

## 10. Evidence

### 10.1 Live test run, recorded on the reviewed branch

Executed in a throwaway container from the project's own image against a dedicated PostgreSQL 16 test
database, with the working tree mounted. Settings module `config.settings.test`. **No assertion was skipped,
deselected or xfailed.**

```
Django 5.2.17 | pytest 9.1.1 | PostgreSQL 16

tests/test_rbac.py                    17 passed
tests/test_tenant_isolation.py        18 passed
------------------------------------------------
RBAC + tenant isolation subtotal      35 passed   in 26.47s

tests/test_login_integration.py       17 passed   (+1 parametrised x2)
tests/test_onboarding_auth.py         12 passed   (+1 parametrised)
------------------------------------------------
Authentication subtotal               30 passed   in 42.87s

FULL SUITE                           180 passed, 1 failed   in 74.85s
```

**The single failure is `tests/test_workorders.py::test_api_persists_completed_before_separate_review_transition`**
(`src/apps/workorders/ui_views.py:302`, a `KeyError` in the **uncommitted** M06 work-order module). It is a
work-order state-machine issue and is **unrelated to authentication or RBAC**. All 65 auth/RBAC/tenant tests pass.

### 10.2 Test inventory by concern

| Concern | File | Tests | What is proven |
|---|---|---|---|
| Role matrix, invariants | `tests/test_rbac.py` | 17 | owner/technician allow-deny matrix, unmapped action denied, locked Owner role, custom roles, last-owner, self-deactivation, additive sync, inactive membership |
| Tenant isolation | `tests/test_tenant_isolation.py` | 18 | fail-closed manager, cross-tenant write blocked, IDOR on every detail route, header cannot grant membership, suspended membership/org, forged session, `.unscoped()` allow-list |
| Login + lockout | `tests/test_login_integration.py` | 17 | real browser flow with CSRF, PBKDF2 and the production command; generic error; axes identity; 429 lockout page; other users unaffected; combined key; proxy trust; spoofing refused |
| Onboarding + JWT | `tests/test_onboarding_auth.py` | 12 | full invite -> activate -> login, single-use link, platform-only org creation, JWT flow, no password on the command line |
| UI enforcement | `tests/test_ui.py`, `tests/test_ui_actions.py` | 12 + 17 | technician 403 on admin pages, permission-filtered nav, role CRUD via UI, deactivate -> blocked -> reactivate, CSRF enforced, open-redirect blocked, scoped global search |
| Site scope + M01 RBAC | `tests/test_sites.py` | 29 | per-endpoint role matrix for sites/zones/calendars/contacts, cross-tenant IDOR on every M01 route |
| Attachment authz | `tests/test_core.py` | 14 | download scoped and permission-gated; cross-tenant attach blocked |
| Audit | `tests/test_audit.py` | 5 | model/queryset guards, **raw-SQL trigger** blocked, critical actions audited, login/logout audited |

Suite-wide: 151 test functions across 11 files.

### 10.3 Selected assertions, verbatim

Wrong role (`tests/test_rbac.py:18-27`):

```python
for method, url in [("get","/api/v1/members/"), ("get","/api/v1/roles/"),
                    ("get","/api/v1/audit-logs/"), ("get","/api/v1/organization/"),
                    ("get","/api/v1/permissions/")]:
    assert getattr(owner, method)(url).status_code == 200, url
    assert getattr(tech,  method)(url).status_code == 403, url
assert tech.post("/api/v1/members/", body, format="json").status_code == 403
```

Wrong organization answers 404, not 403 (`tests/test_tenant_isolation.py:96-101`):

```python
foreign = Membership.objects.unscoped().get(organization=org_b, user=owner_b)
r = as_user(owner_a, org_a).get(f"/api/v1/members/{foreign.pk}/")
assert r.status_code == 404
assert r.json()["error"]["code"] == "not_found"
```

The header cannot grant membership (`tests/test_tenant_isolation.py:114-119`):

```python
"""X-Organization only SELECTS among the caller's own memberships; it never grants access."""
r = as_user(owner_a, org_b).get("/api/v1/members/")
assert r.status_code == 403 and r.json()["error"]["code"] == "not_a_member"
```

Wrong role against M01 (`tests/test_sites.py`, `test_api_rbac_matrix_for_sites`):

```python
tech = as_user(tech_a, org_a)                      # technician holds site.view only
assert tech.get("/api/v1/sites/").status_code == 200
assert tech.post("/api/v1/sites/", {...}).status_code == 403
assert tech.patch(f"/api/v1/sites/{site_a1.pk}/", {...}).status_code == 403
assert tech.post(f"/api/v1/sites/{site_a1.pk}/deactivate/", {...}).status_code == 403
assert tech.post("/api/v1/zones/", {...}).status_code == 403
assert tech.post("/api/v1/calendars/", {...}).status_code == 403
assert tech.post("/api/v1/site-contacts/", {...}).status_code == 403
assert Site.objects.get(pk=site_a1.pk).name == site_a1.name      # refused writes changed nothing
```

Fail-closed tenant manager (`tests/test_tenant_isolation.py:11-16`):

```python
token = tenant.set_current(tenant.NO_TENANT)
try:
    assert Role.objects.count() == 0
finally:
    tenant.reset_current(token)
```

### 10.4 Auth tests follow the mandatory browser-mimicking rule

`docs/DEVELOPMENT_RULES.md` rule 8, written after the 2026-10-03 incident, requires every authentication test to
use a **CSRF-enforcing** client (`Client(enforce_csrf_checks=True)`), GET-then-POST with the scraped
`csrfmiddlewaretoken` and `HTTP_ORIGIN`, the **production PBKDF2 hasher**, accounts created through the **real**
management command, at least two users sharing one IP, and a simulated proxy. All of this is implemented --
`tests/test_login_integration.py` overrides the MD5 test hasher per test and drives the real login form.
Lockout tests additionally assert the stored `AccessAttempt` identity, the 429 response and that **other users
stay unlocked**.

### 10.5 Continuous integration (`.github/workflows/ci.yml`)

`ruff check` -> `makemigrations --check --dry-run` -> `migrate` -> `check --deploy --fail-level ERROR` ->
`pytest --cov=src/apps`, plus `pip-audit`, `gitleaks` and an image build, on PostgreSQL 16 + Redis 7.

### 10.6 What is NOT yet proven -- stated plainly

Per **D-048**, the Team Lead has ruled that browser acceptance for Phases 1-3 (M01-M08) is **outstanding**, and
M06's own document records `IMPLEMENTED, browser acceptance PARTIAL`. Therefore:

- **No manual browser walkthrough of the RBAC screens has been signed off.** All evidence above is automated.
- The **M06 work-order module is uncommitted** work in progress and has one failing test (section 10.1).
- `apps.assets` (M02/M03) **code exists but is not in `INSTALLED_APPS` on this branch**, so its 9 permission codes
  do not register here and its endpoints are inactive. Its tests were not run as part of this review.
- No load, penetration or performance testing of the authorization path has been performed.

---

## 11. Open clarifications

These are **not** resolved by us. Each needs a decision from HPE or the project Team Lead.

### C-R1 -- HPE has defined no roles
**CLARIFICATION REQUIRED (HPE).** We have implemented 11 roles with the responsibilities in section 5.1. HPE's
material names no role, no permission and no job function. Please confirm the role set, or state the intended
roles, so we can map them to ours. Until then all 11 are **OUR IMPLEMENTATION DECISIONS** (D-005, D-030, D-057).
The design already supports any answer: an organization can create custom roles and edit the seeded defaults.

### C-R2 -- Roles for unbuilt modules resolve to a partial permission set
**CLARIFICATION REQUIRED (informational, self-resolving).** The templates were written for the full 15-module
system, so they already name permissions such as `maintenance.*`, `inventory.*`, `sla.*` and `incident.*` that do
not exist yet. Those patterns simply match nothing today; they will populate automatically as those modules land,
because seeding is additive (section 5.4). The visible effect on this branch:

| Role | Effective now | Effective with all modules |
|---|---:|---|
| `maintenance_planner` | 12 | 24 (pattern-intended) |
| `stores_manager` | 3 | full `inventory.*` |
| `service_manager` | 3 | full `incident.*`, `sla.*`, `contract.*`, `portal.manage` |
| `operations_manager` | 22 | plus `incident.*`, `maintenance.*`, `checklist.*`, `inspection.*` |

**No action needed from HPE.** It is recorded because an HPE reviewer reading the role screen on this branch
will see smaller numbers than the templates suggest.

### C-R3 -- The Client / Requester role currently grants nothing
**CLARIFICATION REQUIRED (HPE + Team Lead).** `client_requester` exists and is seeded, but its patterns are
`portal.request.create|view|confirm` and **no `portal.*` code is registered yet** -- module M13 is not built. The
role therefore resolves to **0 permissions** on this branch, verified against the live database.

This matters because **HPE CONFIRMED (7.2)** requires that *clients see only authorised records*, and a
`client_requester` user provisioned today would be unable to do anything. We have **deliberately not** created
speculative `portal.*` codes, because `docs/DEVELOPMENT_RULES.md` forbids stub functionality and
`docs/PROJECT_SOURCE_OF_TRUTH.md` rule 8 forbids inventing HPE requirements. The full M13 design, including
per-asset grants so a client sees only their own assets, is recorded in **D-051** and awaits Team Lead
confirmation.

### C-R4 -- Our own documents disagree on who may read the API schema
**CLARIFICATION REQUIRED (Team Lead).** **D-055** records the decision that the OpenAPI schema and Swagger UI
"require a signed-in user". The code does the opposite: `SPECTACULAR_SETTINGS["SERVE_PERMISSIONS"]` is
`AllowAny` (`base.py:268`, commit `1b40f20`, "Make API docs and OpenAPI schema public"). The code follows the
later commit. **Data endpoints remain protected** -- verified by `test_openapi_docs_are_public_but_data_endpoints_are_not`.
One of the two must be corrected; we have not chosen.

### C-R5 -- Roles vs the portal user
**CLARIFICATION REQUIRED (HPE).** Is a client/requester a *role* inside an organization, or a separate external
user type outside it? We implemented the former (an ordinary `Membership` whose only permissions are
`portal.request.*`, per D-051), because it keeps one identity model. If HPE requires external users to sit
outside the organization boundary, that is a data-model change and needs a decision before M13 is built.

### C-R6 -- Site scope is our concept
**CLARIFICATION REQUIRED (HPE).** HPE material we received does not describe site-level access control. We
introduced it (D-017, D-025) because M01 Site & Location Master is the physical boundary of field work and
technicians work at specific sites. If HPE's intent is organization-wide access only, the `site_id` column on
`rbac_membershiprole` and the `site_scoped` flag can be ignored without a schema change.

---

## 12. Known limits and deliberate non-implementations

| Item | Status |
|---|---|
| PostgreSQL Row Level Security | **Not adopted.** Isolation rests on the fail-closed `TenantManager`, the `organization` FK, service-level same-tenant checks and the tests. D-055 explains the cost/benefit and recommends revisiting after UAT. |
| `TRUNCATE` by a table owner | Bypasses the audit row trigger. Mitigation: the runtime DB role must not hold `TRUNCATE` (`scripts/harden_db_roles.py`). D-053. |
| SSO / SCIM / MFA | Not implemented. HPE defines no such requirement. |
| Permission expiry, time-bound grants | Not implemented. Roles are indefinite until revoked. |
| Delegated administration | Not implemented. Only an Owner may grant Owner. |
| Custom role templates | Not implemented. Organizations may create **custom roles** with any permission set, but cannot author new **templates**. |
| Separation of duties (SoD) | Not implemented. No rule prevents one person holding two roles whose permissions conflict. |
| Approval workflow for role changes | Not implemented. Role changes are applied immediately and audited. |
| Implication chaining | One level only, by design (section 4.2). |
| Django admin | Not mounted. `is_staff` reaches nothing. |

---

## 13. Adding a module: the enforced recipe

1. **Declare the codes** in `src/apps/<module>/permissions.py` via `rbac.catalog.register`, one call per code,
   with `site_scoped=True` for anything a site-limited user could ever hold. Register a module-wide prefix
   (`work_order`, `inventory`) so wildcard templates keep working.
2. **Update the templates** in `src/apps/rbac/role_templates.py`; a wildcard picks new codes up automatically and
   seeding is additive. Use `excludes` when a wildcard would leak a permission to the wrong role.
3. **Gate the HTML views** with `TenantPermissionMixin` + `required_permission`; register nav in
   `<app>/navigation.py` with the same code.
4. **Gate the API** with `TenantAPIMixin` + a **complete** `permission_map` covering every action including custom
   `@action`s. Reduce each queryset with `site_scope()` and each mutation with `require_permission()`.
5. **Re-check in the service layer** so a later mis-wired endpoint cannot bypass the rule
   (`src/apps/workorders/services.py:36` is the reference example).
6. **Write the tests** (section 14) and record a `D-0NN` entry in `docs/DECISIONS.md`.

### Mandatory test matrix

Per `docs/DEVELOPMENT_RULES.md` and `docs/PROJECT_SOURCE_OF_TRUTH.md` section 6, every sensitive endpoint needs:
**allowed role / wrong role / unauthenticated / wrong organization / wrong site object / invalid state**, plus an
**IDOR test for every new detail route.** Use `@pytest.mark.parametrize` for role matrices, as
`tests/test_sites.py::test_api_rbac_matrix_for_sites` does.

### Review checklist

- [ ] Every new code declared in `<module>/permissions.py`; nothing inserted directly into the `Permission` table.
- [ ] `site_scoped=True` wherever a site-limited role could reasonably hold it; organization-wide for `user.*`,
      `role.*`, `organization.*`, `site.create`.
- [ ] Templates reviewed; `excludes` reviewed.
- [ ] `permission_map` complete, including custom actions; nothing relies on a default.
- [ ] Every list and detail queryset goes through `site_scope()`; no template-only filtering.
- [ ] Cross-tenant and out-of-scope ids answer **404**.
- [ ] Services re-check permission and write audit inside one transaction.
- [ ] Celery tasks wrap tenant work in `tenant_context(org)`.
- [ ] `pytest -q`, `ruff check src tests`, `makemigrations --check --dry-run` clean.

---

## 14. Reference

**Decisions cited:** D-003 (UUID keys), D-004 (tenant model), D-005 (per-organization roles, additive templates),
D-006 (invite existing user), D-007 (no plaintext passwords), D-008 (platform admin has no tenant rights),
D-009 (active-org selection grants nothing), D-010 (JWT + throttling), D-011 (error envelope), D-012 (audit
immutability, no hard delete), D-017/D-025 (site scope), D-023 (login throttling model), D-024 (operator secrets),
D-028 (attachment authorisation registry), D-030/D-057 (role templates), D-037 (M06 visibility),
D-050 (opaque identifiers), D-051 (M13 client portal), D-053 (M15 audit, site-scoped `audit.view`),
D-055 (security hardening), D-061 (gap closure). Full text in `docs/DECISIONS.md`.

**Key files:** `src/apps/accounts/` · `src/apps/tenancy/` · `src/apps/rbac/` · `src/apps/core/tenant.py` ·
`src/apps/ui/mixins.py` · `src/apps/tenancy/api.py` · `src/apps/core/apiutils.py` · `src/apps/files/access.py`

**Related:** `docs/PROJECT_SOURCE_OF_TRUTH.md` · `docs/ARCHITECTURE.md` · `docs/DEVELOPMENT_RULES.md` ·
`docs/DOMAIN_MODEL.md` · `docs/DECISIONS.md` · `docs/modules/M06_WORK_ORDER.md` · `tests/test_rbac.py` ·
`tests/test_tenant_isolation.py` · `tests/test_login_integration.py` · `tests/test_onboarding_auth.py`

Done means: requirement, feature, database, service, permission, tenant isolation, API/view, UI, browser workflow,
PostgreSQL verification, audit verification, integration verification, automated + negative + regression tests
(see `docs/PROJECT_SOURCE_OF_TRUTH.md` section 5). **Browser acceptance for this foundation is outstanding per
D-048 and is not claimed here.**
