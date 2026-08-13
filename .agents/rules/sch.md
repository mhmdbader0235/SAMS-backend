# Project Architectural Rules & Guidelines — SchoolDesk (Doumind)

## 🛑 CORE DIRECTIVE & CONFLICT RESOLUTION
Before executing any prompt, generating code, or modifying files:
1. Review the rules and constraints below.
2. If any user request conflicts with these architectural boundaries, multi-tenant isolation rules, or security guidelines: **STOP, refuse execution, state the conflict clearly, and ask for permission/clarification.**

---

## 🏛️ 1. LAYERED BACKEND ARCHITECTURE
The system enforces a strict 3-tier backend design (`FastAPI` + `Python` + `asyncpg`). You MUST NOT bypass layers or mix responsibilities:

1. **Router Layer (`app/api/` / `app/domains/*/router.py`):**
   - Handles incoming HTTP requests, headers, CORS, and Pydantic validation.
   - Extracts JWT Bearer claims (`user_id`, `tenant_id`, `roles`).
   - **STRICT RULE:** NO business logic, calculations, or database queries in the Router.

2. **Service Layer (`app/services/` / `app/domains/*/service.py`):**
   - Contains all business rules, lifecycle state machines, and workflow calculations.
   - Responsible for calling the **OPA client** (`app/core/opa.py`) to verify action authorization before mutating data.
   - Encrypts sensitive PII payload fields (`cryptography.fernet`) before database dispatch.

3. **Repository Layer (`app/repositories/` / `app/domains/*/*repository.py`):**
   - **STRICT RULE:** The ONLY layer permitted to execute raw SQL / `asyncpg` queries against PostgreSQL.
   - Responsible for setting dynamic tenant contexts (e.g., `SET search_path TO "tenant_x"`).

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

### ⚙️ Permissions-to-Role Mapping Matrix

| High-Level Role | Granular Role Permissions | Mapped Functions |
|----------------|---------------------------|-------------------|
| **`super_admin`** | Unrestricted access across all schemas (`*`) | Global platform administration, tenant provisioning, system diagnostics, and audit logs. Automatically bypasses policy checks (`allow = true`). |
| **`school_admin`** | `school:*`, `level:*`, `class:*`, `user:*`, `teacher:*`, `student:*`, `event:*`, `resource:*`, `enrollment:*`, `billing:audit`, `billing:invoice`, `subsidy:manage`, `health:*`, `safety:manage`, `announcement:manage`, `audit:view` | Full school tenant administration: manage levels, classes, staff users, invitations, event oversight, subsidies, and student medical/safety plans. |
| **`manager`** | `school:read`, `level:read`, `class:read`, `teacher:read`, `parent:read`, `student:read`, `user:view`, `event:read`, `event:view`, `event:review`, `event:publish`, `event:view_draft`, `event:audience_predict`, `resource:view`, `resource:price`, `resource:set_cost`, `resource_type:read`, `billing:invoice`, `billing:pay`, `billing:refund`, `billing:audit`, `billing:view_payment`, `subsidy:manage`, `enrollment:view_roster`, `enrollment:read`, `announcement:manage`, `notification:send`, `feedback:view` | Operations and event review: approve/reject proposed events, establish resource costs and pricing, issue invoices/refunds, and audit budgets. |
| **`teacher`** | `school:read`, `level:read`, `class:read`, `teacher:read`, `student:read`, `user:view`, `event:create`, `event:read`, `event:view`, `event:edit`, `event:patch`, `event:delete`, `event:clone`, `event:propose`, `event:submit`, `event:view_draft`, `event:audience_edit`, `event:audience_predict`, `resource:create`, `resource:view`, `resource:edit`, `resource:update`, `resource:delete`, `resource_type:create`, `resource_type:read`, `enrollment:teacher_approve`, `enrollment:view_roster`, `enrollment:read`, `health:view`, `notification:read`, `feedback:view`, `feedback:create` | Class teacher & trip lead: create event drafts, allocate resources, submit for manager approval, approve student enrollments, and view attendee health info. |
| **`parent`** | `school:read`, `user:profile_read`, `user:profile_edit`, `student:view_linked`, `event:read`, `event:view`, `enrollment:parent_approve`, `enrollment:cancel`, `enrollment:read`, `billing:pay`, `billing:view_payment`, `health:manage_child`, `notification:read`, `feedback:create` | Parent/guardian: view published trips for child's class, approve/enroll children, cancel enrollments, pay trip invoices, update child health info, and leave feedback. |
| **`student`** | `school:read`, `user:profile_read`, `user:profile_edit`, `event:read`, `event:view`, `enrollment:request`, `enrollment:read`, `notification:read`, `feedback:create` | Student: browse published trips for their class, submit enrollment requests, view notifications, and leave feedback. |

### Event Lifecycle State Machine:
`draft` ➔ `proposed` (Manager Review) ➔ `published` (Manager Publishes)

- **Teacher Rule:** Can edit or delete an event ONLY IF `resource.status == "draft"`.
- **Manager Rule:** Can review/publish an event ONLY IF `resource.status == "proposed"`.
- **Parent/Student Rule:** Can view an event ONLY IF `resource.status == "published"` AND `resource.class_id` matches child/student class mapping.

---

## 🛡️ 5. SENSITIVE DATA & PII PROTOCOLS
For tables containing sensitive records (National IDs, emergency contacts, medical records, financial data):

1. **Encryption at Rest:** Application-layer encryption using `cryptography.fernet` in the **Service Layer** BEFORE passing payload to the Repository.
2. **Data Masking:** Pydantic response models MUST mask fields by default (e.g., `********89`) unless explicitly requested by an authorized `school_admin`.
3. **Zero PII Logging:** Never write PII parameters to application logs, standard output, or OPA input payloads.
4. **Audit Logs:** Every read/write operation on sensitive tables must trigger an immutable audit log capturing `(user_id, tenant_id, timestamp, action, resource_id)`.

---

## 💻 6. FRONTEND STANDARDS (Vue 3 + Vite + Tailwind)
- **Audience Scope & Class Filtering:** Published events MUST be strictly scoped to their target classes (`WHERE e.status = 'published' AND ecm.class_id = $1`). Events mapped to a specific class (e.g. Class 7A) must **never** appear for students or parents belonging to other classes (e.g. Class 7B).
- **Multi-Child Enrollment Support:** Parents linked to children in different classes must be able to enroll each eligible child into events targeting their specific class.
- **Direct Action Buttons:** In `PublishedEventCard.vue`, render clear, direct `Enroll [Child Name]` buttons for unenrolled linked children instead of generic text inputs or multi-select dropdowns.
- **Optimistic UI Updates (0ms Latency):** Click handlers for enrollment, approval, and cancellation MUST optimistically mutate local state immediately (0ms delay) so buttons and badges update instantly on click, syncing with the API in the background.
- **Visual Aesthetics & Dark Mode:**
  - Dark mode heading text color is `--color-text-heading: #F1F1F9` (off-white Slate).
  - Card/Badge fallbacks use dark slate tones (`bg-slate-800 text-slate-400 border-gray-700`) instead of light grey (`bg-gray-100`).
- **Sticky Actions:** Action bar footers in wizard and details views must remain sticky at the bottom (`sticky -bottom-8`) while top page headers scroll away naturally.
- **Authentication Passphrase Challenge:** Registration form is protected and hidden by default until the user enters the invite passphrase (`regester123`). Inputs must start empty (`""`).

---

## 📝 7. EVENT PLANNING & LIFECYCLE OVERVIEW

### 1️⃣ Create a Draft (Teacher)
1. **Open the Event Wizard** → *Step 1 – Basics* (title, description, address, date, school-subsidy).
2. **Step 2 – Audience** (select classes; predicted attendance = 0.8 × total students).
3. **Step 3 – Resources** (choose resource types: transport, staffing, meals, custom; set quantity).
4. **Step 4 – Review** (verify data, click Save Draft or Send for Approval).

> *Result*: An `events` row created with `status = draft`. Cannot be edited once status leaves `draft`.

### 2️⃣ Submit for Manager Review
- **Teacher** clicks **“Send for Approval”** → backend transition `draft → proposed`. `submitted_at` set; notification sent to tenant Managers.

> *Result*: `status = proposed`. Teacher UI shows event as read-only.

### 3️⃣ Manager Approval / Rejection
| Action | Actor | New Status | Side-effects |
|--------|-------|------------|--------------|
| **Publish** | Manager | `published` | `manager_approved_at` & `published_at` recorded; notification broadcast. |
| **Reject** | Manager | `draft` | Requires non-empty reason; teacher receives notification and regains edit rights. |

### 4️⃣ Enrollment Flow
1. Parents/Students view published events (`GET /api/v1/events/published`).
2. Enrollment created in `requested_by_student` or `approved_by_parent` state.
3. Teacher (event head) approves (`approved_by_teacher`).

---

## 💻 8. CLI COMMAND EXECUTION
- When executing CLI commands, ensure they run non-interactively and exit immediately (e.g., use background flags `-d` or non-blocking parameters).

---

## 🌐 9. NETWORK ARCHITECTURE (DMZ & GATEWAY)
- **Nginx DMZ**: Outer edge proxy connecting to browser.
- **Apache APISIX**: Private internal Docker network API Gateway, routing traffic between Nginx and Python microservices.