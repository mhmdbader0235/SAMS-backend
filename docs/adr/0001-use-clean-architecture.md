# ADR 0001 — Use Clean Architecture (Router → Service → Repository)

**Status:** Accepted, **partially superseded** — see *Amendment (2026-08-18)* below  
**Date:** 2026-07-09  
**Deciders:** Architecture team

---

## ⚠️ Amendment (2026-08-18) — reconciled against the implementation

The layering decision below (Router → Service → Repository) still stands. Two parts of
this ADR no longer describe the system as built:

1. **File organisation is domain-first, not layer-first.** There is no `app/routers/`,
   `app/services/`, or `app/repositories/`. Files are grouped by business domain:
   `app/domains/<feature>/{router,service,repository}.py`, with `app/core/` for shared
   infrastructure. See `.agents/AGENTS.md` §1 and
   `back/.agents/skills/domain-driven-architecture/SKILL.md`, which are authoritative.
   Rule 4 below ("no cross-layer imports") should be read as **"no cross-domain imports
   at the Router level"** — cross-domain reuse is permitted at Service/Repository level.

2. **Tenancy is schema-per-tenant, not database-per-tenant.** The "Multi-Tenant Strategy"
   section below records the opposite of what was implemented. All tenants share one
   PostgreSQL database and are isolated by `SET search_path TO "<tenant_id>", public`.
   `app/core/database.py` explicitly forces every tenant config back to the control-plane
   database name (see the `# Force database name to use control plane database` comment).
   The per-tenant `db_host`/`db_port`/`db_name` columns on the control-plane `tenants`
   table and the `TENANT_*_DB_NAME` env vars are unused remnants of this original plan.

Known deviations from the layering rules in the current code are catalogued in
`.agents/AGENTS.md` §1 ("Known deviations") — notably `domains/events/` calling
`TenantService`/`TenantRepository` directly from its router.

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
