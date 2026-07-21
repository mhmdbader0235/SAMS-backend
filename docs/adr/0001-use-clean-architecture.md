# ADR 0001 — Use Clean Architecture (Router → Service → Repository)

**Status:** Accepted  
**Date:** 2026-07-09  
**Deciders:** Architecture team

---

## Context

The `doumind-backend` serves multiple schools (tenants), each with their own isolated PostgreSQL database. The application needs to be:
- Easy to test at every layer without spinning up the full stack
- Clear about where business rules live vs. where HTTP handling lives vs. where data access lives
- Maintainable as the feature set grows (events, enrollments, comments, fee tracking)

## Decision

We adopt a strict **three-layer Clean Architecture**:

```
HTTP Request
     │
     ▼
┌─────────────┐
│   Router    │  Handles HTTP: parses request, calls service, returns response
└──────┬──────┘
       │ calls
       ▼
┌─────────────┐
│   Service   │  Business logic: validation, role checks, orchestration
└──────┬──────┘
       │ calls
       ▼
┌──────────────┐
│  Repository  │  Data access: SQL queries via asyncpg. Returns plain dicts.
└──────────────┘
```

### Rules
1. **Routers** must NOT contain business logic or SQL queries.
2. **Services** must NOT import FastAPI or asyncpg directly.
3. **Repositories** must NOT contain business logic.
4. **No cross-layer imports** — each layer only imports the layer directly below it.

## Multi-Tenant Strategy

Each school (tenant) is identified by a `tenant_id` embedded in the JWT. On every authenticated request, the Router extracts `tenant_id` and calls `get_db_pool(tenant_id)` to retrieve the correct connection pool. Tenant databases are created dynamically on first access via `_ensure_database_exists()` and their tables are initialized via `_initialize_tables()`.

This is a **database-per-tenant** isolation model. Schema-per-tenant was considered but rejected because database-level isolation provides stronger security boundaries and simpler backup/restore per school.

## Consequences

- ✅ Each layer is independently unit-testable with mocks
- ✅ Business rules have a single home (Services)
- ✅ Swapping the DB driver (asyncpg → SQLAlchemy) only touches Repositories
- ⚠️ Slightly more boilerplate than a flat script style
- ⚠️ Developers must discipline themselves to not bypass layers
