# Project Architectural Rules & Guidelines

## 1. Audience & Class Target Filtering
- **Strict Audience Scope**: Published events MUST be strictly scoped to their target classes (`WHERE e.status = 'published' AND ecm.class_id = $1`).
- Published events mapped to a specific class (e.g. Class 7A) must **never** appear for students or parents belonging to other classes (e.g. Class 7B).

## 2. Parent Multi-Child Enrollment & UI Responsiveness
- **Multi-Child Class Support**: Parents linked to children in different classes must be able to enroll each eligible child into events targeting their specific class.
- **Direct Action Buttons**: In `PublishedEventCard.vue`, render clear, direct `Enroll [Child Name]` buttons for unenrolled linked children instead of generic text inputs or multi-select dropdowns.
- **Optimistic UI Updates (0ms Latency)**: Click handlers for enrollment, approval, and cancellation MUST optimistically mutate local state immediately (0ms delay) so buttons and badges update instantly on click, syncing with the API in the background.

## 3. Teacher Permissions & Workflow Lifecycle
- **Permissions**: The `teacher` role can create events, specify resource lines, and set estimated pricing during the `draft` status.
- **State Machine Flow**: `draft` ➔ `proposed` (Manager Review) ➔ `published` (Manager Publishes).
- **Sticky Footers**: Action bar footers in wizard and details views must remain sticky at the bottom (`sticky -bottom-8`) while top page headers scroll away naturally.

## 4. Authentication & Passphrase Challenge
- **Secret Passphrase Challenge**: Registration form is protected and hidden by default until the user enters the invite passphrase (`regester123`).
- **Clean Inputs**: Login & Register form inputs must start with empty strings (`""`) by default with no auto-filling demo credentials exposed.

## 5. Visual Aesthetics & Dark Mode Standards
- **Heading Colors**: Dark mode heading text color is `--color-text-heading: #F1F5F9` (off-white Slate, avoiding blinding `#FFFFFF`).
- **Badge & Card Fallbacks**: Fallback status badges and cards use dark slate tones (`bg-slate-800 text-slate-400 border-gray-700`) instead of light grey (`bg-gray-100`).

---

# Event Planning & Lifecycle – Overview

## 1️⃣ Create a Draft (Teacher)
1. **Open the Event Wizard** → *Step 1 – Basics*
   - Fill **title, description, address, date, school-subsidy**.
2. **Step 2 – Audience**
   - Select one or more **classes**.
   - System calculates **predicted attendance** = 0.8 × total students in the selected classes.
3. **Step 3 – Resources**
   - Choose required **resource types** (transport, staffing, meals, custom).
   - Set **quantity** for each line.
4. **Step 4 – Review**
   - Verify all data, click **Save Draft** (optional) or **Send for Approval**.

> *Result*: An `events` row is created with `status = draft`. All related data (class-mappings, resources) are stored but **cannot be edited** once the status leaves `draft`.

---

## 2️⃣ Submit for Manager Review
- **Teacher** clicks **“Send for Approval”** → backend `transition_event` runs `draft → proposed`.
- Side-effects:
  - `submitted_at` timestamp set.
  - Assigned **Managers** in the tenant receive a notification.

> *Result*: `status = proposed` (Manager review). The teacher’s UI shows the event as **read-only**.

---

## 3️⃣ Manager Approval / Rejection
| Action | Actor | New Status | Side-effects |
|--------|-------|------------|--------------|
| **Publish** | Manager | `published` | `manager_approved_at` & `published_at` recorded; parents & students receive a public notification with the event summary (title, description, address, date, total cost). |
| **Reject** | Manager | `draft` | Requires a **non-empty reason**; teacher receives a notification and regains edit rights. |

---

## 4️⃣ Enrollment Flow (Parents & Students)
1. **Parents/Students** view the **published events list** (`GET /api/v1/events/published`).
2. When a student or parent **requests enrollment**:
   - A **new enrollment** is created in `requested_by_student` or `approved_by_parent` state.
   - **Parent** can directly approve/enroll linked children.
3. After parent decision, the **teacher** (event head) sees the enrollment in the Audience / Roster view and approves (`approved_by_teacher`).

---

## 5️⃣ Full Cycle Overview (ASCII Flow)
```
┌─────────────┐
│ Teacher     │
│ (Create)    │
└─────┬───────┘
      │  Draft (save)
      ▼
┌─────────────┐   Submit → Manager
│ Draft       │─────────────────────────────────►┌─────────────────┐
│ (editable)  │                                  │ Proposed        │
└─────┬───────┘   ↑ Reject                       │ (read-only)     │
      │            │                             └─────┬───────────┘
      │            │                                   │ Approve / Publish
      ▼            │                                   ▼
┌─────────────┐   │                              ┌─────────────────┐
│ Teacher     │◄──┘                              │ Published Event │
│ (Edit again)│                                  │ (public)        │
└─────────────┘                                  └─────┬───────────┘
                                                       │
                                                       ▼
                                                 ┌─────────────────────┐
                                                 │ Parent/Student View │
                                                 │ (enroll)            │
                                                 └─────┬───────────────┘
                                                       │
                                                       ▼
                                                 ┌─────────────────────┐
                                                 │ Parent Approval     │
                                                 │ (accept / reject)   │
                                                 └─────────────────────┘
```

---

## 6️⃣ Class Data Requirements
- **Head Teachers Are Optional**: A class can be created without a `head_teacher_id`. The database schema (`init.sql`) allows `head_teacher_id` to be `NULL`, and the API handles it as an optional field. This allows for flexible class creation before a teacher is assigned.

---

## 7️⃣ Role & Permissions Workflow (Keycloak RBAC)

### ⚙️ Permissions-to-Role Mapping Matrix

| High-Level Role | Granular Role Permissions | Mapped Functions |
|----------------|---------------------------|-------------------|
| **`school_admin`** | `school:write`, `school:read`, `user:create`, `user:delete`, `user:link`, `user:view`, `event:review`, `event:publish`, `teacher:read`, `enrollment:cancel`, `enrollment:view_roster`, `billing:audit`, `announcement:manage` | Manage school structure, register staff, manage announcements. |
| **`manager`** | `school:read`, `event:review`, `event:publish`, `event:view_draft`, `resource:view`, `resource:price`, `billing:invoice`, `billing:pay`, `billing:refund`, `billing:audit`, `enrollment:view_roster` | Approve event drafts, set final pricing, audit student logs. |
| **`teacher`** | `school:read`, `user:view`, `event:create`, `event:edit`, `event:delete`, `event:propose`, `event:clone`, `teacher:write`, `teacher:read`, `resource:create`, `resource:view`, `enrollment:teacher_approve`, `enrollment:view_roster` | Create events, plan resources, approve enrollments. |
| **`parent`** | `school:read`, `enrollment:parent_approve`, `enrollment:cancel`, `billing:pay` | Approve child requests, pay trip invoices. |
| **`student`** | `school:read`, `enrollment:request` | Browse published trips, request enrollment. |

*Note: The `super_admin` role automatically bypasses all access validations and grants full control.*


