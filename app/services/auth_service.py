"""
AuthService — business logic for authentication.

Does not import FastAPI or asyncpg directly. Enforces 3-tier layering and
Control-Plane vs. Tenant DB boundaries.
"""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from passlib.context import CryptContext

from ..config import (
    JWT_EXPIRATION_MINUTES,
    JWT_PRIVATE_KEY_PATH,
    JWT_PUBLIC_KEY_PATH,
    JWT_SECRET,
)
from ..database import get_control_plane_pool, get_db_pool
from ..repositories.control_plane_repository import ControlPlaneRepository
from ..repositories.user_repository import UserRepository
from ..repositories.tenant_repository import TenantRepository

_pwd_context = CryptContext(schemes=["bcrypt_sha256"], deprecated="auto")


def _get_signing_key() -> tuple[str | bytes, str]:
    """Return (key, algorithm). Prefers RS256 if key file exists, falls back to HS256."""
    if os.path.exists(JWT_PRIVATE_KEY_PATH):
        try:
            with open(JWT_PRIVATE_KEY_PATH) as f:
                return f.read(), "RS256"
        except OSError:
            pass
    return JWT_SECRET, "HS256"


def _get_verification_key() -> tuple[str | bytes, str]:
    """Return (key, algorithm) for token verification."""
    if os.path.exists(JWT_PUBLIC_KEY_PATH):
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import serialization

            with open(JWT_PUBLIC_KEY_PATH, "rb") as f:
                cert_data = f.read()
            cert = x509.load_pem_x509_certificate(cert_data)
            public_key_pem = cert.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("utf-8")
            return public_key_pem, "RS256"
        except Exception:
            pass
    return JWT_SECRET, "HS256"


class AuthService:
    @staticmethod
    def hash_password(password: str) -> str:
        """Return a bcrypt-sha256 hash of the given plaintext password."""
        return _pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Return True iff plain_password matches the stored hash."""
        return _pwd_context.verify(plain_password, hashed_password)

    @staticmethod
    def create_access_token(user_id, tenant_id: str | None, role: str, email: str = "") -> str:
        """Create a signed JWT containing user_id, tenant_id, role, and email."""
        expires_at = datetime.now(UTC) + timedelta(minutes=JWT_EXPIRATION_MINUTES)
        payload = {
            "sub": str(user_id),
            "tenant_id": tenant_id or "",
            "role": role,
            "email": email,
            "exp": int(expires_at.timestamp()),
        }
        key, algorithm = _get_signing_key()
        return jwt.encode(payload, key, algorithm=algorithm)

    @staticmethod
    def decode_access_token(token: str) -> dict | None:
        """Decode and verify a JWT. Returns the payload dict or None on failure."""
        try:
            key, algorithm = _get_verification_key()
            return jwt.decode(token, key, algorithms=[algorithm])
        except jwt.PyJWTError:
            return None

    @staticmethod
    async def register_user(
        email: str,
        password: str,
        role: str,
        tenant_id: str | None = None,
        invite_code: str | None = None,
    ) -> str:
        """Business logic for user registration."""
        password_hash = AuthService.hash_password(password)

        if role == "super_admin":
            cp_pool = await get_control_plane_pool()
            cp_repo = ControlPlaneRepository(cp_pool)
            if await cp_repo.get_super_admin_by_email(email):
                raise ValueError("Email already registered")
            user_id = await cp_repo.create_super_admin(email, password_hash)
            return AuthService.create_access_token(user_id, tenant_id="", role="super_admin", email=email)

        elif role == "parent":
            if not tenant_id:
                raise ValueError("Tenant ID is required for parent registration")
            
            # 1. Register parent globally in control plane
            cp_pool = await get_control_plane_pool()
            cp_repo = ControlPlaneRepository(cp_pool)
            global_parent = await cp_repo.get_parent_by_email(email)
            if global_parent:
                if not AuthService.verify_password(password, global_parent["password_hash"]):
                    raise ValueError("Email already registered with a different password")
                global_parent_id = global_parent["id"]
            else:
                global_parent_id = await cp_repo.create_parent(email, password_hash)
            
            await cp_repo.add_parent_tenant_link(global_parent_id, tenant_id)

            # 2. Register parent locally inside the tenant database
            tenant_pool = await get_db_pool(tenant_id)
            user_repo = UserRepository(tenant_pool)
            tenant_repo = TenantRepository(tenant_pool)
            local_user = await user_repo.get_user_by_email(email)
            if not local_user:
                local_user_id = await user_repo.create_user(email, password_hash, "parent")
                await tenant_repo.create_parent(local_user_id, email.split("@")[0].title())
            else:
                local_user_id = local_user["id"]

            return AuthService.create_access_token(local_user_id, tenant_id=tenant_id, role="parent", email=email)

        elif role in ("school_admin", "teacher", "student", "manager", "finance"):
            if not tenant_id:
                raise ValueError("Tenant ID is required for school users")

            if role in ("teacher", "manager", "finance"):
                from app.config import TEACHER_INVITE_CODE
                if not invite_code or invite_code.strip() != TEACHER_INVITE_CODE:
                    raise PermissionError("Invalid or missing staff invite code")

            tenant_pool = await get_db_pool(tenant_id)
            user_repo = UserRepository(tenant_pool)
            tenant_repo = TenantRepository(tenant_pool)
            
            if await user_repo.get_user_by_email(email):
                raise ValueError("Email already registered")
                
            local_user_id = await user_repo.create_user(email, password_hash, role)

            # Create corresponding teacher / student details
            if role == "teacher":
                await tenant_repo.create_teacher(local_user_id, email.split("@")[0].title())
            elif role == "student":
                # Ensure levels and classes exist to associate student with class
                all_levels = await tenant_repo.get_all_levels()
                if all_levels:
                    lvl_id = all_levels[0]["level_id"]
                else:
                    lvl_id = await tenant_repo.create_level("Grade 1")

                all_classes = await tenant_repo.get_all_classes()
                if all_classes:
                    cls_id = all_classes[0]["id"]
                else:
                    # Resolve head teacher: create or use an existing teacher
                    all_teachers = await tenant_repo.get_all_teachers()
                    if all_teachers:
                        t_id = all_teachers[0]["id"]
                    else:
                        # Auto-create dummy staff user
                        t_user_id = await user_repo.create_user(f"teacher_{tenant_id}@school.com", AuthService.hash_password("password"), "teacher")
                        t_id = await tenant_repo.create_teacher(t_user_id, "Primary Head Teacher")
                    
                    cls_id = await tenant_repo.create_class("General", lvl_id, t_id)

                await tenant_repo.create_student(
                    user_id=local_user_id,
                    name=email.split("@")[0].title(),
                    class_id=cls_id,
                )

            return AuthService.create_access_token(local_user_id, tenant_id, role, email=email)
        else:
            raise ValueError(f"Invalid registration role: {role}")

    @staticmethod
    async def login_user(email: str, password: str, tenant_id: str | None = None) -> str:
        """Business logic for user login."""
        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)

        # Check Super Admins
        super_admin = await cp_repo.get_super_admin_by_email(email)
        if super_admin:
            if not AuthService.verify_password(password, super_admin["password_hash"]):
                raise ValueError("Invalid email or password")
            return AuthService.create_access_token(super_admin["id"], tenant_id="", role="super_admin", email=email)

        # Check Parents (Global database checks)
        parent = await cp_repo.get_parent_by_email(email)
        if parent:
            if not AuthService.verify_password(password, parent["password_hash"]):
                raise ValueError("Invalid email or password")
            
            if not tenant_id:
                raise ValueError("Tenant ID is required for parent login")
            is_linked = await cp_repo.check_parent_tenant_link(parent["id"], tenant_id)
            if not is_linked:
                raise ValueError("Account not registered in this school/tenant")

            # Check/Create parent locally inside tenant DB to keep consistency
            tenant_pool = await get_db_pool(tenant_id)
            user_repo = UserRepository(tenant_pool)
            tenant_repo = TenantRepository(tenant_pool)
            local_user = await user_repo.get_user_by_email(email)
            if not local_user:
                local_user_id = await user_repo.create_user(email, parent["password_hash"], "parent")
                await tenant_repo.create_parent(local_user_id, email.split("@")[0].title(), parent.get("phone"))
            else:
                local_user_id = local_user["id"]
                
            return AuthService.create_access_token(local_user_id, tenant_id=tenant_id, role="parent", email=email)

        # Check Tenant users (school_admin, teacher, student, parent)
        if not tenant_id:
            raise ValueError("Tenant ID is required for school users")

        tenant_pool = await get_db_pool(tenant_id)
        user_repo = UserRepository(tenant_pool)
        user = await user_repo.get_user_by_email(email)
        if not user or not AuthService.verify_password(password, user["password_hash"]):
            raise ValueError("Invalid email or password")

        return AuthService.create_access_token(user["id"], tenant_id, user["role"], email=email)

    @staticmethod
    async def list_tenants() -> list[dict]:
        """Fetch list of all tenants from control plane DB."""
        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)
        return await cp_repo.get_all_tenants()
