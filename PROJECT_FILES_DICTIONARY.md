# 📖 Complete Codebase File & Folder Dictionary

> **Project Name**: SchoolDesk / Doumind (Multi-Tenant School Administration System)  
> **Architecture**: Vue 3 (SPA) + FastAPI (Async Python) + PostgreSQL (Multi-Schema Isolation) + Keycloak 26 (OIDC & Organizations) + Nginx (DMZ) + Apache APISIX (API Gateway)

---

## 📂 1. Workspace Root Directory

| File / Folder Path | Description & Functional Purpose |
| :--- | :--- |
| [`docker-compose.yml`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/docker-compose.yml) | **Single Source of Truth Docker Orchestration File**. Defines `doumind-db` (PostgreSQL 16 on port 5433), `doumind-keycloak` (Keycloak 26 on port 8000), `doumind-apisix` (Apache APISIX on port 9180), `doumind-gateway` (Nginx DMZ on port 9080), and `doumind-opa` (Open Policy Agent on port 8181 loading `./policies`) with persistent named volumes (`keycloak_data`, `doumind-backend_pgdata`). |

| [`.agents/AGENTS.md`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/.agents/AGENTS.md) | **Architectural Rules & Event Lifecycle Guidelines**. Defines strict class filtering, multi-child parent UI rules, teacher event state machines (`draft` ➔ `proposed` ➔ `published`), optional head teacher schemas, and Keycloak RBAC mappings. |
| [`doumind-backend/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend) | **FastAPI Async Backend Application & Database DDL Schemas**. |
| [`doumind-frontend/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-frontend) | **Vue 3 Vite Single Page Application (SPA)**. |

---

## 📂 2. Backend Infrastructure & Configuration (`doumind-backend/`)

| File / Folder Path | Description & Functional Purpose |
| :--- | :--- |
| [`run.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/run.py) | **Local Development Launcher**. Automates Docker volume creation, boots Docker Compose containers, and runs the FastAPI Uvicorn dev server on `http://127.0.0.1:8001` with hot-reloading. |
| [`init.sql`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/init.sql) | **Master PostgreSQL DDL Schema Script**. Initializes control-plane tables (`tenants`, `parents`, `super_admins`, `user_tenant_map`, `user_invitations`) and isolated tenant schemas (`tenant_a`, `tenant_b`) with tables for `users`, `teachers`, `students`, `class`, `levels`, `event`, `resources`, `resource_cost`, `event_registrations`, and `invoices`. |
| [`SAMS-realm.json`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/SAMS-realm.json) | **Keycloak 26 Realm Export JSON**. Contains pre-configured realm roles, groups, clients (`frontend`, `apisix`), and Organizations (`tenant_a` with `schoolA.com`, `tenant_b` with `schoolB.com`). |
| [`create_keycloak_realm_json.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/create_keycloak_realm_json.py) | **Keycloak Realm Generator Script**. Generates `SAMS-realm.json` programmatically with `"organizationsEnabled": true` and domain mappings. |
| [`seed_data.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/seed_data.py) | **Database & Keycloak Seeder Script**. Seeds demo data into PostgreSQL (`tenant_a` and `tenant_b`) and syncs all accounts directly to Keycloak Organizations via Admin REST API. |
| [`Dockerfile`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/Dockerfile) | **Production Container Build Spec**. Multi-stage Python 3.11/3.14 build for running the FastAPI application via Uvicorn. |
| [`pyproject.toml`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/pyproject.toml) | **Python Project Metadata & Dependencies**. Configures project dependencies (`fastapi`, `asyncpg`, `pydantic`, `pyjwt`, `passlib`, `cryptography`). |
| [`.env`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/.env) | **Backend Runtime Environment Variables**. Contains DB connection strings, JWT secret keys, and Keycloak URLs. |
| [`.env.example`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/.env.example) | **Environment Template Blueprint**. Template for environment variables with safe placeholder values for new developers and CI/CD pipelines. |

---

## 📂 3. Backend Gateway & Documentation (`doumind-backend/gateway/` & `doumind-backend/docs/`)

| File / Folder Path | Description & Functional Purpose |
| :--- | :--- |
| [`gateway/nginx.conf`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/gateway/nginx.conf) | **Production Nginx Reverse Proxy Configuration**. Handles port `9080` routing for `/` (Frontend SPA `:3000`), `/api/v1/` (FastAPI Backend `:8001`), and `/realms/` (Keycloak `:8080`). |
| [`docs/KEYCLOAK_WORKFLOW_GUIDE.md`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/docs/KEYCLOAK_WORKFLOW_GUIDE.md) | **Keycloak OIDC & Organization Technical Spec**. Details authentication flows, Organization member assignment, and JWT claims. |
| [`docs/NGINX_AND_APISIX_ARCHITECTURE.md`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/docs/NGINX_AND_APISIX_ARCHITECTURE.md) | **Reverse Proxy & Gateway Architecture Specification**. Documents single-gateway Nginx routing rules and port mappings. |
| [`docs/PERMISSIONS_CATALOG_REFERENCE.md`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/docs/PERMISSIONS_CATALOG_REFERENCE.md) | **RBAC Permissions Matrix Catalog**. Documents permissions for `super_admin`, `school_admin`, `manager`, `teacher`, `parent`, and `student`. |
| [`docs/SEEDED_DEMO_DATA.txt`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/docs/SEEDED_DEMO_DATA.txt) | **Seeded Demo Accounts Reference Sheet**. Contains login credentials (`All passwords: 123321`), class listings, and teacher IDs. |

---

## 📂 4. Backend Application Core (`doumind-backend/app/core/`)

| File Path | Description & Functional Purpose |
| :--- | :--- |
| [`app/main.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/main.py) | **FastAPI Application Initialization**. Configures CORS middleware, connects database connection pools on startup, and registers API routers. |
| [`app/core/config.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/core/config.py) | **Central Configuration Loader**. Loads environment variables from `.env` (DB credentials, JWT settings, Keycloak URLs, Fernet encryption keys). |
| [`app/core/database.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/core/database.py) | **Multi-Tenant Async Database Connection Pool Engine**. Manages `asyncpg` connection pools per tenant (`tenant_a`, `tenant_b`) and control-plane DB. Automatically generates schemas for new tenants. |
| [`app/core/dependencies.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/core/dependencies.py) | **Security & Authorization Engine**. Validates Bearer JWTs, extracts Keycloak Organization claims, enforces `X-Tenant-ID` header, auto-provisions missing Keycloak users JIT in PostgreSQL, and constructs the `CurrentUser` object. |
| [`app/core/keycloak_admin.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/core/keycloak_admin.py) | **Keycloak Admin REST API Client**. Handles `sync_user_to_keycloak(...)` by provisioning accounts, updating user attributes (`tenant_id`, `role`), and mapping members to Keycloak Organizations (`/organizations/{org_id}/members`). |
| [`policies/school_policy.rego`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/policies/school_policy.rego) | **Open Policy Agent (OPA) Rego Authorization Policy**. Enforces role permission matrix, multi-tenant boundaries (`tenant_id`), and event lifecycle state rules (`draft`, `proposed`, `published`) under package `school.authz`. |
| [`app/core/opa.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/core/opa.py) | **OPA Authorization Client**. Queries the OPA HTTP endpoint (`http://opa:8181/v1/data/school/authz/allow`) with structured user, action, and resource payloads. |

---


## 📂 5. Backend Business Domains (`doumind-backend/app/domains/`)

| File Path | Description & Functional Purpose |
| :--- | :--- |
| [`app/domains/auth/router.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/domains/auth/router.py) | **Authentication Endpoints**. Handlers for `/login`, `/register`, `/tenants`, `/invitations`, `/me`, and `/profile`. |
| [`app/domains/auth/service.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/domains/auth/service.py) | **Authentication Business Logic**. Hashing passwords, issuing signed JWTs, registration routing, and invitation code validation. |
| [`app/domains/tenant/service.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/domains/tenant/service.py) | **Tenant & School Operational Service**. Manages Level, Class, Teacher, and Student creation, PII field masking (`national_id`, `emergency_contact`), and audit trail logging. |
| [`app/domains/tenant/control_plane_repository.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/domains/tenant/control_plane_repository.py) | **Control-Plane Data Access Layer**. Queries global tables (`tenants`, `parents`, `user_tenant_map`, `invitations`). |
| [`app/domains/tenant/tenant_repository.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/domains/tenant/tenant_repository.py) | **Tenant Schema Data Access Layer**. Executes SQL queries against tenant schemas (`levels`, `class`, `teachers`, `students`, `events`, `resources`). |
| [`app/domains/tenant/user_repository.py`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/domains/tenant/user_repository.py) | **Tenant Users Data Access Layer**. SQL queries on `tenant_x.users` table for creation, email lookups, and profile updates. |
| [`app/domains/events/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/domains/events/) | **Event Planning & Lifecycle Domain**. Wizard endpoints, state machine transitions (`draft` ➔ `proposed` ➔ `published`), pricing calculators, and attendance predictions. |
| [`app/domains/students/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/domains/students/) | **Roster & Student Operations Domain**. Roster filtering by class, student registration, and parent-child linking. |
| [`app/domains/billing/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/domains/billing/) | **Invoicing & Billing Domain**. Invoice generation, trip payment processing, and audit logs. |
| [`app/domains/announcements/`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-backend/app/domains/announcements/) | **Public & School Announcements Domain**. Publishing targeted school announcements and notification broadcasts. |

---

## 📂 6. Frontend Application (`doumind-frontend/`)

| File Path | Description & Functional Purpose |
| :--- | :--- |
| [`vite.config.js`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-frontend/vite.config.js) | **Vite Build Configuration**. Configured with `host: '0.0.0.0'` and port `3000` for Docker Nginx proxy compatibility. |
| [`src/main.js`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-frontend/src/main.js) | **Vue 3 Entry Point**. Initializes Pinia, Vue Router, and Keycloak SSO JS adapter with safety timeout for instant UI rendering. |
| [`src/api.js`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-frontend/src/api.js) | **Axios HTTP Client**. Intercepts requests to attach Bearer tokens and `X-Tenant-ID` header on every request. |
| [`src/keycloak.js`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-frontend/src/keycloak.js) | **Keycloak OIDC Client Adapter**. Connects to Keycloak realm `SAMS` and client `frontend`. |
| [`src/store.js`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-frontend/src/store.js) | **Pinia Global State Management**. Manages current user session, active tenant selection (`sd_active_tenant`), and roles. |
| [`src/router/index.js`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-frontend/src/router/index.js) | **Vue Router Navigation Setup**. Handles route guards, role restrictions, and page routing. |
| [`src/App.vue`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-frontend/src/App.vue) | **Root Vue Component**. Main layout shell containing header navigation, tenant switcher, and router view. |
| [`src/views/PublishedEventsView.vue`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-frontend/src/views/PublishedEventsView.vue) | **Published Events View**. Renders published trips filtered strictly by student class. |
| [`src/components/PublishedEventCard.vue`](file:///c:/Users/mb883/OneDrive/Desktop/tests/TestAiDoumind-main%201/doumind-frontend/src/components/PublishedEventCard.vue) | **Event Card Component**. Renders direct `Enroll [Child Name]` buttons for multi-child parents with 0ms optimistic UI updates. |
