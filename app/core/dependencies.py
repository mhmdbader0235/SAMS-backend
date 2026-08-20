"""
Shared FastAPI dependencies.

Extracts current user context from JWT tokens and performs role-based authorization guards.
"""

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
    "super_admin": {"*"},
    "school_admin": {
        "school:write",
        "school:read",
        "level:create",
        "level:manage",
        "level:read",
        "class:create",
        "class:edit",
        "class:update",
        "class:read",
        "class:assign_teacher",
        "user:create",
        "user:invite",
        "user:delete",
        "user:link",
        "user:view",
        "user:read",
        "user:profile_read",
        "user:profile_edit",
        "teacher:create",
        "teacher:read",
        "teacher:write",
        "parent:read",
        "student:create",
        "student:read",
        "event:create",
        "event:read",
        "event:view",
        "event:edit",
        "event:patch",
        "event:delete",
        "event:clone",
        "event:propose",
        "event:submit",
        "event:review",
        "event:publish",
        "event:view_draft",
        "event:archive",
        "event:audience_edit",
        "event:audience_predict",
        "resource:create",
        "resource:view",
        "resource:read",
        "resource:edit",
        "resource:update",
        "resource:delete",
        "resource:price",
        "resource:set_cost",
        "resource_type:create",
        "resource_type:read",
        "enrollment:teacher_approve",
        "enrollment:cancel",
        "enrollment:view_roster",
        "enrollment:read",
        "billing:audit",
        "billing:invoice",
        "billing:view_payment",
        "subsidy:manage",
        "audit:view",
        "health:view",
        "health:manage",
        "safety:manage",
        "announcement:manage",
        "notification:send",
        "notification:read",
        "notification:mark_read",
        "feedback:view",
    },
    "admin": {
        "school:write",
        "school:read",
        "level:create",
        "level:manage",
        "level:read",
        "class:create",
        "class:edit",
        "class:update",
        "class:read",
        "class:assign_teacher",
        "user:create",
        "user:invite",
        "user:delete",
        "user:link",
        "user:view",
        "user:read",
        "user:profile_read",
        "user:profile_edit",
        "teacher:create",
        "teacher:read",
        "teacher:write",
        "parent:read",
        "student:create",
        "student:read",
        "event:create",
        "event:read",
        "event:view",
        "event:edit",
        "event:patch",
        "event:delete",
        "event:clone",
        "event:propose",
        "event:submit",
        "event:review",
        "event:publish",
        "event:view_draft",
        "event:archive",
        "event:audience_edit",
        "event:audience_predict",
        "resource:create",
        "resource:view",
        "resource:read",
        "resource:edit",
        "resource:update",
        "resource:delete",
        "resource:price",
        "resource:set_cost",
        "resource_type:create",
        "resource_type:read",
        "enrollment:teacher_approve",
        "enrollment:cancel",
        "enrollment:view_roster",
        "enrollment:read",
        "billing:audit",
        "billing:invoice",
        "billing:view_payment",
        "subsidy:manage",
        "audit:view",
        "health:view",
        "health:manage",
        "safety:manage",
        "announcement:manage",
        "notification:send",
        "notification:read",
        "notification:mark_read",
        "feedback:view",
    },
    "manager": {
        "school:read",
        "level:read",
        "class:read",
        "teacher:read",
        "parent:read",
        "student:read",
        "user:view",
        "user:read",
        "user:profile_read",
        "user:profile_edit",
        "event:read",
        "event:view",
        "event:review",
        "event:publish",
        "event:view_draft",
        "event:audience_predict",
        "resource:view",
        "resource:read",
        "resource:price",
        "resource:set_cost",
        "resource_type:read",
        "billing:invoice",
        "billing:pay",
        "billing:refund",
        "billing:audit",
        "billing:view_payment",
        "subsidy:manage",
        "enrollment:view_roster",
        "enrollment:read",
        "announcement:manage",
        "notification:send",
        "notification:read",
        "notification:mark_read",
        "feedback:view",
    },
    "teacher": {
        "school:read",
        "level:read",
        "class:read",
        "teacher:read",
        "student:read",
        "user:view",
        "user:read",
        "user:profile_read",
        "user:profile_edit",
        "event:create",
        "event:read",
        "event:view",
        "event:edit",
        "event:patch",
        "event:delete",
        "event:clone",
        "event:propose",
        "event:submit",
        "event:view_draft",
        "event:audience_edit",
        "event:audience_predict",
        "resource:create",
        "resource:view",
        "resource:read",
        "resource:edit",
        "resource:update",
        "resource:delete",
        "resource_type:create",
        "resource_type:read",
        "enrollment:teacher_approve",
        "enrollment:view_roster",
        "enrollment:read",
        "health:view",
        "health:manage",
        "notification:read",
        "notification:mark_read",
        "feedback:view",
        "feedback:create",
    },
    "parent": {
        "school:read",
        "user:profile_read",
        "user:profile_edit",
        "student:view_linked",
        "event:read",
        "event:view",
        "enrollment:parent_approve",
        "enrollment:cancel",
        "enrollment:read",
        "billing:pay",
        "billing:view_payment",
        "health:manage_child",
        "notification:read",
        "notification:mark_read",
        "feedback:create",
    },
    "student": {
        "school:read",
        "user:profile_read",
        "user:profile_edit",
        "event:read",
        "event:view",
        "enrollment:request",
        "enrollment:read",
        "notification:read",
        "notification:mark_read",
        "feedback:create",
    },
}


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

    Supports both internal SchoolDesk JWTs and Keycloak OIDC tokens passed via APISIX.
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

    VALID_ROLES = {
        # High-level Roles
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
        # Granular Roles / Permissions
        "system:write",
        "system:read",
        "tenant:manage",
        "tenant:view",
        "school:write",
        "school:read",
        "academic:direct",
        "academic:view",
        "user:invite",
        "user:delete",
        "user:link",
        "user:view",
        "event:create",
        "event:edit",
        "event:delete",
        "event:propose",
        "event:review",
        "event:publish",
        "event:clone",
        "event:view_draft",
        "resource:create",
        "resource:price",
        "resource:view",
        "teacher:write",
        "teacher:read",
        "enrollment:request",
        "enrollment:parent_approve",
        "enrollment:teacher_approve",
        "enrollment:cancel",
        "enrollment:view_roster",
        "billing:invoice",
        "billing:pay",
        "billing:refund",
        "billing:audit",
        "content:create",
        "content:publish",
        "announcement:manage",
    }

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

    tenant_id = payload.get("tenant_id")

    # Try Keycloak Organization / Org claims
    if not tenant_id:
        org = payload.get("organization") or payload.get("org") or payload.get("organizations")
        if isinstance(org, dict):
            tenant_id = list(org.keys())[0] if org else None
        elif isinstance(org, list) and org:
            first = org[0]
            if isinstance(first, dict):
                tenant_id = first.get("name") or first.get("id")
            elif isinstance(first, str):
                tenant_id = first
        elif isinstance(org, str):
            tenant_id = org

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

    # Default fallback if tenant_id could not be resolved from token, map, or header
    if not tenant_id or tenant_id.lower() in ("sams", "schooldesk", "master"):
        tenant_id = "tenant_a"

    # Resolve local database user ID, dynamic roles, and custom permissions for school roles
    if email:
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
                    if db_role in ("pending", "none", "unassigned"):
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
                    # JIT Auto-provision missing Keycloak user locally
                    local_id = await conn.fetchval(
                        """
                        INSERT INTO users (email, role, password_hash)
                        VALUES ($1, $2, 'keycloak_managed')
                        RETURNING id
                        """,
                        email,
                        role,
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
                            # Assign student to first available class or fallback
                            class_id = await conn.fetchval("SELECT id FROM class LIMIT 1")
                            if class_id is None:
                                # Create default class if none exist
                                level_id = await conn.fetchval(
                                    "SELECT level_id FROM levels LIMIT 1"
                                )
                                if level_id is None:
                                    level_id = await conn.fetchval(
                                        "INSERT INTO levels (name) VALUES ('Grade 1') RETURNING level_id"
                                    )
                                t_id = await conn.fetchval("SELECT id FROM teachers LIMIT 1")
                                if t_id is None:
                                    t_user = await conn.fetchval(
                                        "INSERT INTO users (email, role, password_hash) VALUES ($1, 'teacher', 'managed') RETURNING id",
                                        f"head_teacher_{tenant_id}@school.com",
                                    )
                                    t_id = await conn.fetchval(
                                        "INSERT INTO teachers (id, name) VALUES ($1, 'Head Teacher') RETURNING id",
                                        t_user,
                                    )
                                class_id = await conn.fetchval(
                                    "INSERT INTO class (name, level_id, head_teacher_id) VALUES ('Default Class', $1, $2) RETURNING id",
                                    level_id,
                                    t_id,
                                )

                            await conn.execute(
                                "INSERT INTO students (id, name, class_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                                local_id,
                                email.split("@")[0].title(),
                                class_id,
                            )
        except Exception as exc:
            import traceback

            traceback.print_exc()
            print(
                f"[get_current_user] Warning: could not resolve/provision local database ID for email '{email}': {exc}"
            )

    # Resolve or JIT-provision parent roles
    if role == "parent" and email:
        try:
            from app.core.database import get_control_plane_pool, get_db_pool

            # 1. Ensure parent exists globally in control plane
            cp_pool = await get_control_plane_pool()
            async with cp_pool.acquire() as conn_cp:
                parent_row = await conn_cp.fetchrow(
                    "SELECT id FROM parents WHERE email = $1", email
                )
                if not parent_row:
                    global_parent_id = await conn_cp.fetchval(
                        "INSERT INTO parents (email, password_hash) VALUES ($1, 'keycloak_managed') RETURNING id",
                        email,
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
                    local_id = await conn_t.fetchval(
                        "INSERT INTO users (email, role, password_hash) VALUES ($1, 'parent', 'keycloak_managed') RETURNING id",
                        email,
                    )
                    await conn_t.execute(
                        "INSERT INTO parents (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                        local_id,
                        email.split("@")[0].title(),
                    )
                user_id = str(local_id)
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
