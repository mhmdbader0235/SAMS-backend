# Gateway & Token-Validation Hardening Plan

**Scope:** `back/gateway/apisix/`, `back/run.py`.
**Goal:** make APISIX actually reject bad tokens and unfair traffic instead of only rate-limiting, and stop the backend from being reachable by walking around the gateway.
**Status of this doc:** Phase 2 was applied to `apisix.yaml` and then **rolled back** (2026-09-08),
per this doc's own prescribed rollback step, after actually running its "How to test" steps for the
first time and finding it locked out every login in the running stack:

1. `APISIX_KC_CLIENT_SECRET` was never set in `back/.env`, so the plugin rejected every request with
   `invalid_client` regardless of token validity. Fixed by pulling the real secret from Keycloak's
   admin console and setting it in `back/.env`.
2. With that fixed, every request still failed — including a freshly-issued, genuine Keycloak token
   for the realm's `frontend` client. Confirmed with `curl` directly against Keycloak (bypassing
   APISIX and Docker networking entirely): `/protocol/openid-connect/token/introspect` returns
   `{"active": false}` and `/protocol/openid-connect/userinfo` returns `403` for that same token,
   seconds after issuance. This is a Keycloak realm/client-scope problem, not a gateway config typo.
3. Separately, and not fixable by any Keycloak-side correction: this app has two token issuers.
   `POST /api/v1/auth/login` (the local email+password path, used by every non-SSO account) mints
   its own JWT via `AuthService.create_access_token` — never registered with Keycloak — so no
   Keycloak-only introspection check can ever accept it. Phase 2 as designed only works for an
   SSO-only deployment; this app is not one.

**Superseded 2026-09-08 — read `docs/architecture/adr/0004-apisix-as-defense-in-depth-not-auth-authority.md`
first.** Final status of this document's three phases:

- **Phase 1 — applied, then found insufficient and re-fixed.** Keying `limit-count` on
  `http_x_forwarded_for` was itself a bypass: `gateway/nginx.conf` sets that header with
  `$proxy_add_x_forwarded_for`, which *appends* to whatever the caller sent, so rotating a fake
  prefix produced a fresh bucket per request. Reproduced live: 30 login attempts with a rotating
  `X-Forwarded-For` drew **zero** `429`s. Now keyed on `remote_addr` behind the `real-ip` plugin
  (`X-Real-IP` is safe because nginx *overwrites* it) and re-verified. The
  login/register/tenants split was also incomplete — `GET /auth/invitations/{code}` is a **fourth**
  public endpoint and now has its own 30/min bucket.
- **Phase 2 — closed as won't-fix, not deferred.** Item 3 below (two token issuers) is not a
  configuration problem and has no gateway-side redesign: `openid-connect` cannot accept two
  issuers, and `multi-auth` explicitly refuses `openid-connect` as a sub-plugin
  (apache/apisix#11514). `use_jwks: true` would fix item 2 (the introspection failure) but not
  item 3. Edge verification requires unifying issuance first — a decision about the login path.
  APISIX instead enforces *credential presence* (`request-validation`), which cannot silently
  grant access.
- **Phase 3 — already applied**, contrary to what this line previously said: `run.py` and
  `main.py` both bind `127.0.0.1`.

---

## Correction before you start

Earlier in this conversation I repeated a claim from `PROJECT_UNDERSTANDING.md` §14.5 that `dependencies.py` has an insecure fallback which decodes Keycloak tokens with `verify_signature: False`. Before writing this plan I went and read the actual code instead of trusting that doc, and **that specific bug is already fixed**:

- `back/app/core/keycloak_jwt.py`'s `KeycloakVerifier.verify()` always checks signature, `exp`, `iss`, and `aud` via Keycloak's JWKS, with no unverified fallback path — its own docstring says so explicitly.
- `back/app/domains/auth/service.py`'s `decode_access_token()` (for internally-issued tokens) is a plain signature-verified `jwt.decode(...)`, nothing weaker.
- `back/tests/unit/test_keycloak_jwt.py` has a named regression test, `test_unsigned_verify_signature_false_style_token_is_rejected`, whose docstring reads *"Regression guard for the bug being fixed: a token that only an unverified `verify_signature: False` decode would have accepted."* — someone already found and fixed this, and left a test guarding against it coming back.

So there's nothing to do on that front. `PROJECT_UNDERSTANDING.md` §14.5 and the related sentence in `CLAUDE.md` §4 ("the signature-verification fallback in `dependencies.py` is the load-bearing check") are now stale — worth a quick doc fix on its own, separate from this plan, since this codebase's own convention is that fixing a doc you find drifted is part of the work.

What's left to actually fix is at the gateway, not in the token-verification code.

---

## Phase 1 — Fix who the rate limit actually counts (low risk, do this first)

**File:** `back/gateway/apisix/apisix.yaml`

**Current state:**
```yaml
routes:
  -
    uri: /api/v1/*
    plugins:
      limit-count:
        count: 3000
        time_window: 60
        rejected_code: 429
    upstream:
      nodes:
        "host.docker.internal:8001": 1
      type: roundrobin
#END
```

No `key` is set, so `limit-count` falls back to APISIX's default key: the raw `remote_addr` of the TCP connection APISIX receives. Since Nginx is the only thing that ever connects to APISIX, that address is Nginx's own container IP on every request — meaning today this is almost certainly **one shared bucket for the entire app**, not one per user. It's also one uniform limit for both `/api/v1/auth/login` and every other route.

**Proposed change** — key on the forwarded client IP (Nginx already sets this header — confirmed in `back/gateway/nginx.conf`), and split login into its own, stricter bucket:

```yaml
routes:
  -
    uris:
      - /api/v1/auth/login
      - /api/v1/auth/register
      - /api/v1/auth/tenants
    plugins:
      limit-count:
        count: 20
        time_window: 60
        rejected_code: 429
        key_type: "var"
        key: "http_x_forwarded_for"
    upstream:
      nodes:
        "host.docker.internal:8001": 1
      type: roundrobin
  -
    uri: /api/v1/*
    plugins:
      limit-count:
        count: 3000
        time_window: 60
        rejected_code: 429
        key_type: "var"
        key: "http_x_forwarded_for"
    upstream:
      nodes:
        "host.docker.internal:8001": 1
      type: roundrobin
#END
```

Notes before you apply this:

- `uris` (plural) for a list of exact paths is supported alongside singular `uri` in APISIX 3.8 — confirm against `apisix version` / the bundled plugin docs in your image if it errors on load.
- APISIX's router matches the most specific route, so a request to `/api/v1/auth/login` should hit the first block and everything else falls through to the wildcard — verify this is really what happens in your version before trusting it, don't assume.
- 20 requests/60s on login is a starting number, not a magic one — tune it against how your own login UX behaves (Keycloak redirects, retries, etc.) so real users don't get 429'd during normal use.
- Register and tenants-list share the strict bucket here because they're also unauthenticated and worth protecting from abuse; split them into their own bucket instead if you want different limits for each.

**How to test:**
1. `docker compose up -d` (or `python run.py`) to reload APISIX with the new config.
2. From two different machines/IPs (or `curl --interface` / a VPN toggle to simulate two source IPs), hammer `/api/v1/auth/login` on one and confirm the *other* IP is unaffected — that proves the bucket is now per-caller, not global.
3. Confirm normal dashboard use (which polls periodically) doesn't get 429'd under the new numbers.

---

## Phase 2 — Add real token validation at the gateway (medium risk — test before trusting)

**File:** `back/gateway/apisix/apisix.yaml` (same file, extending Phase 1's result)

**The one thing that will break everything if you get it wrong:** three endpoints are intentionally public and have no token to check yet — `POST /api/v1/auth/login`, `POST /api/v1/auth/register`, and `GET /api/v1/auth/tenants` (confirmed in `back/README.md`'s endpoint table; there may be others not in that table's short list — grep `back/app/domains/auth/router.py` and `back/app/domains/invitations/router.py` for endpoints that don't call `Depends(get_current_user)` before assuming this list is complete). If gateway-level JWT validation gets applied to those routes too, nobody will ever be able to log in — the login call itself would get rejected for not carrying a token it's supposed to be requesting.

That's exactly why Phase 1 already split those three onto their own route block — leave that block **without** the token-validation plugin, and only add it to the wildcard block that covers everything else:

```yaml
routes:
  -
    uris:
      - /api/v1/auth/login
      - /api/v1/auth/register
      - /api/v1/auth/tenants
    plugins:
      limit-count:
        count: 20
        time_window: 60
        rejected_code: 429
        key_type: "var"
        key: "http_x_forwarded_for"
    upstream:
      nodes:
        "host.docker.internal:8001": 1
      type: roundrobin
  -
    uri: /api/v1/*
    plugins:
      limit-count:
        count: 3000
        time_window: 60
        rejected_code: 429
        key_type: "var"
        key: "http_x_forwarded_for"
      openid-connect:
        discovery: "http://keycloak:8080/realms/SAMS/.well-known/openid-configuration"
        client_id: "apisix"
        client_secret: "<pull the real secret from Keycloak — see below, don't hardcode a placeholder>"
        bearer_only: true
        realm: "SAMS"
    upstream:
      nodes:
        "host.docker.internal:8001": 1
      type: roundrobin
#END
```

Steps to get this right:

1. **Get the `apisix` client's secret.** `back/SAMS-realm.json` defines an `apisix` confidential client (per `PROJECT_UNDERSTANDING.md` §8.1). Either read its current secret out of that file directly, or — cleaner — open the Keycloak admin console (`http://localhost:8000`, realm `SAMS` → Clients → `apisix` → Credentials tab), regenerate it there, and put the fresh value in the yaml and back into `SAMS-realm.json` so a future `--import-realm` doesn't reset it. Don't leave the secret in this doc or commit it in plaintext if this ever stops being a local dev-only realm.
2. **Use the internal Docker address for discovery**, not the browser-facing one. APISIX calls this server-to-server inside the compose network, so it needs `keycloak:8080` (the compose service name and its internal port), not `localhost:8000`.
3. **Check the issuer match before trusting this.** `back/app/core/keycloak_jwt.py` has its own comment flagging that nobody has confirmed what `iss` value Keycloak actually stamps into a live token versus what `KEYCLOAK_URL`/`KEYCLOAK_ISSUER` resolves to. If APISIX's discovery step resolves a different issuer than what's actually in the token, *valid* logins will start failing with 401 at the gateway. Get one real token (log in through the normal UI, copy it from browser dev tools) and decode it (jwt.io or `python -c "import jwt; print(jwt.decode(token, options={'verify_signature': False}))"` — unverified decode is fine for reading claims to debug, just don't use that pattern in the app itself) to see its real `iss` and `aud` before flipping this on.
4. **Confirm the plugin still forwards the original `Authorization` header downstream.** FastAPI's own verification (the one covered in the correction above) should keep running — this gateway check should be an *additional* barrier, not a replacement. If the plugin config strips or rewrites the auth header instead of passing it through, FastAPI will 401 every request even though APISIX approved it.
5. **Exact field names may differ from what's above.** `openid-connect` plugin schemas have changed across APISIX versions; check `apisix.yaml`'s field names against the plugin's schema shipped in your `apache/apisix:3.8.0-debian` image (`docker exec doumind-apisix cat /usr/local/apisix/conf/config-default.yaml` or the plugin's own doc comments in the image) rather than trusting this snippet verbatim.

**How to test, in this order:**
1. Log in through the normal frontend flow, grab the real bearer token.
2. `curl -H "Authorization: Bearer <token>" http://localhost:9080/api/v1/auth/me` — should succeed exactly as before.
3. `curl http://localhost:9080/api/v1/auth/me` (no header at all) — should now get a `401` **from APISIX**, not from FastAPI. Confirm by checking `back/backend.log` (or wherever `uvicorn`'s stdout goes) never shows this request arriving at all.
4. `curl -H "Authorization: Bearer garbage.not.a.token" ...` — same expectation, rejected before FastAPI sees it.
5. Re-run the full login flow end to end (register → invite → login → dashboard) to make sure the three carved-out public routes still work with zero token.
6. Only after all of the above pass, consider this done — don't ship it on step 2 alone.

---

## Phase 3 — Stop the backend from being reachable around the gateway

**File:** `back/run.py`, line 51

**Current state:**
```python
p_backend = subprocess.Popen(
    [python_bin, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]
)
```

`0.0.0.0` means FastAPI listens on every network interface on the host — including whatever it's connected to beyond localhost. Anyone who can reach that machine on port `8001` at all (same LAN, a VPN, a misconfigured firewall) talks to FastAPI directly, skipping Nginx and APISIX — and Phases 1 and 2 above stop mattering the moment that happens, because they only guard the front door.

**Proposed change:**
```python
p_backend = subprocess.Popen(
    [python_bin, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8001", "--reload"]
)
```

**The thing to verify before trusting this one:** APISIX reaches FastAPI via `host.docker.internal:8001` (see `apisix.yaml`'s `upstream.nodes`), and `docker-compose.yml` adds `extra_hosts: host.docker.internal:host-gateway` for the `apisix` service. On Docker Desktop (which this almost certainly is, given the Windows paths involved), `host.docker.internal` is usually specially routed and *can* still reach a `127.0.0.1`-bound service on the host — but that's a "usually," not a guarantee, and the explicit `host-gateway` override is the kind of thing that can change that behavior. **Test this one directly rather than assuming either way:**

1. Make the change, restart with `python run.py`.
2. From inside the `apisix` container: `docker exec -it doumind-apisix wget -qO- http://host.docker.internal:8001/health` (or `curl` if available in that image). If it returns `{"status":"ok"}`, the binding change is safe here.
3. If that fails, don't force it — the fallback is to leave the bind as `0.0.0.0` and instead add a host-level firewall rule restricting inbound TCP `8001` to the Docker network range plus `localhost` only (exact steps depend on Windows Firewall vs. whatever you're running), which achieves the same goal without touching Docker's internal routing.
4. Once satisfied it isn't reachable from outside, confirm from a *different* machine on the same network that `http://<host-ip>:8001/health` no longer responds, while `http://<host-ip>:9080/...` still works normally through the gateway.

---

## Suggested order

1. Ship Phase 1 alone, watch it for a day or two of normal use.
2. Ship Phase 3 (it's independent of Phase 2 and lower-risk — just confirm the Docker networking test above passes first).
3. Ship Phase 2 last, since it's the one most likely to lock out real logins if the issuer/discovery config is wrong — have a rollback ready (comment the `openid-connect` block back out) before testing in anything other than local dev.
4. Separately, fix `PROJECT_UNDERSTANDING.md` §14.5 and the related line in `CLAUDE.md` §4 to reflect that the JWT-fallback issue is already resolved, per the correction at the top of this document.
