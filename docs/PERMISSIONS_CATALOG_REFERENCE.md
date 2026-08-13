# 🛡️ Enterprise Roles & Fine-Grained Permissions Catalog

This document serves as the master architectural reference and catalog for all high-level **Composite Roles** and **Granular Permission Objects** supported by **SchoolDesk**.

All permission objects defined here are enforced by:
1. **Open Policy Agent (OPA - Sole AuthZ Engine)**: Defined in [`policies/school_policy.rego`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/policies/school_policy.rego).
2. **Keycloak OIDC (AuthN ONLY)**: Issues JWT tokens containing user identity and assigned role claims.
3. **Apache APISIX JWT Token Claims & FastAPI Backend Service Layer Guard**.

---

## 📁 Files Included

- **OPA Authorization Policy**: [`policies/school_policy.rego`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/policies/school_policy.rego) — Centralized Rego policy engine executing all authorization and state machine decisions.
- **Backend OPA Client**: [`app/core/opa.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/core/opa.py) — Async OPA client for service-layer access checks.
- **JSON Role Catalog**: [`docs/keycloak_roles_catalog.json`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/docs/keycloak_roles_catalog.json) — Catalog for automated seed scripts.
- **Backend Context Enforcer**: [`app/core/dependencies.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/core/dependencies.py) — Extracts authenticated user context and tenant bindings.


---

## 🎭 1. Composite High-Level Roles

High-level roles represent traditional job functions. Assigning a composite role to a user automatically grants them all associated granular permissions.

| Composite Role | Target Persona | Key Granted Permissions |
| :--- | :--- | :--- |
| **`super_admin`** | Platform Admin | Unrestricted access across all tenant schemas (`*`). |
| **`school_admin`** | Principal / School Admin | `school:*`, `user:*`, `event:*`, `enrollment:cancel`, `billing:audit`, `announcement:manage` |
| **`manager`** | Operations Manager | `event:review`, `event:publish`, `event:view_draft`, `resource:price`, `billing:*`, `enrollment:view_roster` |
| **`teacher`** | Class Teacher / Trip Head | `event:create`, `event:edit`, `event:delete`, `event:propose`, `event:clone`, `resource:create`, `enrollment:teacher_approve`, `enrollment:view_roster` |
| **`event_teacher`** | Assigned Trip Leader | `event:edit`, `event:propose`, `resource:create`, `enrollment:teacher_approve`, `enrollment:view_roster` |
| **`finance`** | School Bursar / Accountant | `resource:price`, `billing:invoice`, `billing:pay`, `billing:refund`, `billing:audit`, `subsidy:manage` |
| **`parent`** | Parent / Guardian | `enrollment:parent_approve`, `enrollment:cancel`, `billing:pay`, `student:view_linked`, `health:manage_child` |
| **`student`** | Enrolled Student | `enrollment:request`, `feedback:create`, `school:read` |
| **`school_nurse`** | Medical Officer | `health:view`, `health:manage`, `safety:manage`, `enrollment:view_roster` |
| **`auditor`** | External Compliance Auditor | Read-only access to `billing:audit`, `audit:view`, `event:view_draft`, `resource:view` |

---

## 🎯 2. Granular Permissions Catalog (By Category)

Granular permissions can be assigned individually to any Keycloak user to grant specific capabilities without upgrading their high-level role.

### 1️⃣ Event Management (`event:*`)
- `event:create`: Create new event drafts.
- `event:edit`: Update details of draft events.
- `event:delete`: Delete draft or proposed events.
- `event:propose`: Submit draft event for manager review.
- `event:review`: Approve or reject proposed events.
- `event:publish`: Publish approved events to students and parents.
- `event:clone`: Duplicate existing events as template drafts.
- `event:view_draft`: View draft events in review queues.
- `event:archive`: Archive completed historical events.

### 2️⃣ Resource & Logistics (`resource:*`)
- `resource:create`: Define and allocate required resource lines for events.
- `resource:price`: Set unit prices, supplier estimates, and pricing lines.
- `resource:view`: View allocated resources and cost summaries.
- `resource:manage_types`: Create and manage custom resource categories (transport, staffing, meals).

### 3️⃣ User & Identity (`user:*`, `teacher:*`)
- `user:invite`: Create pre-provisioned user invitations.
- `user:delete`: Remove or deactivate user accounts.
- `user:link`: Link parent accounts with eligible student profiles.
- `user:view`: View directory of staff, students, and parents.
- `teacher:write`: Create and update teacher profiles.
- `teacher:read`: View teacher directories and class assignments.

### 4️⃣ Academic & School Structure (`school:*`, `class:*`, `level:*`)
- `school:write`: Modify school organization settings, levels, and classes.
- `school:read`: Read-only access to basic school metadata.
- `class:create`: Create new class sections (e.g. Class 7A).
- `class:assign_teacher`: Assign head teachers to specific class sections.
- `level:manage`: Manage grade levels (e.g. Grade 1, Grade 7).

### 5️⃣ Enrollment & Roster (`enrollment:*`)
- `enrollment:request`: Submit a request to join a published trip.
- `enrollment:parent_approve`: Parent consent and approval for child participation.
- `enrollment:teacher_approve`: Final teacher/staff approval of student roster lines.
- `enrollment:cancel`: Cancel an active enrollment before event execution.
- `enrollment:view_roster`: Access full attendee roster and emergency contact details.

### 6️⃣ Finance, Billing & Subsidies (`billing:*`, `subsidy:*`)
- `billing:invoice`: Generate fee invoices for participating families.
- `billing:pay`: Process electronic payments for trip fees.
- `billing:refund`: Issue refunds for cancelled trips or overpayments.
- `billing:audit`: Audit trip budgets, subsidies, and overall revenue logs.
- `subsidy:manage`: Configure school subsidies reducing per-student ticket prices.

### 7️⃣ Communication & Announcements (`announcement:*`, `feedback:*`)
- `announcement:manage`: Create and publish school-wide or trip notifications.
- `notification:send`: Send direct email/push alerts to targeted classes.
- `feedback:create`: Submit event ratings and feedback.
- `feedback:view_all`: View feedback metrics and survey results.

### 8️⃣ Student Welfare & Health (`health:*`, `safety:*`)
- `health:view`: View confidential student health and allergy records.
- `health:manage`: Update medical conditions and emergency action plans.
- `health:manage_child`: Parent upload/update of child's medical info.
- `safety:manage`: Define trip safety protocols, ratios, and risk assessments.

### 9️⃣ System & Tenant Administration (`system:*`, `tenant:*`, `audit:*`)
- `system:write`: Configure platform system settings and gateway policies.
- `system:read`: View system diagnostics and performance metrics.
- `tenant:manage`: Provision new school tenant schemas and databases.
- `tenant:view`: List registered school tenant organizations.
- `audit:view`: Access immutable system audit logs.

---

## 🚀 How to Use

### 1. Keycloak Import
You can import [`docs/keycloak_roles_catalog.json`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/docs/keycloak_roles_catalog.json) directly into Keycloak Admin Console:
1. Open Keycloak Console ➔ Realm Settings ➔ Roles.
2. Add Realm Role or import role definitions.

### 2. FastAPI Endpoint Enforcement Example
```python
@router.post("/events")
async def create_event(
    payload: EventCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    # Automatically permits teachers, school_admins, or ANY user with 'event:create' granular Keycloak role!
    if not (current_user.has_any_role("school_admin", "teacher") or current_user.has_role("event:create")):
        raise HTTPException(status_code=403, detail="Forbidden")
```
