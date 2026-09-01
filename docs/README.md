# back/docs

- `architecture/` — how the system actually runs: gateway/Keycloak workflow guides, the Nginx/APISIX topology doc, and `adr/` (the architecture decision records).
- `reference/` — lookup tables: the permissions catalog, the Keycloak roles catalog, and the backend files dictionary.
- `testing/` — Postman collection, the guide for running it, and seeded demo-data credentials.

This split was made 2026-09-01 (see PROJECT_UNDERSTANDING.md and .resourses/REFERENCE_INDEX.md, which link into the new paths). If you add a new doc here, put it in the subfolder matching its job rather than back/docs/ directly.
