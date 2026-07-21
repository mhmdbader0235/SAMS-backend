---
trigger: always_on
---

🏫 SchoolDesk — AI Agent Project Rules & Constraints
🛑 CORE DIRECTIVE: RULE CHECKING & CONFLICT RESOLUTION
Before executing any prompt, generating code, or modifying files in this project, you MUST review this document.
If the user's request conflicts with any of these rules, architectural patterns, or security boundaries: You must STOP, refuse to proceed with the modification, clearly state the conflict, and ASK the user for clarification or permission to override.
Do not make assumptions. When in doubt, ask.
---
🏗️ 1. ARCHITECTURE & DESIGN PATTERNS
SchoolDesk operates on a strict layered backend architecture. You must not bypass these layers or mix their responsibilities.
Router Layer (FastAPI Gateway/Endpoints): Handles incoming HTTP requests, CORS, payload validation, and responses. No business logic or database calls here.
Service Layer (Python): Contains all business logic, calculations, and coordination.
Repository Layer (SQL/asyncpg): The ONLY layer permitted to interact with the database.
---
🗄️ 2. DATABASE & MULTI-TENANCY
The system uses PostgreSQL 16 deployed via Docker.
Strict Database-per-Tenant: Tenant data (school data) is strictly isolated at the database level. NEVER write queries, joins, or logic that attempt to cross-query between tenant databases.
Query Optimization: When writing or modifying repository methods, ensure SQL queries, indexing strategies, and execution plans remain highly optimized. These queries will be replicated and run across multiple individual tenant databases, so performance is critical.
Core Schema Constraints:
as i the user ask to do
---
👥 3. ROLE-BASED ACCESS CONTROL (RBAC)
Security and access control via JWT token claims are paramount. Never expose endpoints without verifying the required role guardrails.
Parent / Guardian: Can view notices, track calendar dates, and enroll students. Cannot create or modify core events.
Teacher / Staff: Can create, update, and delete events and notices.
---
💻 4. CODING STANDARDS & TECH STACK
Frontend: Vue 3, Vite, Tailwind CSS. Keep components modular and rely on Tailwind for styling.
Backend: FastAPI, Python, asyncpg.
Error Handling: Catch errors gracefully in the Service layer and bubble them up to the Router layer to return standardized HTTP responses. Do not leak database stack traces to the frontend.
Testing: Ensure backward compatibility with existing Pytest (backend) and Vitest/Cypress (frontend) suites.

🛡️ 5. SENSITIVE DATA & PII (Personally Identifiable Information)
Any table containing highly sensitive data (National IDs, medical conditions, financial data, or emergency contacts) must follow strict PII protocols:
* **Encryption at Rest:** Sensitive string columns must be encrypted at the Application Layer (Service Layer) using strong encryption (e.g., Python's `cryptography.fernet`) BEFORE inserting into the repository. 
* **Never Log PII:** Do not include sensitive data in server logs, print statements, or error messages.
* **Data Masking:** Pydantic response schemas must default to masking sensitive fields (e.g., `********89`) unless the requesting user has explicit `school_admin` rights and passes an elevated clearance check.
* **Audit Trails:** Any read or write to a sensitive table must generate a log entry tracking the `user_id`, `timestamp`, and `action`.
---
📝 AGENT ACKNOWLEDGEMENT
By reading this file, you agree to operate strictly within these boundaries. If requested to bypass the Repository layer, mix data between tenants, or ignore RBAC rules, you will immediately halt and request confirmation.
---
trigger: always_on
---

# Event Workflow & Resource System — Full Implementation Spec

Stack assumed: FastAPI, async SQLAlchemy 2.x + asyncpg, Alembic, PostgreSQL,
database-per-tenant, Router → Service → Repository layering, Vue frontend.

Give this whole file to the agent alongside your project rules.md. Section 8
is the literal task list — have the agent work through it top to bottom, one
numbered task at a time, and stop for your review after each one marked ⏸.

## 1. Enums

```python
# app/models/enums.py (or wherever your existing enums live)
class EventStatus(str, Enum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    FINANCE_APPROVAL = "finance_approval"
    FINAL_REVIEW = "final_review"
    PUBLISHED = "published"

class ResourceCategory(str, Enum):
    TRANSPORT = "transport"
    STAFFING = "staffing"
    MEALS = "meals"
    OTHER = "other"
```

Use a native Postgres enum (`sa.Enum(EventStatus, name="event_status")`) for
`EVENTS.status` so invalid values are rejected at the DB layer too, not just
in application code.

---

## 2. Schema — full DDL

### 2.1 `resource_types`
```sql
CREATE TABLE resource_types (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(120) NOT NULL,
    category        VARCHAR(30)  NOT NULL DEFAULT 'other',
    is_custom       BOOLEAN      NOT NULL DEFAULT false,
    created_by_user_id INTEGER  NULL REFERENCES users(id),
    is_active       BOOLEAN      NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
-- this table lives inside a single tenant's database (per your database-per-tenant
-- architecture), so there is no tenant_id column and no cross-tenant table to join
-- against here — everything in this table already belongs to that one school.
-- system types: is_custom = false, created_by_user_id = NULL
-- a teacher's custom type: is_custom = true, created_by_user_id = <teacher>
```
**Decided:** a custom type a teacher adds is available to every teacher in
that same school (i.e. every user of this tenant's database), reusable on
future events — not scoped to the one event it was created for. Because this
table only exists per tenant, that's automatic; no extra scoping column is
needed to achieve it.

Seed data (system types, `is_custom = false`):
```
('20-Seat Bus', 'transport'), ('40-Seat Bus', 'transport'),
('Male Supervisor', 'staffing'), ('Female Supervisor', 'staffing'),
('Kids Meal', 'meals'), ('Adult Meal', 'meals')
```
Ship these as an Alembic data migration, not app-code seeding, so they're
versioned and reproducible in a fresh environment. Since this is
database-per-tenant, that migration runs once per tenant database (through
whatever mechanism you already use to run migrations across all tenant DBs) —
it is not a single shared row set queried across tenants.

### 2.2 `resources` (replaces `cost_budget`)
```sql
CREATE TABLE resources (
    id                SERIAL PRIMARY KEY,
    event_id          INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    resource_type_id  INTEGER NOT NULL REFERENCES resource_types(id),
    description       TEXT NULL,
    quantity          INTEGER NOT NULL CHECK (quantity > 0),
    added_by_user_id  INTEGER NOT NULL REFERENCES users(id),
    updated_by_user_id INTEGER NULL REFERENCES users(id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_resources_event ON resources(event_id);
```
`updated_by_user_id` is null until someone other than the original teacher
edits the row (i.e. finance, during `finance_approval`) — lets the UI show
"edited by finance" on lines finance touched.

### 2.3 `resource_cost`
```sql
CREATE TABLE resource_cost (
    id              SERIAL PRIMARY KEY,
    resource_id     INTEGER NOT NULL UNIQUE REFERENCES resources(id) ON DELETE CASCADE,
    unit_price      NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    total_cost      NUMERIC(12,2) NOT NULL,   -- unit_price * resources.quantity, set in service layer
    currency        VARCHAR(3) NOT NULL DEFAULT 'JOD',
    set_by_user_id  INTEGER NOT NULL REFERENCES users(id),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
`UNIQUE` on `resource_id` — one price row per resource line, updated in place
rather than versioned. If you want a price-change history later, that's a
separate `resource_cost_history` table; don't build it now unless you need it.

### 2.4 `events` — new columns
```sql
ALTER TABLE events
    ADD COLUMN status EVENT_STATUS NOT NULL DEFAULT 'draft',
    ADD COLUMN predicted_attendance INTEGER NULL,
    ADD COLUMN manager_reviewer_id INTEGER NULL REFERENCES users(id),
    ADD COLUMN finance_reviewer_id INTEGER NULL REFERENCES users(id),
    ADD COLUMN total_cost NUMERIC(12,2) NULL,
    ADD COLUMN submitted_at TIMESTAMPTZ NULL,
    ADD COLUMN manager_approved_at TIMESTAMPTZ NULL,
    ADD COLUMN finance_priced_at TIMESTAMPTZ NULL,
    ADD COLUMN published_at TIMESTAMPTZ NULL;
CREATE INDEX ix_events_status ON events(status);
```

### 2.5 Roles
Add `manager` and `finance` as rows in your existing roles table, and grant
via your existing `user_roles` many-to-many join — do not build a parallel
mechanism.

---

## 3. State Machine

| From | Action | Actor role | To | Preconditions | Side effects |
|---|---|---|---|---|---|
| — | `save_draft` | teacher | `draft` | title/desc/address/date present | none |
| `draft` | `submit_for_approval` | teacher (owner) | `proposed` | ≥1 class selected, ≥1 resource line exists | set `submitted_at`; notify all `manager` role users in tenant |
| `proposed` | `manager_approve` | manager | `finance_approval` | — | set `manager_approved_at`, `manager_reviewer_id`; notify `finance` role users |
| `proposed` | `manager_reject` | manager | `draft` | rejection reason required | notify owning teacher with reason |
| `finance_approval` | `finance_submit` | finance | `final_review` | every resource row has a matching `resource_cost` row | set `finance_priced_at`, `finance_reviewer_id`, compute `events.total_cost`; notify manager |
| `final_review` | `manager_publish` | manager | `published` | — | set `published_at`; notify parents/students of targeted classes |
| `final_review` | `manager_return_to_finance` | manager | `finance_approval` | reason required | notify finance |

**Decided:** both `manager_reject` and `manager_return_to_finance` require a
non-empty `reason` string in the request body — enforce with a Pydantic
validator, not just a frontend "required" attribute.

**Decided:** finance can edit `resources` rows (quantity and resource type)
while the event is in `finance_approval`, not only `resource_cost`. This means
`resources` needs its own `updated_by_user_id` / `updated_at` so you can tell
whether the teacher's original line or finance's edit is what's shown — add
those two columns to `resources` in section 2.2's DDL. The permission matrix
in section 4 is updated accordingly.

Implement as **one function**, not per-router conditionals:
```python
async def transition_event(
    session, event: Event, action: str, actor: User, reason: str | None = None
) -> Event:
    # 1. look up the allowed (current_status, action) -> (next_status, required_role) in a table/dict
    # 2. verify actor has required_role for the tenant
    # 3. verify action-specific preconditions (see table above)
    # 4. apply side effects (timestamps, reviewer ids, notifications)
    # 5. persist and return
```
Keep the transition table as data (a dict or small table), not a chain of
`if event.status == ...` — makes it trivial to add a transition later without
touching unrelated code.

**Two things you still need to decide before this can be built exactly right:**
- Should `manager_reject` and `manager_return_to_finance` require a reason
  field? (Recommended: yes — a teacher/finance user getting bounced back with
  no explanation is a support ticket waiting to happen.)
- Can finance edit `resources` rows themselves (change quantity/type), or only
  ever write to `resource_cost`? This determines whether `resources` needs an
  `updated_by_user_id` beyond the original teacher.

---

## 4. Permission Matrix (enforce in the service layer, not just UI)

| Role | draft | proposed | finance_approval | final_review | published |
|---|---|---|---|---|---|
| Teacher (owner) | read/write own | read-only | read-only | read-only | read-only |
| Teacher (not owner) | no access | no access | no access | no access | read-only (published fields) |
| Manager | no access | read + approve/reject | read | read + publish/return | read |
| Finance | no access | no access | read + write `resources` and `resource_cost` | read | read |

**Decided:** once the teacher submits (`draft` → `proposed`), they can only
view the event for the rest of its lifecycle — no edit access at any later
state, even if it bounces back to `draft` via `manager_reject` they regain
edit rights only because the status is `draft` again, not because they're the
owner in general.
| Parent/Student | no access | no access | no access | no access | read: title, description, address, date, `total_cost` only |

Write a single `check_event_permission(user, event, action)` helper used by
every router — don't duplicate role checks per endpoint.

---

## 5. API Endpoints

| Method | Path | Role | Body | Notes |
|---|---|---|---|---|
| POST | `/events` | teacher | title, description, address, date | creates in `draft` |
| PATCH | `/events/{id}` | teacher (owner, draft only) | any of the basics | rejected if status != draft |
| POST | `/events/{id}/audience` | teacher (owner, draft) | `class_ids: [int]` | writes junction rows, returns `predicted_attendance` |
| GET | `/events/{id}/audience/prediction` | teacher | — | recompute preview before saving, if you want live feedback as they pick classes |
| GET | `/resource-types` | any authenticated | `?category=` | list active system + tenant-custom types |
| POST | `/resource-types` | teacher | name, category | creates custom type, `is_custom=true`; no tenant_id needed — the row is written into this tenant's own database |
| POST | `/events/{id}/resources` | teacher (owner, draft) | `[{resource_type_id, description, quantity}]` | full replace: deletes existing rows for the event, inserts the array sent |
| GET | `/events/{id}/resources` | role-gated per matrix | — | |
| POST | `/events/{id}/submit` | teacher (owner) | — | triggers `submit_for_approval` |
| POST | `/events/{id}/manager-decision` | manager | `{decision: approve|reject, reason?}` | triggers `manager_approve` or `manager_reject` |
| PATCH | `/resources/{id}` | finance (event in `finance_approval` only) | any of `resource_type_id, description, quantity` | sets `updated_by_user_id`/`updated_at`; recomputes `resource_cost.total_cost` if quantity changes and a price already exists |
| PUT | `/resources/{id}/cost` | finance | `{unit_price, currency}` | writes/updates `resource_cost`, recomputes `total_cost` |
| POST | `/events/{id}/finance-submit` | finance | — | triggers `finance_submit`; blocks if any resource missing a price |
| POST | `/events/{id}/final-decision` | manager | `{decision: publish|return_to_finance, reason?}` | triggers `manager_publish` or `manager_return_to_finance` |
| GET | `/events/manager-queue` | manager | `?status=proposed,final_review` | dashboard section |
| GET | `/events/finance-queue` | finance | `?status=finance_approval` | dashboard section |
| GET | `/events/published` | parent/student | — | filtered fields only, scoped to their classes |

there part 2 of this file in same folder
---
trigger: always_on
---

there part 1 before this file in same folder


## 6. Pydantic Schemas (sketch — expand field validators as needed)

```python
class EventCreate(BaseModel):
    title: str
    description: str
    address: str
    date: datetime

class AudienceSelect(BaseModel):
    class_ids: list[int]

class ResourceLineIn(BaseModel):
    resource_type_id: int
    description: str | None = None
    quantity: int = Field(gt=0)

class ResourceCostIn(BaseModel):
    unit_price: Decimal = Field(ge=0)
    currency: str = "JOD"

class ManagerDecision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = None

class PublishedEventOut(BaseModel):
    title: str
    description: str
    address: str
    date: datetime
    total_cost: Decimal
```

---

## 7. Vue Frontend

### 7.1 Teacher wizard — component tree
```
EventWizard.vue                 (holds current step + shared event draft state)
├── StepBasics.vue               title/description/address/date form
├── StepAudience.vue              class multi-select + predicted attendance display
├── StepResources.vue             checkbox grid (grouped by category) + qty inputs + "add custom type" modal
├── StepProposalReview.vue        read-only summary of all above + Save Draft / Send for Approval buttons
└── WizardStepper.vue             progress indicator, shared by all steps
```
Since you're still building Vue fundamentals: keep the draft event object in
a single reactive store (Pinia, if you've reached that in your learning path)
so each step component just reads/writes slices of it, rather than passing
props down four levels and emitting events back up.

### 7.2 Manager dashboard
```
ManagerDashboard.vue
├── ProposedQueue.vue      list of events in `proposed`, approve/reject actions
└── FinalReviewQueue.vue   list of events in `final_review`, publish/return actions
```

### 7.3 Finance dashboard
```
FinanceDashboard.vue
└── PricingQueue.vue       list of events in `finance_approval`
    └── ResourcePricingTable.vue   per-resource unit_price input, running total, "Send to Manager" button
```

### 7.4 Parent/Student view
```
PublishedEventCard.vue   title, description, address, date, total_cost only
```

---

## 8. Task List (execute in order; ⏸ = stop for review before continuing)

1. Write Alembic migration: create `resource_types`, `resources`, `resource_cost`; alter `events` with new columns and the `event_status` enum. Do not drop `cost_budget` yet.
2. Data-migrate the seed `resource_types` rows in the same migration.
3. Add `manager` and `finance` roles via existing role seeding mechanism. ⏸
4. Build repository classes for `resource_types`, `resources`, `resource_cost` following your existing repository pattern.
5. Build service-layer functions: `create_resource_type`, `add_resources_to_event`, `set_resource_cost`, `get_resource_summary(event_id)` (returns lines + total).
6. Implement `transition_event()` as the single state-machine function described in section 3, with the transition table as data.
7. Implement `check_event_permission()` as the single permission-check function described in section 4. ⏸
8. Build API endpoints from section 5, wiring each one through `check_event_permission` and, where relevant, `transition_event`.
9. Write the attendance-prediction calculation (decide the formula per the open question in section 3/original spec, then implement as a single service function, not inline in the router).
10. Write tests for the state machine: every legal transition succeeds, every illegal one (wrong role, wrong current state) is rejected. ⏸
11. Scaffold `EventWizard.vue` and the four step components with a shared store for the in-progress event.
12. Wire `StepResources.vue` to `GET /resource-types` and the "add custom type" flow to `POST /resource-types`.
13. Wire `StepProposalReview.vue`'s two buttons to `save_draft` (no-op if already draft) and `submit_for_approval`.
14. Build `ManagerDashboard.vue` and its two queues, wired to `manager-decision` / `final-decision` endpoints.
15. Build `FinanceDashboard.vue` and `ResourcePricingTable.vue`, wired to `PUT /resources/{id}/cost` and `finance-submit`.
16. Build `PublishedEventCard.vue` and the published-events list for parents/students.
17. Once everything above is verified working end to end, write a follow-up migration to drop `cost_budget`. ⏸

---

## 9. Decisions (resolved)
- Reject/return reason: **required** — enforced as a non-empty field in `ManagerDecision`/the return-to-finance schema, not just a frontend prompt.
- Finance **can** edit `resources` rows (quantity/type), not only `resource_cost` — see section 2.2/3/4.
- Teacher-added custom resource types are available to the whole school automatically, since `resource_types` lives inside that tenant's own database — no `tenant_id` scoping column needed. See section 2.1.
- Attendance prediction formula: **80% of the total count of students across the classes selected for the event** (i.e. `0.8 * sum(enrollment count per selected class)`), not a historical-attendance-based estimate. Implement as a single service function per section 3/task 9 — it only needs `ENROLLMENTS`/`CLASSES`, not `ATTENDANCE`.
- Teacher edit access: **read-only from the moment they submit** (`draft` → `proposed`) for the rest of the event's lifecycle, per section 4 — they only regain write access if the event returns to `draft`, and even then only because of the status, not a standing owner privilege.
- `POST /events/{id}/resources` semantics: **full replace**, not upsert. Each call deletes the event's existing `resources` rows (only reachable while `draft`, so nothing finance touched can be in scope) and inserts the array sent in the request. This matches how a single-page-per-step wizard naturally works — the client always holds and sends the complete current selection, so a diffing upsert would add complexity for no real benefit here. If you later want per-line history (e.g. "teacher removed the bus, then re-added it"), that's a separate audit-log concern, not a reason to change this endpoint's contract.


