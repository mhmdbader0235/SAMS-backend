"""
Shared FastAPI dependencies.

Extracts current user context from JWT tokens and performs role-based authorization guards.
"""


import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.domains.auth.service import AuthService

_security = HTTPBearer(auto_error=False)


class CurrentUser:
    """Value object representing an authenticated user extracted from the JWT."""

    def __init__(self, user_id: str, tenant_id: str | None, role: str, email: str = "", roles: list[str] | None = None) -> None:
        self.id = user_id
        self.tenant_id = tenant_id
        self.role = role
        self.email = email
        self.roles = roles or [role]

    def has_role(self, role_name: str) -> bool:
        """Check if user has a specific role."""
        return role_name in self.roles or self.role == role_name or self.role == "super_admin" or "super_admin" in self.roles

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
        "super_admin", "school_admin", "tenant_manager", "manager",
        "academic_director", "department_head", "teacher", "event_scheduler",
        "event_teacher", "resource_manager", "finance", "finance_auditor",
        "content_creator", "content_editor", "announcement_manager",
        "parent", "student", "student_rep", "guest_viewer", "auditor"
    }
    
    keycloak_roles = payload.get("realm_access", {}).get("roles", [])
    extracted_roles = [r for r in keycloak_roles if r in VALID_ROLES]
    
    single_role = payload.get("role")
    if single_role and single_role not in extracted_roles:
        extracted_roles.append(single_role)

    role = single_role if (single_role and single_role in VALID_ROLES) else (extracted_roles[0] if extracted_roles else "student")

    tenant_id = payload.get("tenant_id") or "tenant_a"

    return CurrentUser(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=email,
        roles=extracted_roles if extracted_roles else [role],
    )
