"""
Shared FastAPI dependencies.

Extracts current user context from JWT tokens and performs role-based authorization guards.
"""

import json
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.keycloak_jwt import KeycloakTokenError, verify_keycloak_token
from app.domains.auth.service import AuthService

_security = HTTPBearer(auto_error=False)


class RoleList(list):
    """Subclass of list that implements the set-like intersection method."""

    def intersection(self, other):
        return set(self).intersection(other)


class CurrentUser:
    """Value object representing an authenticated user extracted from the JWT."""

    def __init__(
        self,
        user_id: str,
        tenant_id: str | None,
        role: str,
        email: str = "",
        roles: list[str] | None = None,
    ) -> None:
        self.id = user_id
        self.tenant_id = tenant_id
        self.role = role
        self.email = email
        self.roles = RoleList(roles or [role])

    def has_role(self, role_name: str) -> bool:
        """Check if user has a specific role or permission."""
        roles_set = set(self.roles)
        if self.role:
            roles_set.add(self.role)

        if "super_admin" in roles_set or "*" in roles_set:
            return True

        if role_name in ("admin", "school_admin") and (
            "school_admin" in roles_set or "admin" in roles_set
        ):
            return True

        if role_name in roles_set:
            return True

        for r in list(roles_set):
            if r in COMPOSITE_ROLE_PERMISSIONS and role_name in COMPOSITE_ROLE_PERMISSIONS[r]:
                return True

        return False

    def has_any_role(self, *role_names: str) -> bool:
        """Check if user has any of the specified roles."""
        return any(self.has_role(r) for r in role_names)

    async def can(self, action: str, resource: dict | None = None) -> bool:
        """Verify action authorization against OPA.

        An explicit decision from a reachable OPA is final — including a
        deny — and is never second-guessed by the local has_role() check.
        The local check only kicks in when OPA itself couldn't be reached
        (OPAUnavailableError), as a resilience measure against an OPA outage,
        not as a way to override a real policy decision.
        """
        from app.core.opa import OPAUnavailableError, verify_opa_authorization

        try:
            return await verify_opa_authorization(
                user_id=str(self.id or ""),
                tenant_id=str(self.tenant_id or ""),
                roles=list(self.roles),
                action=action,
                resource=resource,
            )
        except OPAUnavailableError:
            return self.has_role(action)


COMPOSITE_ROLE_PERMISSIONS: dict[str, set[str]] = {
    role: set(perms)
    for role, perms in json.loads(
        (Path(__file__).parent / "permissions_catalog.json").read_text(encoding="utf-8")
    ).items()
}


# Role and permission tokens accepted off a JWT. Anything absent here is
# silently dropped from the caller's role list -- so a permission missing from
# this set can be granted in the admin UI, stored on the user row, baked into
# the login token, and still authorize nothing (that is exactly how
# `event:submit` came to be ungrantable). It is therefore DERIVED from
# COMPOSITE_ROLE_PERMISSIONS -- the catalog every role is built from -- rather
# than hand-listed, so the two cannot drift apart again. `_EXTRA_PERMISSIONS`
# holds the tokens that are legitimate on a token but belong to no composite
# role. This set is a typo guard, not a security boundary: the only issuer of
# these claims is our own login path reading the user's own roles/permissions
# columns (Keycloak tokens contribute no roles at all -- see get_current_user),
# and the JWT signature is what makes that trustworthy.
_ROLE_NAMES: set[str] = {
    "super_admin",
    "school_admin",
    "admin",
    "administrator",
    "manager",
    "teacher",
    "event_teacher",
    "parent",
    "student",
    "pending",
}

_EXTRA_PERMISSIONS: set[str] = {
    "system:write",
    "system:read",
    "tenant:manage",
    "tenant:view",
    "academic:direct",
    "academic:view",
    "content:create",
    "content:publish",
}

VALID_ROLES: set[str] = (
    _ROLE_NAMES
    | _EXTRA_PERMISSIONS
    | {p for perms in COMPOSITE_ROLE_PERMISSIONS.values() for p in perms if ":" in p}
)


def require_permission(action: str, resource: dict | None = None):
    """FastAPI dependency guard verifying that current_user can perform `action` via OPA.

    The resource sent to OPA always includes the caller's own tenant_id
    (merged with any static `resource` fields given here), so the policy's
    tenant-isolation check (valid_tenant) has a real value to compare
    against — omitting it would make every call hit the "missing
    tenant_id" case, which the policy denies by default. Actions that need
    per-request resource data (e.g. an event's current status, fetched
    from the DB) aren't a fit for this static form — call
    `current_user.can(action, resource=...)` directly in the route body
    instead.
    """

    async def _guard(current_user: CurrentUser = Depends(get_current_user)):
        merged_resource = {"tenant_id": current_user.tenant_id, **(resource or {})}
        allowed = await current_user.can(action, resource=merged_resource)
        if not allowed:
            raise HTTPException(status_code=403, detail=f"Permission denied for action '{action}'")
        return current_user

    return _guard


async def get_current_user(
    request: Request = None,
    credentials: HTTPAuthorizationCredentials = Depends(_security),
) -> CurrentUser:
    """Decode the Bearer JWT and return a CurrentUser object.

    Supports both internal SAMS JWTs and Keycloak OIDC tokens passed via APISIX.
    Raises HTTP 401 if the token is missing or invalid.
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authentication token")

    token = credentials.credentials

    # First attempt: Try standard internal JWT verification
    payload = AuthService.decode_access_token(token)

    # Fallback attempt: Keycloak OIDC tokens, verified here against the
    # realm's cached JWKS (signature, exp, iss, aud). APISIX does NOT
    # validate tokens upstream — see gateway/apisix/apisix.yaml — so this is
    # the only place a Keycloak-issued token is authenticated. Any failure
    # raises KeycloakTokenError; there is no further fallback path.
    is_keycloak = False
    if not payload:
        try:
            payload = await verify_keycloak_token(token)
            is_keycloak = True
        except KeycloakTokenError:
            payload = None

    if not payload:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
        )

    # Extract claims cleanly whether from Keycloak or internal JWT
    user_id = payload.get("sub")
    email = payload.get("email") or payload.get("preferred_username", "")

    # Keycloak is strictly for Authentication (AuthN). We DO NOT extract authorization roles from Keycloak tokens.
    # We only check for roles if they were embedded by our own local AuthService.
    if is_keycloak:
        single_role = None
        payload_roles = []
    else:
        single_role = payload.get("role")
        payload_roles = payload.get("roles", [])

    role = None
    if single_role:
        clean_sr = single_role.lower().strip()
        if clean_sr in ("admin", "administrator", "school_admin"):
            role = "school_admin"
        elif clean_sr in VALID_ROLES:
            role = clean_sr

    if not role:
        role = "pending"

    extracted_roles = set(r for r in payload_roles if r in VALID_ROLES)
    extracted_roles.add(role)

    if role == "school_admin" or "school_admin" in extracted_roles:
        extracted_roles.add("admin")
    elif role == "admin" or "admin" in extracted_roles:
        extracted_roles.add("school_admin")

    # Expand composite role permissions
    for r in list(extracted_roles):
        if r in COMPOSITE_ROLE_PERMISSIONS:
            extracted_roles.update(COMPOSITE_ROLE_PERMISSIONS[r])

    final_roles_list = list(extracted_roles)

    # Recognize a control-plane super_admin authenticating via Keycloak.
    # Keycloak tokens never carry our roles (by design, above), so without
    # this check a super_admin logging in via Keycloak SSO falls through
    # every path below as an unrecognized "pending" user, defaults to
    # tenant_a, and gets a bogus JIT-provisioned `users` row created there.
    # The local-password login path (AuthService.login_user) already checks
    # `super_admins` first and never reaches this function's fallback logic,
    # which is why this only surfaces for Keycloak SSO.
    if role == "pending" and email:
        try:
            from app.core.database import get_control_plane_pool

            cp_pool = await get_control_plane_pool()
            is_super_admin_email = await cp_pool.fetchval(
                "SELECT 1 FROM super_admins WHERE UPPER(email) = UPPER($1)", email
            )
            if is_super_admin_email:
                role = "super_admin"
                extracted_roles.discard("pending")
                extracted_roles.add("super_admin")
                if "super_admin" in COMPOSITE_ROLE_PERMISSIONS:
                    extracted_roles.update(COMPOSITE_ROLE_PERMISSIONS["super_admin"])
                final_roles_list = list(extracted_roles)
        except Exception as _e:
            print(f"[get_current_user] Warning: could not check super_admins for '{email}': {_e}")

    tenant_id = payload.get("tenant_id")

    # Keycloak Organization membership -> tenant. The organization alias is the
    # tenant_id by construction (see keycloak_admin.create_keycloak_organization).
    #
    # The claim is a flat ARRAY of aliases -- verified against Keycloak 26.7 with
    # scope=organization:* -- and its order is HashSet iteration over organization
    # UUIDs, so it is neither stable nor meaningful. Exactly one membership
    # resolves directly; SEVERAL memberships deliberately resolve to nothing here,
    # leaving the caller to be resolved by user_tenant_map (or, for a super_admin,
    # by the X-Tenant-ID selection below).
    #
    # This previously did `org[0]`, which picked an arbitrary school for any
    # multi-school user -- the exact failure the unordered claim invites.
    org_aliases: list[str] = []
    org = payload.get("organization") or payload.get("org") or payload.get("organizations")
    if isinstance(org, dict):
        org_aliases = [str(k) for k in org]
    elif isinstance(org, list):
        for item in org:
            if isinstance(item, dict):
                val = item.get("alias") or item.get("name") or item.get("id")
                if val:
                    org_aliases.append(str(val))
            elif isinstance(item, str) and item.strip():
                org_aliases.append(item.strip())
    elif isinstance(org, str) and org.strip():
        org_aliases = [org.strip()]

    if not tenant_id and len(org_aliases) == 1:
        tenant_id = org_aliases[0]

    # Try User Attributes claim
    if not tenant_id:
        attrs = payload.get("attributes", {})
        if isinstance(attrs, dict) and "tenant_id" in attrs:
            val = attrs["tenant_id"]
            tenant_id = val[0] if isinstance(val, list) and val else str(val)

    # Try Groups path claim (e.g., /tenant_b/Teachers)
    if not tenant_id:
        raw_groups = payload.get("groups", [])
        if isinstance(raw_groups, list):
            for g in raw_groups:
                parts = [p.strip() for p in str(g).split("/") if p.strip()]
                for p in parts:
                    if p.startswith("tenant_"):
                        tenant_id = p
                        break
                if tenant_id:
                    break

    # Fallback for Realm-per-tenant architecture
    if not tenant_id:
        iss = payload.get("iss", "")
        if "/realms/" in iss:
            realm = iss.split("/realms/")[-1]
            if realm.lower() not in ("schooldesk", "master", "sams"):
                tenant_id = realm

    # ── Last resort: look up email → tenant from control-plane user_tenant_map ──
    # This is the primary resolution path for Keycloak SSO users whose token
    # does not carry a tenant_id claim, OR does not carry a role claim.
    if (
        not tenant_id
        or tenant_id.lower() in ("sams", "schooldesk", "master")
        or role in ("student", "pending")
    ) and email:
        try:
            from app.core.database import get_control_plane_pool
            from app.domains.tenant.control_plane_repository import ControlPlaneRepository

            cp_pool = await get_control_plane_pool()
            cp_repo = ControlPlaneRepository(cp_pool)
            mapping = await cp_repo.get_tenant_for_email(email)
            if mapping:
                tenant_id = mapping["tenant_id"]
                # If role wasn't established from token, use the stored role
                if not role or role in ("student", "pending"):
                    stored_role = mapping.get("role")
                    if stored_role and stored_role in VALID_ROLES:
                        role = stored_role
                        extracted_roles.add(role)
                        # Expand composite permissions for the resolved role
                        if role in COMPOSITE_ROLE_PERMISSIONS:
                            extracted_roles.update(COMPOSITE_ROLE_PERMISSIONS[role])
                        final_roles_list = list(extracted_roles)

            if (not mapping or not role or role in ("student", "pending")) and email:
                async with cp_pool.acquire() as cp_conn:
                    inv_row = await cp_conn.fetchrow(
                        "SELECT tenant_id, role FROM user_invitations WHERE UPPER(email) = UPPER($1) ORDER BY created_at DESC LIMIT 1",
                        email,
                    )
                    if not inv_row:
                        inv_row = await cp_conn.fetchrow(
                            "SELECT tenant_id, role FROM invitations WHERE UPPER(target_email) = UPPER($1) ORDER BY created_at DESC LIMIT 1",
                            email,
                        )
                    if inv_row and inv_row.get("tenant_id") and inv_row.get("role"):
                        tenant_id = inv_row["tenant_id"]
                        role = inv_row["role"]
                        extracted_roles.add(role)
                        if role in COMPOSITE_ROLE_PERMISSIONS:
                            extracted_roles.update(COMPOSITE_ROLE_PERMISSIONS[role])
                        final_roles_list = list(extracted_roles)
                        await cp_repo.upsert_user_tenant_map(email, tenant_id, role)
        except Exception as _e:
            print(
                f"[get_current_user] Warning: could not resolve tenant from control plane for '{email}': {_e}"
            )

    # X-Tenant-ID header / ?tenant_id= query param override — super_admin ONLY.
    # This lets a platform operator switch which tenant they're inspecting; it
    # must NEVER apply to an ordinary school_admin/teacher/parent/student,
    # since a client fully controls its own request headers — trusting this
    # for anyone else would let any authenticated user read or write any
    # other tenant's data just by sending a header.
    is_super_admin = role == "super_admin" or "super_admin" in extracted_roles
    if request and is_super_admin:
        req_tenant = request.headers.get("x-tenant-id") or request.query_params.get("tenant_id")
        if req_tenant and req_tenant.strip():
            tenant_id = req_tenant.strip().lower()
            # A vendor-side account reading/writing a specific school's data via
            # this override is exactly the "who looked at my tenant's data"
            # question a school should be able to ask -- audit it. Non-fatal
            # (an audit outage must not block a legitimate super_admin request)
            # and written into the TARGET tenant's own audit_log, not a
            # control-plane table, so the affected school can see it directly.
            try:
                from app.core.database import get_db_pool
                from app.domains.audit.service import AuditService

                class _OverrideActor:
                    id = user_id
                    role = "super_admin"
                    email = email

                async with (await get_db_pool(tenant_id)).acquire() as audit_conn:
                    await AuditService.record(
                        audit_conn,
                        actor=_OverrideActor(),
                        action="tenant_override.cross_tenant_access",
                        entity_type="tenant",
                        entity_id=tenant_id,
                        outcome="allow",
                        metadata={"target_tenant_id": tenant_id},
                    )
            except Exception:
                pass

    # Last-ditch cross-tenant scan. Every prior resolution step depends on
    # user_tenant_map already having a row for this email; if it doesn't (a first
    # Keycloak login racing the map, or a user created by some path that never
    # wrote one), resolution used to fall through to a hardcoded tenant_a and
    # JIT-provision a brand new 'pending' user there -- even when a real,
    # fully-roled account for this exact email already existed in a different
    # tenant's schema. That is the mechanism behind every stray 'pending in the
    # wrong tenant' user found in this codebase so far (student.11@tenantb.com,
    # student.12@tenantb.com -- see PROJECT_UNDERSTANDING.md drift log). Mirrors
    # the same fallback AuthService.login_user does for the password-login path.
    #
    # This is now a SELF-HEAL for accounts that predate organization membership,
    # not a resolution step: the tenant_a default it used to protect against is
    # gone (see below), so without it those users would get a hard 400 rather
    # than landing in the wrong school. It should be deleted once every user has
    # an `organization` claim -- it opens a pool per tenant and is a
    # tenant-enumeration primitive.
    if (
        (not tenant_id or tenant_id.lower() in ("sams", "schooldesk", "master"))
        and email
        and not is_super_admin
    ):
        try:
            from app.core.database import get_db_pool as _get_db_pool
            from app.domains.tenant.control_plane_repository import (
                ControlPlaneRepository as _CpRepo,
            )

            cp_pool = await get_control_plane_pool()
            cp_repo = _CpRepo(cp_pool)
            all_tenants = await cp_repo.get_all_tenants()
            for t in all_tenants:
                tid = t.get("tenant_id") or t.get("id")
                if not tid:
                    continue
                try:
                    t_pool = await _get_db_pool(tid)
                    found = await t_pool.fetchrow(
                        "SELECT role FROM users WHERE UPPER(email) = UPPER($1)", email.strip()
                    )
                except Exception:
                    continue
                if found:
                    tenant_id = tid
                    found_role = found.get("role")
                    if found_role and found_role not in ("pending", "none", "unassigned"):
                        role = found_role
                        extracted_roles.add(found_role)
                    # Self-heal: this is exactly the row that was missing.
                    await cp_repo.upsert_user_tenant_map(email, tenant_id, role or "pending")
                    break
        except Exception as _e:
            print(f"[get_current_user] Warning: cross-tenant scan for '{email}' failed: {_e}")

    # FAIL CLOSED. This used to read `tenant_id = "tenant_a"`.
    #
    # tenant_a is not a neutral placeholder -- it is the first real school. So any
    # token whose tenant could not be resolved (a misconfigured Keycloak client, a
    # realm named sams/schooldesk/master, a user with no user_tenant_map row and no
    # organization membership) silently READ AND WROTE that school's data, and the
    # JIT-provisioning block below would then create a 'pending' user inside it.
    # An unresolvable tenant is now an error, never a guess.
    if not tenant_id or tenant_id.lower() in ("sams", "schooldesk", "master"):
        if is_super_admin:
            # A super_admin genuinely has no home tenant: AuthService.login_user
            # mints their token with tenant_id="". They choose one per request via
            # X-Tenant-ID (handled above, and restricted to super_admin). Leaving
            # this None keeps cross-tenant endpoints like analytics working, while
            # tenant-scoped endpoints fail cleanly instead of defaulting into an
            # arbitrary school. CurrentUser.tenant_id is typed `str | None`.
            tenant_id = None
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Your account is not associated with a school. Ask an "
                    "administrator to invite you to one."
                ),
            )

    # Resolve local database user ID, dynamic roles, and custom permissions for
    # school roles.
    #
    # `and tenant_id` guards the super_admin-with-no-selection case: tenant_id is
    # deliberately None there, and get_db_pool(None) would raise instead of
    # producing the clean "pick a school" behaviour that None is meant to express.
    # Everyone else is guaranteed a tenant_id by the fail-closed check above.
    if email and tenant_id:
        try:
            from app.core.database import get_db_pool

            pool = await get_db_pool(tenant_id)
            async with pool.acquire() as conn:
                user_row = await conn.fetchrow(
                    """
                    SELECT id, role, 
                           COALESCE(roles, ARRAY[]::TEXT[]) as roles,
                           COALESCE(permissions, ARRAY[]::TEXT[]) as permissions
                    FROM users 
                    WHERE UPPER(email) = UPPER($1)
                    """,
                    email.strip(),
                )
                if user_row:
                    user_id = str(user_row["id"])
                    db_role = user_row.get("role")
                    # A super_admin has no real account in most tenants (see
                    # the JIT-provisioning branch below), but if one somehow
                    # already has a stray/legacy tenant-scoped `users` row
                    # with role='pending' -- e.g. left over from before this
                    # function recognized super_admins on the Keycloak path
                    # -- that row must never downgrade a real super_admin
                    # back to pending. super_admin overrides every other
                    # workflow/role check in this codebase; this is that
                    # same invariant applied here.
                    if db_role in ("pending", "none", "unassigned") and not is_super_admin:
                        role = db_role
                        extracted_roles = {"pending"}
                    else:
                        if db_role and db_role in VALID_ROLES:
                            extracted_roles.add(db_role)
                            if not role or role in ("student", "pending"):
                                role = db_role

                        db_roles = user_row.get("roles") or []
                        for r in db_roles:
                            if (
                                r
                                and r in VALID_ROLES
                                and r not in ("pending", "none", "unassigned")
                            ):
                                extracted_roles.add(r)

                        # Prevent Keycloak's default 'student' role from bleeding into other roles (like parent/teacher)
                        if (
                            "student" in extracted_roles
                            and db_role != "student"
                            and "student" not in db_roles
                        ):
                            extracted_roles.remove("student")

                        for p in user_row.get("permissions") or []:
                            if p:
                                extracted_roles.add(p)
                elif is_super_admin:
                    # A super_admin has no real account in most tenants — they
                    # only ever pass through here because of the X-Tenant-ID
                    # override used to inspect/manage other schools. Every
                    # permission check for super_admin already short-circuits
                    # on role/extracted_roles elsewhere in this codebase, so
                    # there is nothing to gain by JIT-provisioning a phantom
                    # local `users` row for them in every tenant they visit —
                    # and doing so was surprising tenant admins who found a
                    # "user I never created" sitting in their school.
                    pass
                else:
                    # JIT Auto-provision missing Keycloak user locally.
                    # ON CONFLICT (email) DO NOTHING + fallback SELECT makes
                    # this atomic: the preceding SELECT above and this INSERT
                    # are two separate statements, so a concurrent request for
                    # the same brand-new email could otherwise race here. The
                    # unique constraint on users.email already prevented a
                    # plain INSERT from corrupting an existing row, but it
                    # would raise and get swallowed by this function's outer
                    # except-and-log, silently leaving the request without a
                    # resolved local id. This resolves to the winner's row
                    # instead of failing.
                    local_id = await conn.fetchval(
                        """
                        INSERT INTO users (email, role, password_hash)
                        VALUES ($1, $2, 'keycloak_managed')
                        ON CONFLICT (email) DO NOTHING
                        RETURNING id
                        """,
                        email,
                        role,
                    )
                    if local_id is None:
                        local_id = await conn.fetchval(
                            "SELECT id FROM users WHERE UPPER(email) = UPPER($1)", email
                        )
                    if local_id is not None:
                        user_id = str(local_id)
                        # Create profile record depending on role
                        if role in ("teacher", "event_teacher", "school_admin", "manager"):
                            await conn.execute(
                                "INSERT INTO teachers (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                                local_id,
                                email.split("@")[0].title(),
                            )
                        elif role == "student":
                            # Assign to an existing class deterministically
                            # if one exists; otherwise leave unassigned --
                            # class_id is nullable for exactly this reason
                            # (see students.class_id / StudentPlacementView's
                            # "Unassigned" state). This used to fabricate a
                            # fake "Grade 1" level, a fake
                            # head_teacher_*@school.com teacher account with
                            # no real password, and a fake "Default Class"
                            # the moment a student SSO-authenticated before
                            # any real structure existed -- a role with no
                            # administrative capability was silently
                            # creating academic structure and a staff
                            # account as a side effect of merely
                            # authenticating.
                            class_id = await conn.fetchval(
                                "SELECT id FROM class ORDER BY id ASC LIMIT 1"
                            )

                            await conn.execute(
                                "INSERT INTO students (id, name, class_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                                local_id,
                                email.split("@")[0].title(),
                                class_id,
                            )
                        # Ensure this JIT-provisioned user is also in user_tenant_map
                        # so future Keycloak SSO logins can resolve their tenant correctly
                        try:
                            from app.core.database import get_control_plane_pool as _get_cp_pool
                            from app.domains.tenant.control_plane_repository import (
                                ControlPlaneRepository as _CpRepo,
                            )

                            _cp_pool = await _get_cp_pool()
                            _cp_repo = _CpRepo(_cp_pool)
                            await _cp_repo.upsert_user_tenant_map(email, tenant_id, role)
                        except Exception as _e:
                            print(
                                f"[get_current_user] Warning: could not add JIT-provisioned user to user_tenant_map: {_e}"
                            )
        except Exception as exc:
            import traceback

            traceback.print_exc()
            print(
                f"[get_current_user] Warning: could not resolve/provision local database ID for email '{email}': {exc}"
            )

    # Resolve or JIT-provision parent roles.
    #
    # is_keycloak is load-bearing here, not optional: this block exists only
    # to provision a parent who authenticated via Keycloak SSO and has never
    # been seen locally. For a local-login token, get_current_user has
    # already fully resolved user_id/tenant_id/role from the tenant-local
    # `users` row lookup above (or, if that row didn't exist, from
    # AuthService.login_user's own control-plane linkage at login time) --
    # there is nothing left to provision. Running this unconditionally used
    # to mean *every* authenticated request from a local-login parent
    # re-executed "ensure parent exists globally in control plane", and the
    # first time that ran for a parent who (like every seed_data.py demo
    # parent) only ever got a tenant-local `users` row and no
    # public.parents row, it would find none and INSERT one with the
    # literal placeholder password_hash 'keycloak_managed' -- which
    # AuthService.login_user() then finds *first* on every subsequent
    # login attempt (it checks public.parents before tenant-local users),
    # permanently shadowing the real, working password with an
    # unauthenticatable placeholder. Confirmed live: parent.1@tenanta.com,
    # parent.1@tenantb.com, parent.1@tenantc.com, and parent.2@tenanta.com
    # all ended up with exactly this phantom row.
    if role == "parent" and email and is_keycloak:
        try:
            from app.core.database import get_control_plane_pool, get_db_pool

            # 1. Ensure parent exists globally in control plane
            cp_pool = await get_control_plane_pool()
            async with cp_pool.acquire() as conn_cp:
                parent_row = await conn_cp.fetchrow(
                    "SELECT id FROM parents WHERE email = $1", email
                )
                if not parent_row:
                    # ON CONFLICT (email) DO NOTHING + fallback SELECT: the
                    # SELECT above and this INSERT are separate statements, so
                    # a concurrent request for the same brand-new parent email
                    # could race here. parents.email is UNIQUE, so a plain
                    # INSERT on the loser would just raise (caught by the
                    # outer except below) and leave that request without a
                    # resolved parent id instead of resolving to the winner's
                    # row -- this makes it atomic.
                    global_parent_id = await conn_cp.fetchval(
                        """
                        INSERT INTO parents (email, password_hash)
                        VALUES ($1, 'keycloak_managed')
                        ON CONFLICT (email) DO NOTHING
                        RETURNING id
                        """,
                        email,
                    )
                    if global_parent_id is None:
                        global_parent_id = await conn_cp.fetchval(
                            "SELECT id FROM parents WHERE email = $1", email
                        )
                else:
                    global_parent_id = parent_row["id"]

                # Link parent to tenant in control plane
                await conn_cp.execute(
                    "INSERT INTO parent_tenant_links (parent_id, tenant_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    global_parent_id,
                    tenant_id,
                )

            # 2. Ensure parent exists locally in tenant DB
            tenant_pool = await get_db_pool(tenant_id)
            async with tenant_pool.acquire() as conn_t:
                local_id = await conn_t.fetchval("SELECT id FROM users WHERE email = $1", email)
                if local_id is None:
                    async with conn_t.transaction():
                        local_id = await conn_t.fetchval(
                            """
                            INSERT INTO users (email, role, password_hash)
                            VALUES ($1, 'parent', 'keycloak_managed')
                            ON CONFLICT (email) DO NOTHING
                            RETURNING id
                            """,
                            email,
                        )
                        if local_id is None:
                            local_id = await conn_t.fetchval(
                                "SELECT id FROM users WHERE email = $1", email
                            )
                        # The tenant-schema table is `parenets` (typo
                        # preserved from the original schema), not `parents`
                        # -- `parents` only exists in the control-plane
                        # (public) schema, which is still reachable here
                        # since search_path is "<tenant>, public". Writing
                        # to it used to silently create a control-plane row
                        # with the wrong id type and no `name` column,
                        # raise, get swallowed by the except below, and
                        # leave the users row committed with no matching
                        # parenets row -- invisible to GET /parents, and a
                        # foreign key violation (500) the moment staff tried
                        # to link this parent to a student. Wrapped in a
                        # transaction so any future failure here rolls back
                        # the users insert too, instead of repeating that.
                        await conn_t.execute(
                            "INSERT INTO parenets (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                            local_id,
                            email.split("@")[0].title(),
                        )
                user_id = str(local_id)

            # Same gap as the generic JIT-provision path above: without this,
            # a parent JIT-provisioned here has no user_tenant_map row, so the
            # next Keycloak SSO login can't resolve their tenant and falls
            # back to tenant_a, re-provisioning them as a stray 'pending' user
            # in the wrong tenant.
            from app.domains.tenant.control_plane_repository import (
                ControlPlaneRepository as _CpRepo,
            )

            await _CpRepo(cp_pool).upsert_user_tenant_map(email, tenant_id, "parent")
        except Exception as exc:
            print(
                f"[get_current_user] Warning: could not resolve/provision local parent for email '{email}': {exc}"
            )

    # Re-expand composite role permissions with database roles/permissions
    for r in list(extracted_roles):
        if r in COMPOSITE_ROLE_PERMISSIONS:
            extracted_roles.update(COMPOSITE_ROLE_PERMISSIONS[r])

    final_roles_list = list(extracted_roles)

    return CurrentUser(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=email,
        roles=final_roles_list if final_roles_list else [role],
    )


def require_selected_tenant(current_user: CurrentUser) -> str:
    """The caller's active tenant id, normalised, or a 400 if there isn't one.

    `CurrentUser.tenant_id` is `str | None`, and None is a real, expected state: a
    super_admin has no home tenant (login mints tenant_id="") and picks one per
    request via X-Tenant-ID. Tenant-scoped endpoints must turn that into a clear
    "choose a school" response rather than an AttributeError from
    `.strip()` on None -- which is what they did when the resolver still defaulted
    everyone to tenant_a and None could never reach them.

    Every other role is guaranteed a tenant_id by the fail-closed check in
    get_current_user, so in practice this only fires for an unselected super_admin.
    """
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "No school selected. Send an X-Tenant-ID header to choose which "
                "school to act in."
            ),
        )
    return current_user.tenant_id.strip().lower()


async def require_tenant_live(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Hard gate: block every tenant-scoped action until the school has finished
    Day-1 setup (see app/domains/school/). Applied at the router level to every
    domain except the school setup endpoints themselves and the auth essentials
    (register/login/me), so a tenant stuck in "setup" cannot create users, send
    invitations, create events, or write to any other domain object over the
    API — regardless of what the frontend renders.

    super_admin is exempt: a platform operator must be able to reach a tenant
    regardless of its onboarding state, consistent with super_admin bypassing
    every other workflow/role check in this codebase.
    """
    if current_user.has_role("super_admin"):
        return current_user
    if not current_user.tenant_id:
        return current_user

    try:
        from app.core.database import get_db_pool

        pool = await get_db_pool(current_user.tenant_id)
        activated_at = await pool.fetchval(
            "SELECT activated_at FROM school_profile ORDER BY id ASC LIMIT 1"
        )

        if activated_at is None:
            # Legacy-tenant grandfathering, performed inline rather than only
            # via GET /school/setup-state: a tenant that already has a real
            # academic structure predates this feature and must never be
            # locked out just because this happens to be the first endpoint
            # it hits after a deploy.
            from app.domains.tenant.tenant_repository import TenantRepository

            structure = await TenantRepository(pool).get_academic_structure()
            if structure.get("has_structure"):
                from app.domains.school.repository import SchoolRepository

                await SchoolRepository(pool).grandfather_activate_if_missing()
                activated_at = True
    except Exception as exc:
        # Fail open on infra errors (e.g. transient connection issue) rather than
        # locking every tenant out — the table is created as part of the same
        # tenant provisioning that already ran earlier in this request.
        print(
            f"[require_tenant_live] Warning: could not resolve school_profile for tenant '{current_user.tenant_id}': {exc}"
        )
        return current_user

    if activated_at is None:
        raise HTTPException(
            status_code=403, detail="Complete school setup before performing this action"
        )
    return current_user
