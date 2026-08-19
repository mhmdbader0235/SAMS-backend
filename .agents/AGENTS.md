# Project Architectural Rules & Guidelines — SchoolDesk (Doumind)

> **Canonical source:** this file (`/.agents/AGENTS.md`) at the repo root.
> `back/.agents/AGENTS.md` is a required mirror because `back/` is its own git
> repository — **the two must be kept byte-identical.** Verify with
> `diff .agents/AGENTS.md back/.agents/AGENTS.md` after any edit.
> Last reconciled against the code: 2026-08-18.

## 🛑 CORE DIRECTIVE & CONFLICT RESOLUTION
Before executing any prompt, generating code, or modifying files:
1. Review the rules and constraints below.
2. If any user request conflicts with these architectural boundaries, multi-tenant isolation rules, or security guidelines: **STOP, refuse execution, state the conflict clearly, and ask for permission/clarification.**

---

## 🏛️ 1. LAYERED BACKEND ARCHITECTURE (DOMAIN-DRIVEN / FEATURE-FIRST)
The backend (`FastAPI` + `Python 3.12` + `asyncpg`, no ORM) is organised **by business
domain, not by technical layer**. Do NOT create top-level `routers/`, `services/`, or
`repositories/` folders. Within a domain you MUST NOT bypass layers or mix
responsibilities.

### Actual folder structure
```text
app/
├── core/                      Global, shared across domains:
│                              config.py, database.py, dependencies.py,
│                              schemas.py, opa.py, keycloak_admin.py
└── domains/<feature>/         e.g. auth, events, students, invitations, notifications
    ├── router.py              FastAPI endpoints
    ├── service.py             Business logic
    └── repository.py          DB queries
```

1. **Router Layer (`app/domains/*/router.py`):**
   - Handles incoming HTTP requests, headers, CORS, and Pydantic validation.
   - Extracts JWT Bearer claims (`user_id`, `tenant_id`, `roles`).
   - **STRICT RULE:** NO business logic, calculations, or database queries in the Router.

2. **Service Layer (`app/domains/*/service.py`):**
   - Contains all business rules, lifecycle state machines, and workflow calculations.
   - Responsible for calling the **OPA client** (`app/core/opa.py`) to verify action authorization before mutating data.
   - Encrypts sensitive PII payload fields (`cryptography.fernet`) before database dispatch.

3. **Repository Layer (`app/domains/*/*repository.py`):**
   - **STRICT RULE:** The ONLY layer permitted to execute raw SQL / `asyncpg` queries against PostgreSQL.
   - Responsible for setting dynamic tenant contexts (e.g., `SET search_path TO "tenant_x"`).

### Additional rules
- **Self-contained domains:** a domain should contain everything it needs to function.
- **No circular imports:** domains should rarely import from other domains. If they
  must, import at the **Service or Repository** level — **NEVER** at the Router level.
- **Core is global:** use `app.core` for anything shared across domains.

### ⚠️ Known deviations (existing debt — do NOT copy these patterns)
- `domains/events/` has **no** `service.py` or `repository.py`; it calls `TenantService`
  and `TenantRepository` directly — including from the router, which violates the
  no-cross-domain-import-in-router rule above. New event logic belongs in a proper
  `events/service.py`.
- `domains/tenant/` has **no** `router.py` — it is a shared service/repository library
  (`service.py`, `tenant_repository.py`, `control_plane_repository.py`,
  `user_repository.py`), not an HTTP domain.
- No domain has its own `schemas.py`; all Pydantic models are centralized in
  `app/core/schemas.py` (except `app/schemas/invitation.py`).
- Only `domains/invitations/` currently has the full router + service + repository triple.

---

## 🔒 2. STRICT MULTI-TENANT DATABASE ISOLATION
Tenant data isolation is paramount and enforced at the database level.

- **Isolation Strategy:** Strict Database-per-Tenant / Schema Isolation (`PostgreSQL 16` via Docker).
- **Zero Cross-Querying:** NEVER write SQL queries, joins, or repository methods that attempt to query across tenant schemas or databases.
- **Tenant Context Propagation:**
  1. Keycloak / APISIX passes the validated JWT containing the `tenant_id` claim.
  2. Router extracts `tenant_id`.
  3. Service passes `tenant_id` to Repository.
  4. Repository binds the connection explicitly to the target tenant scope BEFORE executing any SQL statement.
- **OPA Tenant Guard:** OPA policies must explicitly verify `input.user.tenant_id == input.resource.tenant_id` for resource-specific actions.

---

## 🔐 3. AUTHENTICATION (KEYCLOAK) VS. AUTHORIZATION (OPA)
We decouple AuthN and AuthZ completely:

- **Keycloak (Identity Provider - AuthN ONLY):**
  - Handles login, user identity, and issues RS256/HS256 signed JWT tokens containing `sub`, `roles`, and `tenant_id`.
  - **STRICT RULE:** Keycloak is strictly used for **Authentication (AuthN)**. Keycloak is NOT used for authorization decisions, fine-grained policies, or access control enforcement.
- **Apache APISIX (API Gateway):**
  - Validates JWT signatures at the network edge and routes `/api/v1/*` traffic.
- **Open Policy Agent (OPA - Sole AuthZ Engine):**
  - OPA is the **SINGLE source of truth for Authorization (AuthZ)** across the entire application.
  - Runs in a dedicated Docker container (`openpolicyagent/opa:latest`) on port `8181`.
  - Service Layer queries OPA endpoint (`http://opa:8181/v1/data/school/authz/allow`) with structured JSON inputs:
    ```json
    {
      "input": {
        "user": { "id": "usr_123", "tenant_id": "school_a", "roles": ["teacher"] },
        "action": "event:edit",
        "resource": { "type": "event", "tenant_id": "school_a", "status": "draft", "owner_id": "usr_123" }
      }
    }
    ```

---

## 🎭 4. RBAC MATRIX & WORKFLOW LIFECYCLE

### ⚙️ Permissions-to-Role Mapping Matrix — ACTIVE ROLES (Phase 1)

These 6 roles are the **only** supported set. They are the keys present in
`app/core/dependencies.py` → `COMPOSITE_ROLE_PERMISSIONS` and in
`front/src/store.js` → `COMPOSITE_ROLE_PERMISSIONS`.

Aliasing rules that apply everywhere:
- `admin` and `administrator` are **aliases for `school_admin`**.
- `school_admin` / `super_admin` bypass the role check on every workflow transition.

| High-Level Role | Granular Role Permissions | Mapped Functions |
|----------------|---------------------------|-------------------|
| **`super_admin`** | Unrestricted access across all schemas (`*`) | Global platform administration, tenant provisioning, system diagnostics, and audit logs. Automatically bypasses policy checks (`allow = true`). |
| **`school_admin`** | `school:*`, `level:*`, `class:*`, `user:*`, `teacher:*`, `student:*`, `event:*`, `resource:*`, `enrollment:*`, `billing:audit`, `billing:invoice`, `subsidy:manage`, `health:*`, `safety:manage`, `announcement:manage`, `audit:view` | Full school tenant administration: manage levels, classes, staff users, invitations, event oversight, subsidies, and student medical/safety plans. |
| **`manager`** | `school:read`, `level:read`, `class:read`, `teacher:read`, `parent:read`, `student:read`, `user:view`, `event:read`, `event:view`, `event:review`, `event:publish`, `event:view_draft`, `event:audience_predict`, `resource:view`, `resource:price`, `resource:set_cost`, `resource_type:read`, `billing:invoice`, `billing:pay`, `billing:refund`, `billing:audit`, `billing:view_payment`, `subsidy:manage`, `enrollment:view_roster`, `enrollment:read`, `announcement:manage`, `notification:send`, `feedback:view` | Operations and event review: approve/reject proposed events, establish resource costs and pricing, issue invoices/refunds, and audit budgets. |
| **`teacher`** | `school:read`, `level:read`, `class:read`, `teacher:read`, `student:read`, `user:view`, `event:create`, `event:read`, `event:view`, `event:edit`, `event:patch`, `event:delete`, `event:clone`, `event:propose`, `event:submit`, `event:view_draft`, `event:audience_edit`, `event:audience_predict`, `resource:create`, `resource:view`, `resource:edit`, `resource:update`, `resource:delete`, `resource_type:create`, `resource_type:read`, `enrollment:teacher_approve`, `enrollment:view_roster`, `enrollment:read`, `health:view`, `notification:read`, `feedback:view`, `feedback:create` | Class teacher & trip lead: create event drafts, allocate resources, submit for manager approval, approve student enrollments, and view attendee health info. |
| **`parent`** | `school:read`, `user:profile_read`, `user:profile_edit`, `student:view_linked`, `event:read`, `event:view`, `enrollment:parent_approve`, `enrollment:cancel`, `enrollment:read`, `billing:pay`, `billing:view_payment`, `health:manage_child`, `notification:read`, `feedback:create` | Parent/guardian: view published trips for child's class, approve/enroll children, cancel enrollments, pay trip invoices, update child health info, and leave feedback. |
| **`student`** | `school:read`, `user:profile_read`, `user:profile_edit`, `event:read`, `event:view`, `enrollment:request`, `enrollment:read`, `notification:read`, `feedback:create` | Student: browse published trips for their class, submit enrollment requests, view notifications, and leave feedback. |

### 🔮 Phase 2 roles — PLANNED, NOT IN SCOPE. Do NOT build on these.

`finance`, `event_teacher`, `school_nurse`, `auditor` are **not** part of Phase 1.
Some are partially scaffolded in the codebase; scaffolding is **not** a working
feature. Do not route new work through them, and do not assume they grant access.

| Phase 2 Role | Current real state in the code |
|---|---|
| **`finance`** | Deeply scaffolded but **non-functional end-to-end**: present in DDL role `CHECK` constraints, `VALID_ROLES`, the OPA policy (action + HTTP rules), 8 router guards, and `POST /api/v1/students/finance`. **Has NO entry in `COMPOSITE_ROLE_PERMISSIONS` on either backend or frontend.** Its workflow endpoints are dead (see below) and the Finance dashboard perspective button renders with no panel behind it. |
| **`event_teacher`** | Scaffolded in DDL, `VALID_ROLES`, router guards, and frontend role lists. Has **zero OPA policy coverage** and **no entry in either `COMPOSITE_ROLE_PERMISSIONS`**, so it currently resolves to no capabilities. The state machine treats it as `teacher` (see aliasing below). |
| **`school_nurse`** | **Documentation only.** Appears in `docs/keycloak_roles_catalog.json` and `docs/PERMISSIONS_CATALOG_REFERENCE.md`. No executable code anywhere. |
| **`auditor`** | **Documentation only.** Same as above. |

### 📋 Event Lifecycle State Machine (CANONICAL)

Enforced by `TRANSITIONS` in `app/domains/tenant/service.py` → `transition_event()`.

```text
draft ──teacher submits──▶ proposed ──manager accepts──▶ approved ──teacher publishes──▶ published
                              │
                              └──manager rejects (reason required)──▶ draft
```

**There is deliberately NO `proposed → published` shortcut.** Publishing must pass
through `approved`, because `published_at` stamping and the student/parent
notification fan-out both live in the `approved → published` branch. A direct jump
silently published events with no timestamp and no notifications.

| Transition | Actor | Preconditions | Side effects |
|---|---|---|---|
| `draft → proposed` | teacher (**creator only**) | At least one class must be mapped | Sets `submitted_at`; recomputes `predicted_attendance` (= `round(0.8 × students in mapped classes)`); clears `rejection_reason`; notifies **all managers** |
| `proposed → approved` | manager | — | Sets `manager_approved_at` + `manager_reviewer_id`; clears `rejection_reason`; notifies the event creator |
| `proposed → draft` (reject) | manager | **Non-empty `reason` is required** | Stores `rejection_reason`; notifies creator with the reason; teacher regains edit rights |
| `approved → published` | **teacher** (canonical) | — | Sets `published_at`; fans out notifications to every student in the mapped classes **and** each student's linked parents |
| `approved → published` | manager (**override**) | — | Same as above |

**Role rules:**
- **Teacher Rule:** Can edit or delete an event ONLY IF `resource.status == "draft"`.
- **Manager Rule:** Can accept or reject an event ONLY IF `resource.status == "proposed"`. May publish only an **already-approved** event.
- **Parent/Student Rule:** Can view an event ONLY IF `resource.status == "published"` AND `resource.class_id` matches child/student class mapping.
- `school_admin` / `super_admin` bypass the role check on every transition.
- `event_teacher` is treated as `teacher` by the state machine.

### ⚠️ Phase 2 / dead surface — exists but does nothing

Do not build against these; they are reserved for Phase 2.

- **Unreachable DB enum values:** `resource_planning`, `finance_approval`,
  `final_review` are in the `event_status` enum but no transition produces them.
- **Always HTTP 400** (their action strings are not in `TRANSITIONS`):
  `POST /{id}/finance-submit`, `POST /{id}/event-teacher-decision`, and the
  `return_to_finance` arm of `POST /{id}/final-decision`.
- **`GET /finance-queue`** — live endpoint, but structurally always empty because it
  filters on the unreachable `finance_approval` status.
- **`GET /manager-queue`** also filters `final_review`, which never matches any row.
- **Statuses referenced in code but absent from the enum:** `ready_to_publish`,
  `pricing_review` — dead branches.

---

## 🛡️ 5. SENSITIVE DATA & PII PROTOCOLS
For tables containing sensitive records (National IDs, emergency contacts, medical records, financial data):

1. **Encryption at Rest:** Application-layer encryption using `cryptography.fernet` in the **Service Layer** BEFORE passing payload to the Repository.
2. **Data Masking:** Pydantic response models MUST mask fields by default (e.g., `********89`) unless explicitly requested by an authorized `school_admin`.
3. **Zero PII Logging:** Never write PII parameters to application logs, standard output, or OPA input payloads.
4. **Audit Logs:** Every read/write operation on sensitive tables must trigger an immutable audit log capturing `(user_id, tenant_id, timestamp, action, resource_id)`.

---

## 💻 6. FRONTEND DESIGN & UI/UX STANDARDS (Vue 3 + Vite + Tailwind)
- **Light-Mode-First UI Theme:** The application is strictly Light Mode first (`bg-slate-50`, `bg-white`, `border-slate-200`, `text-slate-900`, `text-slate-600`). Avoid dark mode traps, unreadable dark boxes, and glowing neon gradients.
- **Rectangular Geometric Design:** Use crisp, structured, dashboard-grade rectangular elements (`rounded` or `rounded-sm`). Avoid excessive pill shapes, bubbles, and blur effects.
- **Natural Numerical Grade Sorting:** Grade levels MUST always sort numerically: `Kindergarten/Early Years` &rarr; `Grade 1` &rarr; `Grade 2` &rarr; ... &rarr; `Grade 12` (never alphabetical string sorting where `Grade 10`, `11`, `12` follow `Grade 1`).
- **2-Line Checkbox Grade Filter:** The Live Structure & Classes filter must be presented in a clean, 2-line symmetrical checkbox grid (e.g., 6–7 items per line) with *Select All*, *Deselect All*, and *Reset* controls.
- **Zero-Scrolling Segmented Dashboard Hub:** Multi-role dashboard views must use a high-contrast segmented tab bar (`Published Trips & Activities`, `Manager Review Queue`, `Teacher Workspace`) with real-time count badges instead of stacking long queues vertically.
- **Audience Scope & Class Filtering:** Published events MUST be strictly scoped to their target classes (`WHERE e.status = 'published' AND ecm.class_id = $1`). Events mapped to a specific class (e.g. Class 7A) must **never** appear for students or parents belonging to other classes (e.g. Class 7B).
- **Multi-Child Enrollment Support:** Parents linked to children in different classes must be able to enroll each eligible child into events targeting their specific class.
- **Direct Action Buttons:** In `EventPublishedCard.vue`, render clear, direct `Enroll [Child Name]` buttons for unenrolled linked children instead of generic text inputs or multi-select dropdowns.
- **Component Naming:** Frontend components live flat in `front/src/components/` and use a domain-prefix + role suffix convention (`Event*`, `Manage*View`, `User*View`, wizard steps as `Step*.vue`). Note the published-event card is `EventPublishedCard.vue` — **not** `PublishedEventCard.vue`.
- **Optimistic UI Updates (0ms Latency):** Click handlers for enrollment, approval, and cancellation MUST optimistically mutate local state immediately (0ms delay) so buttons and badges update instantly on click, syncing with the API in the background.
- **Sticky Actions:** Action bar footers in wizard and details views must remain sticky at the bottom (`sticky -bottom-8`) while top page headers scroll away naturally.
- **Authentication Passphrase Challenge:** Registration form is protected and hidden by default until the user enters the invite passphrase (`regester123`). Inputs must start empty (`""`).

---

## 🏫 7. ACADEMIC STRUCTURE & CURRICULUM WIZARD STANDARDS
- **3 Canonical Curriculum Systems:** The Curriculum Ladder Wizard supports exactly 3 systems:
  1. **UK National Curriculum** (Early Years / Reception to Year 13)
  2. **International Standard** (Kindergarten to Grade 12)
  3. **Customer / Custom Standard** (Configurable)
- **Full 14-Stage Live Preview:** The hierarchy preview renders all 14 canonical educational stages without clipping or vertical cutoffs.
- **Locked Grade Prefix in Classes:** Class section names must have the selected grade display name permanently locked as a static, non-editable prefix (e.g. `Grade 1 - A`, `Year 7 - B`).
- **Section Quantity Limit:** Enforce a maximum of **25 class sections** per grade (A–Y or 1–25).
- **Uncapped Student Capacity:** Student capacity restrictions are unblocked (unlimited students can be enrolled), while displaying live occupied seat counts and roster metrics.

---

## 📝 8. EVENT PLANNING & LIFECYCLE OVERVIEW

### 1️⃣ Create a Draft (Teacher)
1. **Open the Event Wizard** → *Step 1 – Basics* (title, description, address, date, school-subsidy).
2. **Step 2 – Audience** (select classes; predicted attendance = 0.8 × total students).
3. **Step 3 – Resources** (choose resource types: transport, staffing, meals, custom; set quantity).
4. **Step 4 – Review** (verify data, click Save Draft or Send for Approval).

> *Result*: An `events` row created with `status = draft`. Cannot be edited once status leaves `draft`.

### 2️⃣ Submit for Manager Review (Teacher)
- **Teacher** (the event **creator only**) clicks **“Send for Approval”** → backend transition `draft → proposed` via `POST /api/v1/events/{id}/submit`. Requires **at least one mapped class**. `submitted_at` set, `predicted_attendance` recomputed; notification sent to all tenant Managers.

> *Result*: `status = proposed`. Teacher UI shows event as read-only.

### 3️⃣ Manager Accepts or Rejects
`POST /api/v1/events/{id}/manager-decision`

| Action | Actor | New Status | Side-effects |
|--------|-------|------------|--------------|
| **Accept** | Manager | `approved` | `manager_approved_at` & `manager_reviewer_id` recorded; creator notified. **Does not publish** — the teacher still has to publish. |
| **Reject** | Manager | `draft` | Requires non-empty reason; teacher receives notification with the reason and regains edit rights. |

### 4️⃣ Publish to Students & Parents (Teacher)
`POST /api/v1/events/{id}/publish`

| Action | Actor | New Status | Side-effects |
|--------|-------|------------|--------------|
| **Publish** | **Teacher** (canonical) | `published` | `published_at` recorded; notifications fan out to every student in the mapped classes **and** each student's linked parents. |
| **Publish** | Manager (override) | `published` | Same. Only permitted on an **already-approved** event — a manager cannot publish straight from `proposed`. |

> *Result*: `status = published`. Parents/students in the targeted classes can now see and enroll.

### 5️⃣ Enrollment Flow
1. Parents/Students view published events (`GET /api/v1/events/published`).
2. Enrollment created in `requested_by_student` or `approved_by_parent` state.
3. Teacher (event head) approves (`approved_by_teacher`).

---

## 💻 9. CLI COMMAND EXECUTION
- When executing CLI commands, ensure they run non-interactively and exit immediately (e.g., use background flags `-d` or non-blocking parameters).

---

## 🌐 10. NETWORK ARCHITECTURE (DMZ & GATEWAY)
- **Nginx DMZ**: Outer edge proxy connecting to browser.
- **Apache APISIX**: Private internal Docker network API Gateway, routing traffic between Nginx and Python microservices.
