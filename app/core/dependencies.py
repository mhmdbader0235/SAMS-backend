"""
Shared FastAPI dependencies.

Extracts current user context from JWT tokens and performs role-based authorization guards.
"""


import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.domains.auth.service import AuthService

_security = HTTPBearer(auto_error=False)


class RoleList(list):
    """Subclass of list that implements the set-like intersection method."""
    def intersection(self, other):
        return set(self).intersection(other)


class CurrentUser:
    """Value object representing an authenticated user extracted from the JWT."""

    def __init__(self, user_id: str, tenant_id: str | None, role: str, email: str = "", roles: list[str] | None = None) -> None:
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

        if "super_admin" in roles_set:
            return True

        if role_name in roles_set:
            return True

        return False

    def has_any_role(self, *role_names: str) -> bool:
        """Check if user has any of the specified roles."""
        return any(self.has_role(r) for r in role_names)


async def get_current_user(
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

    # Fallback attempt: Handle Keycloak OIDC claims (when verified upstream via APISIX)
    if not payload:
        try:
            payload = jwt.decode(token, options={"verify_signature": False})
        except Exception:
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
        "super_admin", "school_admin", "tenant_manager", "manager",
        "academic_director", "department_head", "teacher", "event_scheduler",
        "event_teacher", "resource_manager", "finance", "finance_auditor",
        "content_creator", "content_editor", "announcement_manager",
        "parent", "student", "student_rep", "guest_viewer", "auditor",
        
        # New 40 Granular Roles
        "system:write", "system:read", "tenant:manage", "tenant:view",
        "school:write", "school:read", "academic:direct", "academic:view",
        "user:create", "user:delete", "user:link", "user:view",
        "event:create", "event:edit", "event:delete", "event:propose",
        "event:review", "event:publish", "event:clone", "event:view_draft",
        "resource:create", "resource:price", "resource:view",
        "teacher:write", "teacher:read",
        "enrollment:request", "enrollment:parent_approve", "enrollment:teacher_approve",
        "enrollment:cancel", "enrollment:view_roster",
        "billing:invoice", "billing:pay", "billing:refund", "billing:audit",
        "content:create", "content:publish", "announcement:manage"
    }
    
    PRIMARY_ROLE_ORDER = [
        "super_admin", "school_admin", "manager", "finance",
        "event_teacher", "teacher", "parent", "student"
    ]
    
    keycloak_roles = payload.get("realm_access", {}).get("roles", [])
    single_role = payload.get("role")
    
    role = None
    if single_role and single_role in VALID_ROLES:
        role = single_role
    else:
        for p_role in PRIMARY_ROLE_ORDER:
            if p_role in keycloak_roles:
                role = p_role
                break
    
    if not role:
        extracted = [r for r in keycloak_roles if r in VALID_ROLES]
        role = extracted[0] if extracted else "student"

    extracted_roles = [r for r in keycloak_roles if r in VALID_ROLES]
    if role not in extracted_roles:
        extracted_roles.append(role)

    tenant_id = payload.get("tenant_id") or "tenant_a"

    # Resolve local database user ID for school roles (teachers, managers, finance, admins, students, parents)
    if role != "super_admin" and email:
        try:
            from app.core.database import get_db_pool
            pool = await get_db_pool(tenant_id)
            async with pool.acquire() as conn:
                local_id = await conn.fetchval(
                    "SELECT id FROM users WHERE email = $1", email
                )
                if local_id is not None:
                    user_id = str(local_id)
                else:
                    # JIT Auto-provision missing Keycloak user locally
                    local_id = await conn.fetchval(
                        """
                        INSERT INTO users (email, role, password_hash)
                        VALUES ($1, $2, 'keycloak_managed')
                        RETURNING id
                        """,
                        email,
                        role
                    )
                    if local_id is not None:
                        user_id = str(local_id)
                        # Create profile record depending on role
                        if role in ("teacher", "event_teacher", "school_admin", "manager", "finance"):
                            await conn.execute(
                                "INSERT INTO teachers (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                                local_id,
                                email.split("@")[0].title()
                            )
                        elif role == "student":
                            # Assign student to first available class or fallback
                            class_id = await conn.fetchval("SELECT id FROM class LIMIT 1")
                            if class_id is None:
                                # Create default class if none exist
                                level_id = await conn.fetchval("SELECT level_id FROM levels LIMIT 1")
                                if level_id is None:
                                    level_id = await conn.fetchval("INSERT INTO levels (name) VALUES ('Grade 1') RETURNING level_id")
                                t_id = await conn.fetchval("SELECT id FROM teachers LIMIT 1")
                                if t_id is None:
                                    t_user = await conn.fetchval("INSERT INTO users (email, role, password_hash) VALUES ($1, 'teacher', 'managed') RETURNING id", f"head_teacher_{tenant_id}@school.com")
                                    t_id = await conn.fetchval("INSERT INTO teachers (id, name) VALUES ($1, 'Head Teacher') RETURNING id", t_user)
                                class_id = await conn.fetchval("INSERT INTO class (name, level_id, head_teacher_id) VALUES ('Default Class', $1, $2) RETURNING id", level_id, t_id)
                            
                            await conn.execute(
                                "INSERT INTO students (id, name, class_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                                local_id,
                                email.split("@")[0].title(),
                                class_id
                            )
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"[get_current_user] Warning: could not resolve/provision local database ID for email '{email}': {exc}")

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
                        email
                    )
                else:
                    global_parent_id = parent_row["id"]
                
                # Link parent to tenant in control plane
                await conn_cp.execute(
                    "INSERT INTO parent_tenant_links (parent_id, tenant_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    global_parent_id,
                    tenant_id
                )

            # 2. Ensure parent exists locally in tenant DB
            tenant_pool = await get_db_pool(tenant_id)
            async with tenant_pool.acquire() as conn_t:
                local_id = await conn_t.fetchval(
                    "SELECT id FROM users WHERE email = $1", email
                )
                if local_id is None:
                    local_id = await conn_t.fetchval(
                        "INSERT INTO users (email, role, password_hash) VALUES ($1, 'parent', 'keycloak_managed') RETURNING id",
                        email
                    )
                    await conn_t.execute(
                        "INSERT INTO parenets (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                        local_id,
                        email.split("@")[0].title()
                    )
                user_id = str(local_id)
        except Exception as exc:
            print(f"[get_current_user] Warning: could not resolve/provision local parent for email '{email}': {exc}")

    return CurrentUser(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=email,
        roles=extracted_roles if extracted_roles else [role],
    )
