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