"""
Keycloak Admin REST API Synchronization Helper.

Automatically provisions and syncs newly registered/created users from SAMS
into the Keycloak realm so that users can seamlessly authenticate via both internal JWT
and Keycloak OIDC SSO.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8000")
KEYCLOAK_ADMIN = os.getenv("KEYCLOAK_ADMIN", "admin")
KEYCLOAK_ADMIN_PASSWORD = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "SAMS")


def sync_user_to_keycloak(
    email: str,
    password: str,
    role: str,
    tenant_id: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> bool:
    """Sync a user account to Keycloak realm via Admin REST API."""
    try:
        # 1. Obtain Keycloak Admin Access Token
        token_url = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
        data = urllib.parse.urlencode(
            {
                "client_id": "admin-cli",
                "username": KEYCLOAK_ADMIN,
                "password": KEYCLOAK_ADMIN_PASSWORD,
                "grant_type": "password",
            }
        ).encode()
        req = urllib.request.Request(token_url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            token_res = json.loads(resp.read().decode())
            admin_token = token_res["access_token"]

        headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}

        # 2. Search for user by email in Keycloak
        search_url = (
            f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users?email={urllib.parse.quote(email)}"
        )
        req = urllib.request.Request(search_url, headers=headers, method="GET")
        kc_user_id = None
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                users = json.loads(resp.read().decode())
                if users and isinstance(users, list) and len(users) > 0:
                    kc_user_id = users[0]["id"]
        except Exception:
            pass

        # 3. Create user if not existing in Keycloak
        if not kc_user_id:
            create_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users"
            user_payload = {
                "username": email,
                "email": email,
                "firstName": first_name or "",
                "lastName": last_name or "",
                "enabled": True,
                "emailVerified": True,
                "attributes": {"tenant_id": [tenant_id], "role": [role]},
                "credentials": [
                    {
                        "type": "password",
                        "value": password,
                        "temporary": False,
                    }
                ],
            }
            req = urllib.request.Request(
                create_url,
                data=json.dumps(user_payload).encode(),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                loc = resp.headers.get("Location")
                if loc:
                    kc_user_id = loc.split("/")[-1]

        # Always update user attributes (tenant_id and role)
        if kc_user_id:
            try:
                update_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users/{kc_user_id}"
                update_payload = {
                    "email": email,
                    "firstName": first_name or "",
                    "lastName": last_name or "",
                    "attributes": {"tenant_id": [tenant_id], "role": [role]},
                }
                req_up = urllib.request.Request(
                    update_url,
                    data=json.dumps(update_payload).encode(),
                    headers=headers,
                    method="PUT",
                )
                with urllib.request.urlopen(req_up, timeout=3):
                    pass
            except Exception as e:
                logger.warning(f"Could not update attributes for user {email}: {e}")

        # The `tenant_id` attribute written above does not actually persist: this
        # realm's declarative User Profile declares only username/email/firstName/
        # lastName and leaves unmanagedAttributePolicy unset, so Keycloak silently
        # drops it (verified -- 500 users in the live realm, none carrying it).
        # Organization membership is the durable form of the same fact, and it is
        # what reaches the token as the `organization` claim. The attribute write
        # is left in place for now only so nothing that still reads it regresses;
        # it should be deleted once membership is the sole source.
        if tenant_id:
            add_user_to_organization(email, tenant_id)

        return True
    except Exception as exc:
        logger.warning(f"Keycloak user sync skipped for {email}: {exc}")
        return False


def update_user_role_in_keycloak(email: str, new_role: str, tenant_id: str) -> bool:
    """Update an existing user's role and tenant_id in Keycloak."""
    try:
        # 1. Obtain Keycloak Admin Access Token
        token_url = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
        data = urllib.parse.urlencode(
            {
                "client_id": "admin-cli",
                "username": KEYCLOAK_ADMIN,
                "password": KEYCLOAK_ADMIN_PASSWORD,
                "grant_type": "password",
            }
        ).encode()
        req = urllib.request.Request(token_url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            token_res = json.loads(resp.read().decode())
            admin_token = token_res["access_token"]

        headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}

        # 2. Search for user by email in Keycloak
        search_url = (
            f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users?email={urllib.parse.quote(email)}"
        )
        req = urllib.request.Request(search_url, headers=headers, method="GET")
        kc_user_id = None
        with urllib.request.urlopen(req, timeout=3) as resp:
            users = json.loads(resp.read().decode())
            if users and isinstance(users, list) and len(users) > 0:
                kc_user_id = users[0]["id"]
                user_obj = users[0]

        if not kc_user_id:
            logger.warning(f"Cannot update role: User {email} not found in Keycloak.")
            return False

        # 3. Update user attributes
        update_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users/{kc_user_id}"
        update_payload = {
            "email": email,
            "firstName": user_obj.get("firstName", ""),
            "lastName": user_obj.get("lastName", ""),
            "attributes": {"tenant_id": [tenant_id], "role": [new_role]},
        }
        req_up = urllib.request.Request(
            update_url, data=json.dumps(update_payload).encode(), headers=headers, method="PUT"
        )
        with urllib.request.urlopen(req_up, timeout=3):
            pass

        # 4. Map new role
        return True
    except Exception as exc:
        logger.warning(f"Keycloak role update failed for {email}: {exc}")
        return False


def delete_user_from_keycloak(email: str) -> bool:
    """Delete a user's Keycloak account(s) by email, best-effort.

    Called when a tenant admin hard-deletes a user, so the account can't
    silently reappear via Keycloak SSO's JIT re-provisioning on next login.
    Deletes every Keycloak user matching this email exactly, not just the
    first search result — this codebase has two independent Keycloak
    provisioning paths (this module's sync_user_to_keycloak, used at
    registration, and invitations/service.py's own separate client, used at
    invite time), and each does its own find-or-create against the same
    email; if both ever ran for the same address, two Keycloak users can
    exist for one local account, and deleting only the first left the
    second visible in Keycloak after "deletion." Uses exact=true so a
    substring match against another email doesn't get swept up too.
    Returns False (never raises) on any failure — callers treat this as
    non-blocking, matching sync_user_to_keycloak's existing convention.
    """
    try:
        token_url = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
        data = urllib.parse.urlencode(
            {
                "client_id": "admin-cli",
                "username": KEYCLOAK_ADMIN,
                "password": KEYCLOAK_ADMIN_PASSWORD,
                "grant_type": "password",
            }
        ).encode()
        req = urllib.request.Request(token_url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            token_res = json.loads(resp.read().decode())
            admin_token = token_res["access_token"]

        headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}

        search_url = (
            f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users"
            f"?email={urllib.parse.quote(email)}&exact=true"
        )
        req = urllib.request.Request(search_url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            users = json.loads(resp.read().decode())

        kc_user_ids = (
            [u["id"] for u in users if isinstance(u, dict) and u.get("id")]
            if isinstance(users, list)
            else []
        )

        if not kc_user_ids:
            logger.warning(f"Keycloak user deletion: no matching user found for {email}")
            return False

        all_deleted = True
        for kc_user_id in kc_user_ids:
            delete_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users/{kc_user_id}"
            req_del = urllib.request.Request(delete_url, headers=headers, method="DELETE")
            try:
                with urllib.request.urlopen(req_del, timeout=3):
                    pass
            except Exception as exc:
                all_deleted = False
                logger.warning(
                    f"Keycloak user deletion failed for {email} (id={kc_user_id}): {exc}"
                )

        return all_deleted
    except Exception as exc:
        logger.warning(f"Keycloak user deletion failed for {email}: {exc}")
        return False


def ensure_keycloak_frontend_redirect_uris():
    """Ensure Keycloak 'frontend' client has all valid redirect URIs for ports 3000, 9080, 8000."""
    try:
        token_url = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
        data = urllib.parse.urlencode(
            {
                "client_id": "admin-cli",
                "username": KEYCLOAK_ADMIN,
                "password": KEYCLOAK_ADMIN_PASSWORD,
                "grant_type": "password",
            }
        ).encode()
        req = urllib.request.Request(token_url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            token_res = json.loads(resp.read().decode())
            admin_token = token_res["access_token"]

        headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}

        # Search for frontend client
        req_c = urllib.request.Request(
            f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/clients?clientId=frontend",
            headers=headers,
        )
        with urllib.request.urlopen(req_c, timeout=3) as resp_c:
            clients = json.loads(resp_c.read().decode())
            if clients and isinstance(clients, list) and len(clients) > 0:
                client = clients[0]
                c_id = client["id"]
                current_uris = client.get("redirectUris", [])
                needed_uris = [
                    "http://localhost:5173",
                    "http://localhost:5173/",
                    "http://localhost:5173/*",
                    "http://127.0.0.1:5173",
                    "http://127.0.0.1:5173/",
                    "http://127.0.0.1:5173/*",
                    "http://localhost:5174",
                    "http://localhost:5174/",
                    "http://localhost:5174/*",
                    "http://127.0.0.1:5174",
                    "http://127.0.0.1:5174/",
                    "http://127.0.0.1:5174/*",
                    "http://localhost:3000",
                    "http://localhost:3000/",
                    "http://localhost:3000/*",
                    "http://127.0.0.1:3000",
                    "http://127.0.0.1:3000/",
                    "http://127.0.0.1:3000/*",
                    "http://localhost:9080",
                    "http://localhost:9080/",
                    "http://localhost:9080/*",
                    "http://127.0.0.1:9080",
                    "http://127.0.0.1:9080/",
                    "http://127.0.0.1:9080/*",
                    "http://localhost:8000",
                    "http://localhost:8000/",
                    "http://localhost:8000/*",
                    "http://127.0.0.1:8000",
                    "http://127.0.0.1:8000/",
                    "http://127.0.0.1:8000/*",
                    "http://localhost:*",
                    "http://localhost:*/*",
                    "http://localhost:*?*",
                    "http://localhost:*/*?*",
                    "http://127.0.0.1:*",
                    "http://127.0.0.1:*/*",
                    "http://127.0.0.1:*?*",
                    "http://127.0.0.1:*/*?*",
                    "*",
                ]
                updated_uris = list(set(current_uris + needed_uris))
                if set(updated_uris) != set(current_uris):
                    client["redirectUris"] = updated_uris
                    req_up = urllib.request.Request(
                        f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/clients/{c_id}",
                        data=json.dumps(client).encode(),
                        headers=headers,
                        method="PUT",
                    )
                    with urllib.request.urlopen(req_up, timeout=3):
                        logger.info(
                            "Successfully updated Keycloak frontend client valid redirect URIs."
                        )
    except Exception as exc:
        logger.warning(f"Could not update Keycloak frontend client redirect URIs: {exc}")


# =============================================================================
# Organizations — the identity-side mirror of a tenant
#
# One Keycloak Organization per tenant, with `alias` == `tenant_id`. The alias is
# what Keycloak emits in the `organization` claim, so it is the value the backend
# resolves a tenant from; `name` is display-only and must never be relied on.
#
# These live here rather than in SAMS-realm.json because organizations do NOT
# round-trip through realm export/import -- `POST /admin/realms/{realm}/partial-export`
# returns no `organizations` key at all (verified against Keycloak 26.7). The realm
# file therefore cannot carry them, and provisioning has to go through the Admin
# REST API. ensure_keycloak_frontend_redirect_uris() above is the existing
# precedent for "reconcile realm config at startup".
# =============================================================================


class KeycloakOrganizationError(RuntimeError):
    """An organization could not be provisioned in Keycloak.

    Raised rather than logged because a tenant whose organization is missing is a
    school nobody can be resolved into: its users would authenticate fine and then
    fail tenant resolution on every request.
    """


def _admin_token(timeout: int = 5) -> str:
    """Fetch a master-realm admin token.

    NOTE: this repeats what sync_user_to_keycloak / update_user_role_in_keycloak /
    delete_user_from_keycloak / ensure_keycloak_frontend_redirect_uris each do
    inline. It is factored out here so the organization helpers below share one
    copy; the older functions are deliberately left alone to keep this change
    reviewable. Collapsing all five onto this helper is worth doing separately.
    """
    data = urllib.parse.urlencode(
        {
            "client_id": "admin-cli",
            "username": KEYCLOAK_ADMIN,
            "password": KEYCLOAK_ADMIN_PASSWORD,
            "grant_type": "password",
        }
    ).encode()
    req = urllib.request.Request(
        f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        data=data,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())["access_token"]


def _list_organizations(headers: dict, timeout: int = 5) -> list[dict]:
    """Every organization in the realm.

    `first`/`max` are passed explicitly: several Keycloak admin list endpoints
    default to a small page, and a truncated list would make the idempotency check
    below think an existing org is missing and try to re-create it.
    """
    req = urllib.request.Request(
        f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/organizations?first=0&max=1000",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode())
    return body if isinstance(body, list) else []


def find_organization_by_alias(alias: str, headers: dict | None = None) -> dict | None:
    """Look up one organization by EXACT alias.

    Exact-match only, deliberately. The lookup this replaces in
    invitations/service.py also accepted `tenant_id in alias`, which would match
    `tenant_a` against an org aliased `tenant_a_backup` or `xtenant_ab` and hand a
    user membership of the wrong school.
    """
    if not alias:
        return None
    if headers is None:
        headers = {
            "Authorization": f"Bearer {_admin_token()}",
            "Content-Type": "application/json",
        }
    target = alias.strip().lower()
    for org in _list_organizations(headers):
        if str(org.get("alias") or "").strip().lower() == target:
            return org
    return None


def create_keycloak_organization(
    tenant_id: str,
    name: str | None = None,
    domains: list[str] | None = None,
) -> dict:
    """Create the Keycloak Organization backing one tenant, if it is not there yet.

    Returns the organization dict (existing or newly created). Raises
    KeycloakOrganizationError on failure so the caller can refuse to leave a tenant
    half-provisioned.

    `domains` defaults to None on purpose. An email domain on an organization is a
    discovery/join surface, and this realm currently has `registrationAllowed: true`
    -- attaching `schoola.com` to tenant_a would make "anyone who can register with
    a schoola.com address" a path toward that school's organization. Pass domains
    explicitly once registration is locked down.
    """
    tid = (tenant_id or "").strip()
    if not tid:
        raise KeycloakOrganizationError("tenant_id is required to create an organization")

    try:
        headers = {
            "Authorization": f"Bearer {_admin_token()}",
            "Content-Type": "application/json",
        }
    except Exception as exc:
        raise KeycloakOrganizationError(
            f"Could not obtain a Keycloak admin token to provision organization '{tid}': {exc}"
        ) from exc

    existing = find_organization_by_alias(tid, headers)
    if existing:
        return existing

    payload = {
        "name": name or tid,
        "alias": tid,
        "enabled": True,
        "description": f"SAMS tenant {tid}",
    }
    if domains:
        payload["domains"] = [{"name": d, "verified": False} for d in domains]

    try:
        req = urllib.request.Request(
            f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/organizations",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except urllib.error.HTTPError as exc:
        if exc.code != 409:
            detail = exc.read().decode()[:300]
            raise KeycloakOrganizationError(
                f"Keycloak refused to create organization '{tid}': HTTP {exc.code} {detail}"
            ) from exc
        # A 409 here has two different causes that must not be conflated:
        #  (a) our own alias was created by a concurrent request between the
        #      lookup above and this POST -- genuinely idempotent, the
        #      immediate re-check below will find it by alias; or
        #  (b) Keycloak enforces a realm-wide UNIQUE organization `name`
        #      (independent of alias) -- a DIFFERENT tenant already owns
        #      this display name. Re-running onboarding with a repeated
        #      school name (a small word list, or a demo re-run) hits this
        #      routinely. Treating every 409 as idempotent success used to
        #      silently swallow case (b), which then failed the alias
        #      readback below for a tenant that was never actually created
        #      -- surfacing as a confusing "not readable back" error instead
        #      of the real "that name is taken" one.
        by_alias = find_organization_by_alias(tid, headers)
        if by_alias:
            return by_alias
        detail = exc.read().decode()[:300]
        raise KeycloakOrganizationError(
            f"Cannot create organization '{tid}': the name '{payload['name']}' is "
            f"already used by a different tenant in Keycloak. Detail: {detail}"
        ) from exc
    except Exception as exc:
        raise KeycloakOrganizationError(
            f"Could not create Keycloak organization '{tid}': {exc}"
        ) from exc

    # A brief allowance for the list endpoint to catch up with the write we
    # just made -- this runs once per tenant (school onboarding), so it's
    # cheap to wait a moment rather than fail a real, successful creation.
    created = None
    for attempt in range(3):
        created = find_organization_by_alias(tid, headers)
        if created:
            break
        if attempt < 2:
            time.sleep(0.5)
    if not created:
        raise KeycloakOrganizationError(
            f"Organization '{tid}' was not readable back after creation"
        )
    logger.info(f"Created Keycloak organization '{tid}'.")
    return created


def add_user_to_organization(email: str, tenant_id: str) -> bool:
    """Make `email` a member of the organization aliased `tenant_id`.

    Organization membership is what puts the `organization` claim in the user's
    token, and that claim is how a request resolves to a tenant. It replaces the
    `tenant_id` user attribute, which never worked: this realm's declarative User
    Profile declares only username/email/firstName/lastName and leaves
    `unmanagedAttributePolicy` unset, so Keycloak SILENTLY DISCARDS any
    `tenant_id`/`role` attribute written by sync_user_to_keycloak or
    _find_or_create_user_shell. Verified against the live realm: 500 users, zero
    carrying a tenant_id attribute.

    Returns True if the user is a member afterwards. Non-fatal: callers treat this
    as best-effort, because control-plane `user_tenant_map` is still a working
    resolution path while membership is being rolled out.
    """
    if not email or not tenant_id:
        return False
    try:
        headers = {
            "Authorization": f"Bearer {_admin_token()}",
            "Content-Type": "application/json",
        }

        org = find_organization_by_alias(tenant_id, headers)
        if not org:
            logger.warning(f"No Keycloak organization aliased '{tenant_id}'; cannot add {email}.")
            return False

        lookup = urllib.request.Request(
            f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users"
            f"?email={urllib.parse.quote(email)}&exact=true",
            headers=headers,
        )
        with urllib.request.urlopen(lookup, timeout=5) as resp:
            found = json.loads(resp.read().decode())
        if not found:
            logger.warning(f"No Keycloak user for '{email}'; cannot add to '{tenant_id}'.")
            return False
        kc_user_id = found[0]["id"]

        try:
            add_req = urllib.request.Request(
                f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/organizations/{org['id']}/members",
                # The bare user id as a JSON string is the shape this endpoint
                # takes -- verified 201 against Keycloak 26.7.
                data=json.dumps(kc_user_id).encode(),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(add_req, timeout=5):
                return True
        except urllib.error.HTTPError as exc:
            # 409 = already a member, which is the desired end state.
            if exc.code == 409:
                return True
            raise
    except Exception as exc:
        logger.warning(f"Could not add '{email}' to Keycloak organization '{tenant_id}': {exc}")
    return False


def ensure_keycloak_organizations(tenant_ids) -> dict:
    """Reconcile: every tenant id given has an organization aliased to it.

    Best-effort and non-fatal per tenant -- this runs at application startup and one
    unreachable organization must not stop the API from booting. Callers that need a
    hard guarantee (tenant creation) should use create_keycloak_organization, which
    raises. Returns a summary suitable for logging.
    """
    summary = {"existing": [], "created": [], "failed": {}}
    ids = [str(t).strip() for t in (tenant_ids or []) if str(t).strip()]
    if not ids:
        return summary

    try:
        headers = {
            "Authorization": f"Bearer {_admin_token()}",
            "Content-Type": "application/json",
        }
        present = {str(o.get("alias") or "").strip().lower() for o in _list_organizations(headers)}
    except Exception as exc:
        logger.warning(f"Could not reconcile Keycloak organizations: {exc}")
        summary["failed"] = {t: str(exc) for t in ids}
        return summary

    for tid in ids:
        if tid.lower() in present:
            summary["existing"].append(tid)
            continue
        try:
            create_keycloak_organization(tid)
            summary["created"].append(tid)
        except KeycloakOrganizationError as exc:
            logger.warning(f"Could not provision organization for tenant '{tid}': {exc}")
            summary["failed"][tid] = str(exc)

    if summary["created"] or summary["failed"]:
        logger.info(
            "Keycloak organization reconcile: "
            f"{len(summary['existing'])} existing, {len(summary['created'])} created, "
            f"{len(summary['failed'])} failed."
        )
    return summary
