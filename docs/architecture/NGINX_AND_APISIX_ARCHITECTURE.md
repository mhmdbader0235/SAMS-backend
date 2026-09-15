# Complete Guide: Nginx & Apache APISIX Architecture in SAMS

This document provides a comprehensive explanation of how **Nginx**, **Apache APISIX**, **Keycloak**, and the **FastAPI Backend** work together in the SAMS application.

> ## ⚠️ Read this first — much of what follows is TARGET DESIGN, not shipped behaviour
>
> Reconciled against the code on **2026-09-08**. For what actually runs, see
> [`PROJECT_UNDERSTANDING.md`](../../../PROJECT_UNDERSTANDING.md) §4 and
> [`adr/0004-apisix-as-defense-in-depth-not-auth-authority.md`](adr/0004-apisix-as-defense-in-depth-not-auth-authority.md).
>
> Specifically, this document describes as present several things that **do not exist**:
>
> | Claim below | Reality |
> |---|---|
> | APISIX validates JWT signatures at the edge | **It does not, and by decision never will** — two token issuers make it impossible (ADR-0004). It *does* require an `Authorization: Bearer` header to be present, which is not the same thing. |
> | APISIX is "backed by `etcd`" | Standalone YAML mode (`config_provider: yaml`), no etcd, Admin API disabled. A stray `example-etcd-1` container from an old experiment is not part of this stack. |
> | APISIX gateway on `:9080` | `:9080` is **nginx**. APISIX listens on `:9180` and publishes no proxy port at all; only `127.0.0.1:9091` (Prometheus metrics) is published. |
> | `gateway/apisix_proxy_route.json` maps the routes | That file does not exist. Routes live in `gateway/apisix/apisix.yaml`. |
> | Nginx serves the Vue build (`dist/`) with `try_files` and Gzip/Brotli | `gateway/nginx.conf` has no `root`, no `try_files`, and no compression — it proxies `/` to the Vite **dev server** on `:3000`. A correct production config exists at `front/nginx.conf` (used by `front/Dockerfile`) but **no compose service references it**. |
> | Nginx terminates SSL/TLS | No SSL config anywhere; the stack is plain HTTP on `:9080`. |
> | Prometheus (`:9090`) + Grafana (`:3005`) integration | The `prometheus` **plugin** is now enabled and exports on `127.0.0.1:9091`, but there is no Prometheus server and no Grafana in this stack. Scrape it manually. |
> | Nginx routes `/auth/*` to Keycloak | Only `/realms/*` is routed. `/api/v1/auth/*` is the **backend's** auth router, not Keycloak. |
> | "DMZ vs private network" separation | There are no custom Docker networks; every container is on the default compose bridge. Conceptual only. |
>
> The §3 "Functional Audit" table further below marks several of these as ✅ Correct. Those
> verdicts are wrong; treat this banner as authoritative over them.

---

## 🏛️ 1. Architecture Overview & High-Level Flow

The application uses a **defense-in-depth, 4-tier microservices & gateway architecture**:

```
                              [ Client Browser / Vue Frontend ]
                                              │
                                              │ (HTTP/HTTPS)
                                              ▼
                         ┌─────────────────────────────────────────┐
                         │   Nginx Edge Server (:9080, proxy only) │
                         │   - Proxies / to Vite dev server :3000  │
                         │     (does NOT serve dist/ — aspirational)│
                         │   - No SSL termination today            │
                         │   - No try_files today                  │
                         └────────────┬──────────────────┬─────────┘
                                      │                  │
                            /realms/*               /api/v1/*
                                      │                  │
                                      ▼                  ▼
                         ┌─────────────────┐    ┌───────────────────┐
                         │ Keycloak OIDC   │    │ Apache APISIX     │
                         │ Server (:8000)  │    │ Gateway (:9180)   │
                         │ - Realm: SAMS   │    │ - Routing         │
                         │ - Organizations │    │ - Rate Limiting   │
                         └─────────────────┘    │ - Request-ID      │
                                                │ - Metrics         │
                                                │ - Body caps       │
                                                │ - Bearer PRESENT  │
                                                │   (no signature   │
                                                │    check — see    │
                                                │    ADR-0004)      │
                                                └─────────┬─────────┘
                                                          │
                                                    Proxy Pass
                                                          │
                                                          ▼
                                                ┌───────────────────┐
                                                │ FastAPI Backend   │
                                                │ Server (:8001)    │
                                                │ - Multi-Tenancy   │
                                                │ - Postgres DB     │
                                                └───────────────────┘
```

---

## ⚙️ 2. Detailed Roles & Responsibilities

### 🔹 Nginx (Edge Web Server & Reverse Proxy)
* **Primary Role**: Nginx sits at the outermost edge of your network (Port 80/443).
* **Key Functions**:
  1. **Serving Static Files**: Host the compiled Vue 3 production build (`dist/`). Nginx delivers static HTML, JS, CSS, and images with Gzip/Brotli compression at lightning speed.
  2. **SPA Routing Protection**: Implements `try_files $uri $uri/ /index.html;`. This ensures that refreshing a deep page (e.g. `/events/123`) returns `index.html` so Vue Router can handle client-side routing instead of returning a browser 404.
  3. **Unified Single-Origin Proxy**: Routes `/api/v1/*` to APISIX (`:9080`) and `/auth/*` / `/realms/*` to Keycloak (`:8000`). This removes CORS issues completely in production.
  4. **SSL/TLS Termination**: Manages HTTPS certificates (Let's Encrypt / Certbot) in one single place.

### 🔹 Apache APISIX (API Gateway)
* **Primary Role**: Enterprise API Gateway (built on OpenResty / Nginx + Lua). **Note:** run here in *standalone YAML mode* — no `etcd`, Admin API disabled, config is the git-tracked `gateway/apisix/apisix.yaml`.
* **Key Functions**:
  1. **Dynamic Routing**: Maps incoming requests (`/api/v1/*`) to backend upstream instances (`host.docker.internal:8001`) dynamically without restarting the server.
  2. **API Traffic Management**: Handles Rate-Limiting, IP Whitelisting/Blacklisting, Circuit Breaking, and Health Checks.
  3. **Token Verification**: Validates JWT signatures upstream before requests hit FastAPI.
  4. **Observability & Analytics**: Integrates with **Prometheus** (Port 9090) and **Grafana** (Port 3005) for live API monitoring.

### 🔹 Keycloak (Identity & Access Management)
* **Primary Role**: Centralized OIDC Authentication Server.
* **Key Functions**:
  1. **Single Shared Realm (`SAMS`)**: Houses all multi-tenant users.
  2. **Tenant Organizations**: Segregates schools into Keycloak Organizations (`tenant_a`, `tenant_b`).
  3. **JWT Token Issuance**: Issues signed RS256/HS256 tokens containing user roles and `tenant_id` claims.

###  accumulation 🔹 FastAPI Backend (`doumind-backend`)
* **Primary Role**: Core business logic and database execution server (Port 8001).
* **Key Functions**:
  1. Decodes JWT token passed from APISIX / Nginx.
  2. Extracts `tenant_id` (e.g. `tenant_a`) and switches database context (`SET search_path TO "tenant_a"`).
  3. Executes RBAC permissions check and multi-tenant PostgreSQL queries.

---

## 🔍 3. Functional Audit & Verification Checklist

| Functional Requirement | Status | Implementation Details |
| :--- | :---: | :--- |
| **API Gateway Upstream Route** | ✅ **Correct** | `gateway/apisix_proxy_route.json` maps `/api/v1/*` ➔ `host.docker.internal:8001`. |
| **Multi-Tenant Context Propagation** | ✅ **Correct** | APISIX forwards `Authorization: Bearer <JWT>`. FastAPI `dependencies.py` extracts `tenant_id` claim and isolates Postgres schema. |
| **Single Keycloak Realm (`SAMS`) Integration** | ✅ **Correct** | `KEYCLOAK_REALM=SAMS` configured across `.env`, `keycloak_admin.py`, and `SAMS-realm.json`. |
| **Vue SPA Fallback Routing** | ✅ **Correct** | Nginx `try_files $uri $uri/ /index.html` configured for frontend deployment. |
| **Invitation Pre-Provisioning System** | ✅ **Correct** | 3-layer flow in `app/domains/invitations/` (Router ➔ Service ➔ Repository) pre-creates user in Keycloak `SAMS` realm and audits log in `user_invitations` table. |

---

## 🚀 4. How to Run the Complete Stack

1. **Start PostgreSQL Database**:
   ```bash
   docker compose up -d db
   ```

2. **Start APISIX & Monitoring Infrastructure**:
   ```bash
   cd gateway/apisix-docker/example
   docker compose up -d
   ```

3. **Start Keycloak Identity Provider**:
   ```bash
   cd "docker - keycloak"
   docker compose up -d
   ```

4. **Start Backend Server**:
   ```bash
   cd doumind-backend
   .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
   ```

5. **Start Frontend Vue App**:
   ```bash
   cd doumind-frontend
   npm run dev
   ```
