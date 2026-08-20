# 🚀 Postman API Testing & Architectural Tracing Guide

This guide walks you through importing the ready-to-use **SchoolDesk Postman Collection** and executing end-to-end API calls to trace the backend architecture, Keycloak RBAC governance, and multi-tenant PostgreSQL isolated schemas.

---

## 📥 1. How to Import into Postman

1. Open **Postman**.
2. Click **Import** (top left).
3. Drag & drop or select the collection file:
   [`back/docs/SchoolDesk_API_Postman_Collection.json`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/docs/SchoolDesk_API_Postman_Collection.json)
4. Click **Import**. You will see the **SchoolDesk Enterprise API Collection** with 4 organized folders in your Postman sidebar.

---

## 🔑 2. Environment Variables & Automatic JWT Token Saving

The collection comes with built-in Postman test scripts that **automatically extract and save your JWT access tokens** upon registration or login!

| Variable Name | Default Value | Description |
| :--- | :--- | :--- |
| `baseUrl` | `http://localhost:9080` | Nginx reverse proxy edge gateway URL |
| `tenantId` | `tenant_a` | Active PostgreSQL school schema |
| `invitePassphrase` | `regester123` | Passphrase challenge required for registration |
| `teacherToken` | *(Auto-saved)* | Bearer JWT for Teacher requests |
| `managerToken` | *(Auto-saved)* | Bearer JWT for Manager requests |
| `parentToken` | *(Auto-saved)* | Bearer JWT for Parent requests |
| `studentToken` | *(Auto-saved)* | Bearer JWT for Student requests |
| `eventId` | *(Auto-saved)* | ID of created event draft |

---

## 🧪 3. Step-by-Step API Execution Order

Follow these steps in order to trace the complete event planning & student enrollment lifecycle:

### 📁 Folder 1: Auth & Identity
1. **`GET /api/v1/auth/tenants`**
   - Returns active tenant IDs: `["tenant_a", "tenant_b", "tenant_c"]`.
2. **`POST /api/v1/auth/register` (Teacher)**
   - Body: `{"email": "teacher_postman@school.com", "password": "teacher123", "role": "teacher", "tenant_id": "tenant_a", "invite_code": "regester123"}`
   - **Postman Script**: Automatically saves `access_token` into `{{teacherToken}}`.
3. **`GET /api/v1/auth/me`**
   - Headers: `Authorization: Bearer {{teacherToken}}`
   - Verifies your role (`teacher`), user ID, and tenant assignment (`tenant_a`).

---

### 📁 Folder 2: Academic Structure & Classes
1. **`POST /api/v1/students/levels`**
   - Create grade level (e.g., `"Grade 7"`).
2. **`POST /api/v1/students/classes`**
   - Create class (e.g., `"Class 7A"` mapped to `level_id: 1`).

---

### 📁 Folder 3: Event Planning & Governance Lifecycle
1. **`POST /api/v1/events` (1. Create Event Draft)**
   - Teacher creates a new trip proposal. Returns `id` (saved to `{{eventId}}`).
   - Event status: `draft`.
2. **`POST /api/v1/events/{{eventId}}/audience` (2. Set Target Audience)**
   - Maps event to `Class 7A` with suggested ticket price (`12.50 JOD`).
   - Calculates predicted attendance (`0.8 × total students`).
3. **`POST /api/v1/events/{{eventId}}/resources` (3. Add Requested Resources)**
   - Adds resource lines (transport, meals, staffing) with estimated unit pricing.
4. **`POST /api/v1/events/{{eventId}}/submit` (4. Submit Draft for Manager Review)**
   - Transitions event from `draft` ➔ `proposed`.
   - Event is now read-only for the teacher.
5. **`POST /api/v1/events/{{eventId}}/manager-decision` (5. Manager Decision)**
   - Manager reviews the proposal and sends `{"decision": "approve"}`.
   - Transitions event from `proposed` ➔ `published`.

---

### 📁 Folder 4: Student & Parent Enrollment Flow
1. **`GET /api/v1/events/published`**
   - Scoped to target class: returns the published trip for students/parents in Class 7A.
2. **`POST /api/v1/students/enrollments`**
   - Student requests enrollment (state: `requested_by_student`).
3. **`POST /api/v1/students/enrollments/{{enrollmentId}}/approve`**
   - Parent approves child enrollment (state: `approved_by_parent`).
4. **`POST /api/v1/events/enrollments/{{enrollmentId}}/pay`**
   - Pay trip invoice (state: `paid`).

---

## 🏛️ 4. Backend Clean Architecture Layers to Trace

While making these Postman calls, observe how requests flow through the backend codebase:

```
HTTP Request (Postman)
     │
     ▼
┌─────────────────────────┐
│ 1. Router Layer         │  app/domains/<domain>/router.py
│ (HTTP & Pydantic parse) │  Extracts Bearer JWT, validates request DTOs
└────────────┬────────────┘
             │ calls
             ▼
┌─────────────────────────┐
│ 2. Service Layer        │  app/domains/<domain>/service.py
│ (Business Rules & RBAC) │  Validates status state machine transitions & Keycloak RBAC
└────────────┬────────────┘
             │ calls
             ▼
┌─────────────────────────┐
│ 3. Repository Layer     │  app/domains/tenant/tenant_repository.py
│ (SQL & Database Pool)   │  Executes parameterized SQL queries against target tenant schema
└─────────────────────────┘
```

---

## 🎯 Verification Checklist

- [x] All 4 Postman collection folders loaded cleanly.
- [x] Test scripts automatically populate `teacherToken`, `managerToken`, `parentToken`, and `studentToken`.
- [x] State machine transitions verified (`draft` ➔ `proposed` ➔ `published`).
