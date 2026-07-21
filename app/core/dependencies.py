"""
Shared FastAPI dependencies.

Extracts current user context from JWT tokens and performs role-based authorization guards.
"""


from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.domains.auth.service import AuthService

_security = HTTPBearer(auto_error=False)


class CurrentUser:
    """Value object representing an authenticated user extracted from the JWT."""

    def __init__(self, user_id: str, tenant_id: str | None, role: str, email: str = "") -> None:
        self.id = user_id
        self.tenant_id = tenant_id
        self.role = role
        self.email = email


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_security),
) -> CurrentUser:
    """Decode the Bearer JWT and return a CurrentUser object.

    Raises HTTP 401 if the token is missing, expired, or invalid.
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authentication token")

    payload = AuthService.decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
        )

    role = payload.get("role", "student")
    tenant_id = payload.get("tenant_id")

    # Guard: school users MUST have a valid tenant_id context
    if role in ("school_admin", "teacher", "student", "parent", "manager", "finance") and not tenant_id:
        raise HTTPException(
            status_code=401,
            detail="Missing tenant context in token for school users",
        )

    return CurrentUser(
        user_id=payload["sub"],
        tenant_id=tenant_id if tenant_id else None,
        role=role,
        email=payload.get("email", ""),
    )
