# 📖 Complete Codebase File & Folder Dictionary

> **Project Name**: SAMS / Doumind (Multi-Tenant School Administration System)  
> **Architecture**: Vue 3 (SPA) + FastAPI (Async Python) + PostgreSQL (Multi-Schema Isolation) + Keycloak 26 (OIDC & Organizations) + Nginx (DMZ) + Apache APISIX (API Gateway)

---

## 📂 1. Workspace Root Directory

| File / Folder Path | Description & Functional Purpose |
| :--- | :--- |
| [`docker-compose.yml`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/docker-compose.yml) | **Single Source of Truth Docker Orchestration File**. Defines `doumind-db` (PostgreSQL 16 on port 5433), `doumind-keycloak` (Keycloak 26 on port 8000), `doumind-apisix` (Apache APISIX on port 9180), `doumind-gateway` (Nginx DMZ on port 9080), and `doumind-opa` (Open Policy Agent on port 8181 loading `./policies`) with persistent named volumes (`keycloak_data`, `doumind-backend_pgdata`). |

| [`.agents/AGENTS.md`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/.agents/AGENTS.md) | **Architectural Rules & Event Lifecycle Guidelines**. Defines strict class filtering, multi-child parent UI rules, teacher event state machines (`draft` ➔ `proposed` ➔ `published`), optional head teacher schemas, and Keycloak RBAC mappings. |
| [`back/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back) | **FastAPI Async Backend Application & Database DDL Schemas**. |
| [`front/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/front) | **Vue 3 Vite Single Page Application (SPA)**. |

---

## 📂 2. Backend Infrastructure & Configuration (`back/`)

| File / Folder Path | Description & Functional Purpose |
| :--- | :--- |
| [`run.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/run.py) | **Local Development Launcher**. Automates Docker volume creation, boots Docker Compose containers, and runs the FastAPI Uvicorn dev server on `http://127.0.0.1:8001` with hot-reloading. |
| [`init.sql`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/init.sql) | **Master PostgreSQL DDL Schema Script**. Initializes control-plane tables (`tenants`, `parents`, `super_admins`, `user_tenant_map`, `user_invitations`) and isolated tenant schemas (`tenant_a`, `tenant_b`) with tables for `users`, `teachers`, `students`, `class`, `levels`, `event`, `resources`, `resource_cost`, `event_registrations`, and `invoices`. |
| [`SAMS-realm.json`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/SAMS-realm.json) | **Keycloak 26 Realm Export JSON**. Contains pre-configured realm roles, groups, clients (`frontend`, `apisix`), and Organizations (`tenant_a` with `schoolA.com`, `tenant_b` with `schoolB.com`). |
| [`seed_data.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/seed_data.py) | **Database & Keycloak Seeder Script**. Seeds ~85 users per tenant (`tenant_a`, `tenant_b`, `tenant_c` — teachers, students, parents, admin, manager) covering levels/classes, events across every lifecycle state with enrollments/payments/resources, school profile/campus/contact, and every `super_admin` in every tenant, syncing every account to Keycloak via `sync_user_to_keycloak()`. This is now the single seed script — the older `seed_tenant_b_like_a.py` (pure-SQL clone) and `sync_db_to_keycloak.py` (standalone reconciliation) have been removed; both are superseded by this one file. |
| [`scripts/live_data_migration.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/scripts/live_data_migration.py) | **One-off Live Data Migration Script**. Collapses the three unreachable `event_status` enum values (`resource_planning`, `finance_approval`, `final_review`) into `proposed`/`approved` across every tenant. Formerly `tests/test_migrate.py`, where it matched pytest's `test_*.py` collection and could be triggered by a plain `pytest tests/` run; moved to `scripts/` so it never collects. `--dry-run` is read-only; `--apply` requires `ALLOW_LIVE_MIGRATION=true` plus an interactive typed `CONFIRM` prompt. |
| [`Dockerfile`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/Dockerfile) | **Production Container Build Spec**. Multi-stage Python 3.11/3.14 build for running the FastAPI application via Uvicorn. |
| [`README.md`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/README.md) | **Backend Project Documentation**. Overview of the backend structure, setup instructions, and development guidelines. |
| [`pyproject.toml`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/pyproject.toml) | **Python Project Metadata & Dependencies**. Configures project dependencies (`fastapi`, `asyncpg`, `pydantic`, `pyjwt`, `passlib`, `cryptography`). |
| [`.env`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/.env) | **Backend Runtime Environment Variables**. Contains DB connection strings, JWT secret keys, and Keycloak URLs. |
| [`.env.example`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/.env.example) | **Environment Template Blueprint**. Template for environment variables with safe placeholder values for new developers and CI/CD pipelines. |

---

## 📂 3. Backend Gateway & Documentation (`back/gateway/` & `back/docs/`)

| File / Folder Path | Description & Functional Purpose |
| :--- | :--- |
| [`gateway/nginx.conf`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/gateway/nginx.conf) | **Production Nginx Reverse Proxy Configuration**. Handles port `9080` routing for `/` (Frontend SPA `:3000`), `/api/v1/` (FastAPI Backend `:8001`), and `/realms/` (Keycloak `:8080`). |
| [`docs/KEYCLOAK_WORKFLOW_GUIDE.md`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/docs/KEYCLOAK_WORKFLOW_GUIDE.md) | **Keycloak OIDC & Organization Technical Spec**. Details authentication flows, Organization member assignment, and JWT claims. |
| [`docs/NGINX_AND_APISIX_ARCHITECTURE.md`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/docs/NGINX_AND_APISIX_ARCHITECTURE.md) | **Reverse Proxy & Gateway Architecture Specification**. Documents single-gateway Nginx routing rules and port mappings. |
| [`docs/PERMISSIONS_CATALOG_REFERENCE.md`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/docs/PERMISSIONS_CATALOG_REFERENCE.md) | **RBAC Permissions Matrix Catalog**. Documents permissions for `super_admin`, `school_admin`, `manager`, `teacher`, `parent`, and `student`. |
| [`docs/SEEDED_DEMO_DATA.txt`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/docs/SEEDED_DEMO_DATA.txt) | **Seeded Demo Accounts Reference Sheet**. Contains login credentials (`All passwords: 123321`), class listings, and teacher IDs. |

---

## 📂 4. Backend Core Services (`back/app/core/`)

| File Path | Description & Functional Purpose |
| :--- | :--- |
| [`app/main.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/main.py) | **FastAPI Application Initialization**. Configures CORS middleware, connects database connection pools on startup, and registers API routers. |
| [`app/core/config.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/core/config.py) | **Central Configuration Loader**. Loads environment variables from `.env` (DB credentials, JWT settings, Keycloak URLs, Fernet encryption keys). |
| [`app/core/database.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/core/database.py) | **Multi-Tenant Async Database Connection Pool Engine**. Manages `asyncpg` connection pools per tenant (`tenant_a`, `tenant_b`) and control-plane DB. Automatically generates schemas for new tenants. |
| [`app/core/dependencies.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/core/dependencies.py) | **Security & Authorization Engine**. Validates Bearer JWTs, extracts Keycloak Organization claims, enforces `X-Tenant-ID` header, auto-provisions missing Keycloak users JIT in PostgreSQL, and constructs the `CurrentUser` object. |
| [`app/core/keycloak_admin.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/core/keycloak_admin.py) | **Keycloak Admin REST API Client**. Handles `sync_user_to_keycloak(...)` by provisioning accounts, updating user attributes (`tenant_id`, `role`), and mapping members to Keycloak Organizations (`/organizations/{org_id}/members`). |
| [`policies/school_policy.rego`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/policies/school_policy.rego) | **Open Policy Agent (OPA) Rego Authorization Policy**. Enforces role permission matrix, multi-tenant boundaries (`tenant_id`), and event lifecycle state rules (`draft`, `proposed`, `published`) under package `school.authz`. |
| [`app/core/opa.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/core/opa.py) | **OPA Authorization Client**. Queries the OPA HTTP endpoint (`http://opa:8181/v1/data/school/authz/allow`) with structured user, action, and resource payloads. |

---

## 📂 5. Database Migrations (`back/alembic/`)

| File Path | Description & Functional Purpose |
| :--- | :--- |
| [`alembic/README`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/alembic/README) | **Alembic Configuration Guide**. Documentation for running and understanding database migrations. |
| [`alembic/env.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/alembic/env.py) | **Alembic Environment Configuration**. Configures migration context and context.configure settings for running migrations. |
| [`alembic/apply_all_tenants.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/alembic/apply_all_tenants.py) | **Multi-Tenant Migration Applicator**. Applies tenant schema migrations across all tenant schemas in the database. |
| [`alembic/script.py.mako`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/alembic/script.py.mako) | **Migration Template**. Mako template for generating new migration scripts. |
| [`alembic/versions/cp_0001_control_plane_baseline.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/alembic/versions/cp_0001_control_plane_baseline.py) | **Control Plane Schema Baseline Migration**. Initial Alembic migration for the control-plane schema. |
| [`alembic/versions/tenant_0001_tenant_schema_baseline.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/alembic/versions/tenant_0001_tenant_schema_baseline.py) | **Tenant Schema Baseline Migration**. Initial Alembic migration for all tenant schemas. |

---

## 📂 6. Authorization & Policy (`back/policies/`)

| File Path | Description & Functional Purpose |
| :--- | :--- |
| [`policies/school_policy.rego`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/policies/school_policy.rego) | **Open Policy Agent (OPA) Authorization Policy**. Enforces role permission matrix, multi-tenant boundaries (`tenant_id`), and event lifecycle state rules (`draft`, `proposed`, `published`) under package `school.authz`. Contains 80+ test cases for comprehensive coverage. |

---

## 📂 7. Testing Infrastructure (`back/tests/`)

| File Path | Description & Functional Purpose |
| :--- | :--- |
| [`tests/conftest.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/tests/conftest.py) | **Pytest Configuration & Fixtures**. Sets up test database, async fixtures, and shared test utilities for all test suites. |
| [`tests/unit/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/tests/unit/) | **Unit Tests**. Isolated tests for services, repositories, and utilities without database dependency. |
| [`tests/integration/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/tests/integration/) | **Integration Tests**. Tests with real database connection, testing domain endpoints and service layers. |
| [`tests/e2e/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/tests/e2e/) | **End-to-End QA Tests**. Full workflow testing through HTTP endpoints with real authentication flows. |

---

## 📂 8. Backend Business Domains (`back/app/domains/`)

| File Path | Description & Functional Purpose |
| :--- | :--- |
| [`app/domains/auth/router.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/domains/auth/router.py) | **Authentication Endpoints**. Handlers for `/login`, `/register`, `/tenants`, `/invitations`, `/me`, and `/profile`. |
| [`app/domains/auth/service.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/domains/auth/service.py) | **Authentication Business Logic**. Hashing passwords, issuing signed JWTs, registration routing, and invitation code validation. |
| [`app/domains/tenant/service.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/domains/tenant/service.py) | **Tenant & School Operational Service**. Manages Level, Class, Teacher, and Student creation, PII field masking (`national_id`, `emergency_contact`), and audit trail logging. |
| [`app/domains/tenant/control_plane_repository.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/domains/tenant/control_plane_repository.py) | **Control-Plane Data Access Layer**. Queries global tables (`tenants`, `parents`, `user_tenant_map`, `invitations`). |
| [`app/domains/tenant/tenant_repository.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/domains/tenant/tenant_repository.py) | **Tenant Schema Data Access Layer**. Executes SQL queries against tenant schemas (`levels`, `class`, `teachers`, `students`, `events`, `resources`). |
| [`app/domains/tenant/user_repository.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/domains/tenant/user_repository.py) | **Tenant Users Data Access Layer**. SQL queries on `tenant_x.users` table for creation, email lookups, and profile updates. |
| [`app/domains/events/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/domains/events/) | **Event Planning & Lifecycle Domain**. Wizard endpoints, state machine transitions (`draft` ➔ `proposed` ➔ `published`), pricing calculators, and attendance predictions. |
| [`app/domains/students/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/domains/students/) | **Roster & Student Operations Domain**. Roster filtering by class, student registration, and parent-child linking. |
| [`app/domains/school/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/domains/school/) | **School Onboarding & Setup Domain**. Three-layer domain (router, service, repository) managing school profile, campuses, contacts, and activation lifecycle with irreversible stage commits. |
| [`app/domains/invitations/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/domains/invitations/) | **User Invitation & Onboarding Domain**. Manages invitation code generation, email delivery, and user role provisioning flow. |
| [`app/domains/notifications/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/domains/notifications/) | **In-App Notifications Domain**. Handles notification feed, delivery tracking (`delivered_at`, `read_at`), and event-triggered broadcasts. |
| [`app/domains/analytics/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/back/app/domains/analytics/) | **Cross-Tenant Analytics Domain**. Aggregates metrics across all tenant schemas; restricted to `super_admin` role only. |

---

## 📂 9. Frontend Application (`front/`)

| File Path | Description & Functional Purpose |
| :--- | :--- |
| [`vite.config.js`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/front/vite.config.js) | **Vite Build Configuration**. Configured with `host: '0.0.0.0'` and port `3000` for Docker Nginx proxy compatibility. |
| [`src/main.js`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/front/src/main.js) | **Vue 3 Entry Point**. Initializes Pinia, Vue Router, and Keycloak SSO JS adapter with safety timeout for instant UI rendering. |
| [`src/api.js`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/front/src/api.js) | **Axios HTTP Client**. Intercepts requests to attach Bearer tokens and `X-Tenant-ID` header on every request. |
| [`src/keycloak.js`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/front/src/keycloak.js) | **Keycloak OIDC Client Adapter**. Connects to Keycloak realm `SAMS` and client `frontend`. |
| [`src/store.js`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/front/src/store.js) | **Pinia Global State Management**. Three option-style stores (`useAuthStore`, `useEventStore`, `useNotificationStore`) plus the client-side `COMPOSITE_ROLE_PERMISSIONS` capability map. Manages user session, active tenant selection (`sd_active_tenant`), and roles. |
| [`src/router.js`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/front/src/router.js) | **Vue Router Navigation Setup**. Flat route table + a single global `beforeEach` auth guard (`requiresAuth` / `requiresGuest`). Note: there is **no route-level role gating** — role restrictions are enforced in-template and by the backend. |
| [`src/App.vue`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/front/src/App.vue) | **Root Vue Component**. Layout shell containing `LayoutSidebar`, top header, theme toggle, super-admin tenant switcher, pending-role gate, and `<router-view>`. |
| [`src/index.css`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/front/src/index.css) | **Design System**. Tailwind entry plus the CSS-variable theme palette (light/dark) and semantic utility classes (`.theme-card`, `.theme-input`, `.btn-primary`, `.nav-item`). |
| [`src/components/DashboardView.vue`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/front/src/components/DashboardView.vue) | **Multi-Role Dashboard Hub**. Segmented tab bar (Published Trips / Manager Review Queue / Teacher Workspace) with live count badges, per-role perspective switcher, and the enrollment/approval right rail. |
| [`src/components/ManageStructureView.vue`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/front/src/components/ManageStructureView.vue) | **Academic Administration Hub**. Three tabs in one component: Live Structure & Classes, Student Class Placement, and the Setup & Ladder Wizard (14-stage curriculum spine, max 25 sections/grade). |
| [`src/components/EventPublishedCard.vue`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/front/src/components/EventPublishedCard.vue) | **Event Card Component**. Renders direct `Enroll [Child Name]` buttons for multi-child parents with 0ms optimistic UI updates. (Named `EventPublishedCard`, **not** `PublishedEventCard`.) |
| [`src/components/wizard/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/front/src/components/wizard) | **4-Step Event Wizard**. `EventWizard.vue` orchestrator + `StepBasics` → `StepAudience` → `StepResources` → `StepProposalReview`, with `WizardStepper.vue` for progress. |

> **Layout note:** all frontend components — routed views *and* shared components — live
> flat in `front/src/components/`. There is no `src/views/`, no `src/pages/`, no
> `src/composables/`, and no `src/router/` directory (routing is the single file
> `src/router.js`). Only `wizard/` is nested.
