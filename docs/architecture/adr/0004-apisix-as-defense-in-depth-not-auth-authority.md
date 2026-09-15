# ADR-0004: APISIX is defense-in-depth, not the authorization authority

- **Status:** Accepted
- **Date:** 2026-09-08
- **Supersedes:** the edge-JWT-validation intent stated in `.agents/AGENTS.md` §3 and
  `docs/architecture/NGINX_AND_APISIX_ARCHITECTURE.md` §2

## Context

APISIX has been in the stack since the beginning but ran exactly one plugin, `limit-count`, on
two routes. Several documents described it doing considerably more — validating JWT signatures
at the edge, backed by etcd, exporting to Prometheus and Grafana. None of that was implemented.

On 2026-09-08 an attempt to close the auth gap (`GATEWAY_AUTH_HARDENING_PLAN.md` Phase 2, the
`openid-connect` plugin with `bearer_only`) was applied, live-tested for the first time, and
rolled back. It failed for a reason no amount of configuration can fix:

**This application has two token issuers.**

1. `AuthService.create_access_token` mints an **HS256** JWT signed with the app's own
   `JWT_SECRET` for the local email+password path (`POST /api/v1/auth/login`), used by every
   non-SSO account. It is never registered with Keycloak.
2. Keycloak issues **RS256** tokens for the SSO path.

`app/core/dependencies.py` tries the local decoder **first**, then falls back to Keycloak. Any
Keycloak-only edge check — introspection or JWKS — rejects every locally-issued token by
construction.

No single APISIX auth plugin accepts both. `openid-connect` cannot. `multi-auth`, the obvious
workaround, [explicitly refuses `openid-connect` as a sub-plugin](https://github.com/apache/apisix/issues/11514).
The only single-plugin path is `jwt-auth` with two consumers, which requires pinning Keycloak's
RS256 public key into git-tracked YAML — it has no JWKS support, so it breaks silently on key
rotation, and it would duplicate a check `KeycloakVerifier` already performs correctly with an
automatic 300-second JWKS refresh.

## Decision

**1. FastAPI remains the sole authority for authentication, tenancy, and authorization.**
`KeycloakVerifier.verify()` (signature + `exp` + `iss` + `aud`) and
`AuthService.decode_access_token()` are the load-bearing checks. APISIX does not attempt token
verification, and no code may assume it has.

**2. APISIX handles what it can do correctly without a key**: abuse control, request
correlation, metrics, body limits, response headers, and refusing requests that carry no
credential at all. Each is genuinely useful, and none of it can silently grant access.

**3. Tenant resolution stays in the application.** Six of the nine cascade steps require
control-plane or per-tenant database reads, and the `X-Tenant-ID` override is trusted only after
a `super_admins` lookup that can only happen post-decode. A gateway cannot perform any of this.

**4. The `opa` plugin is not used at the edge.** OPA's input requires `tenant_id`, which does not
exist until the cascade above has run. Gateway-level OPA would have to guess the input it is
authorizing on.

**5. Standalone YAML mode is kept over etcd.** `config_provider: yaml` with the Admin API
disabled means the entire gateway config is one reviewable, git-tracked file with no second
source of truth and no cluster to operate. The `tenants` table's `db_host`/`db_name` columns
already demonstrate the cost of infrastructure that suggests a topology the code does not use.

**6. `proxy-cache` is prohibited.** A cache key omitting tenant or identity would serve one
school's data to another. The central invariant here is that no read crosses a tenant boundary;
this feature makes a config typo into a PII incident.

## Consequences

- Edge token *verification* stays unavailable until issuance is unified — most plausibly by
  moving local login onto Keycloak's direct-grant so one issuer exists. That is a decision about
  the login path, not a gateway task, and it is a prerequisite, not an alternative.
- Because APISIX does not verify tokens, `dependencies.py` is security-critical code. Changes
  there carry no gateway safety net.
- The gateway now rejects credential-less requests, so it is one more place a request can fail
  with `401`. The response body distinguishes them: APISIX returns
  `"Missing or malformed Authorization header."`; FastAPI returns `"Missing authentication token"`.
- **No test in either repository covers the gateway.** `pytest` runs in-process against the ASGI
  app and never traverses nginx or APISIX. Gateway behaviour is verified only by the manual curl
  checks recorded in `GATEWAY_AUTH_HARDENING_PLAN.md`. Treat gateway config as unverified until
  those are run against a live stack — Phase 2's rollback happened precisely because a config
  was committed without them.

## What was actually shipped under this decision

Verified live on 2026-09-08 against the running stack:

| Concern | Plugin | Note |
|---|---|---|
| Real client IP | `real-ip` (global) | Keys everything below off the caller, not nginx's container IP |
| Abuse control | `limit-count` | 20/min auth, 30/min invite lookup, 20/min import, 3000/min general |
| Request correlation | `request-id` (global) | Consumed by the app's existing `CorrelationIdMiddleware` |
| Metrics | `prometheus` (global) | `127.0.0.1:9091` — loopback only; the endpoint is unauthenticated |
| PII cache safety | `response-rewrite` (global) | `Cache-Control: no-store`, `nosniff`, `no-referrer` |
| Upload limits | `client-control` | 5 MB on the two otherwise-uncapped XLSX import endpoints |
| Credential presence | `request-validation` | `Authorization: Bearer …` required except on the four public endpoints and CORS preflight |
