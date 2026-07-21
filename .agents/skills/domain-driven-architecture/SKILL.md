---
name: domain-driven-architecture
description: Enforces domain-driven (feature-first) organization for all backend API development.
---

# Domain-Driven Architecture (Backend)

When adding new features to the SchoolDesk backend, do NOT separate files by their technical layer (e.g., putting all routers in a `routers/` folder). Instead, group files by their **business domain**.

## Folder Structure
```text
app/
├── core/                   <-- Global configurations, DB connection, base schemas
└── domains/
    └── [feature_name]/     <-- e.g., 'billing', 'attendance', 'events'
        ├── router.py       <-- FastAPI endpoints
        ├── service.py      <-- Business logic
        ├── repository.py   <-- DB queries
        └── schemas.py      <-- Pydantic models for this feature
```

## Rules
1. **Self-Contained Domains**: A domain should contain everything it needs to function.
2. **No Circular Imports**: Domains should rarely import from other domains. If they must, import at the Service or Repository level, NEVER at the Router level.
3. **Core is Global**: Use `app.core` for anything shared across multiple domains (like `dependencies.py` or the DB pool).
