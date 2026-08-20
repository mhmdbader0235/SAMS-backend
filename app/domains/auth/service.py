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

from app.core.config import (
    JWT_EXPIRATION_MINUTES,
    JWT_PRIVATE_KEY_PATH,
    JWT_PUBLIC_KEY_PATH,
    JWT_SECRET,
)
from app.core.database import get_control_plane_pool, get_db_pool
from app.core.keycloak_admin import sync_user_to_keycloak
from app.domains.tenant.control_plane_repository import ControlPlaneRepository
from app.domains.tenant.tenant_repository import TenantRepository
from app.domains.tenant.user_repository import UserRepository

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
            public_key_pem = (
                cert.public_key()
                .public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                )
                .decode("utf-8")
            )
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
    def create_access_token(
        user_id, tenant_id: str | None, role: str, email: str = "", roles: list[str] | None = None
    ) -> str:
        """Create a signed JWT containing user_id, tenant_id, role, roles, and email."""
        expires_at = datetime.now(UTC) + timedelta(minutes=JWT_EXPIRATION_MINUTES)
        payload = {
            "sub": str(user_id),
            "tenant_id": tenant_id or "",
            "role": role,
            "roles": roles or ([role] if role else []),
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
        name: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> str:
        """Business logic for user registration."""
        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)

        if not invite_code or not invite_code.strip():
            raise ValueError("Invitation code is required for registration")

        code_str = invite_code.strip()
        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)

        inv_record = await cp_repo.get_invitation_by_code(code_str)

        # If not found in invitations table, search user_invitations audit table
        if not inv_record:
            async with cp_pool.acquire() as conn:
                ui_row = await conn.fetchrow(
                    """
                    SELECT id, email AS target_email, tenant_id, role, status, created_at
                    FROM user_invitations
                    WHERE (id::text = $1 OR UPPER(email) = UPPER($1) OR $1 LIKE 'INV-%') AND status = 'pending'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    code_str,
                )
                if ui_row:
                    inv_record = {
                        "code": code_str,
                        "tenant_id": ui_row["tenant_id"],
                        "role": ui_row["role"],
                        "target_email": ui_row["target_email"],
                        "max_uses": 1,
                        "uses_count": 0,
                        "expires_at": None,
                        "is_active": True,
                    }

        from app.core.config import SUPER_ADMIN_BOOTSTRAP_CODE

        fallback_codes = {"school-staff-2026", "regester123", "register123", "teacher-pass-2026"}
        is_recognized = (
            code_str.lower() in fallback_codes
            # Case-sensitive and role-scoped on purpose: this code must not
            # also work as a staff passphrase for teacher/manager/etc, or
            # F-01's "one shared secret grants full cross-tenant access" gap
            # just reopens under a new name.
            or (role == "super_admin" and code_str == SUPER_ADMIN_BOOTSTRAP_CODE)
        )
        if not inv_record and not is_recognized:
            raise ValueError("Invalid or unrecognized invitation code")

        invitation = None
        if inv_record:
            if not inv_record.get("is_active", True):
                raise ValueError("Invitation code is inactive or has already been used")

            exp = inv_record.get("expires_at")
            if exp:
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=UTC)
                if exp < datetime.now(UTC):
                    raise ValueError("Invitation code has expired")

            if inv_record.get("uses_count", 0) >= inv_record.get("max_uses", 1):
                raise ValueError("Invitation code max uses reached")

            # Lock parameters strictly to the invitation creator's organization & role
            target_tenant = inv_record.get("tenant_id")
            if target_tenant:
                tenant_id = target_tenant.strip().lower()

            target_role = inv_record.get("role")
            if target_role:
                role = target_role.strip().lower()

            # STRICT MATCH: Email must match invitation target if specified
            target_email = inv_record.get("target_email")
            if target_email and target_email.strip().lower() != email.strip().lower():
                raise ValueError(f"Invitation code is strictly reserved for email: {target_email}")

            invitation = inv_record

        password_hash = AuthService.hash_password(password)

        if role == "super_admin":
            if not invitation:
                from app.core.config import SUPER_ADMIN_BOOTSTRAP_CODE

                if code_str != SUPER_ADMIN_BOOTSTRAP_CODE:
                    raise PermissionError(
                        "super_admin accounts require the dedicated platform bootstrap code, "
                        "not a staff self-registration passphrase."
                    )
            if await cp_repo.get_super_admin_by_email(email):
                raise ValueError("Email already registered")
            user_id = await cp_repo.create_super_admin(email, password_hash)
            sync_user_to_keycloak(email, password, "super_admin")
            if invitation:
                await cp_repo.increment_invitation_uses(invitation["code"])
            return AuthService.create_access_token(
                user_id, tenant_id="", role="super_admin", email=email
            )

        elif role == "parent":
            if not tenant_id:
                raise ValueError("Tenant ID is required for parent registration")

            # 1. Register parent globally in control plane
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

            sync_user_to_keycloak(email, password, "parent", tenant_id)
            # Save email→tenant mapping for Keycloak token resolution
            await cp_repo.upsert_user_tenant_map(email, tenant_id, "parent")
            if invitation:
                await cp_repo.increment_invitation_uses(invitation["code"])
            return AuthService.create_access_token(
                local_user_id, tenant_id=tenant_id, role="parent", email=email
            )

        elif role in ("school_admin", "teacher", "student", "manager", "event_teacher"):
            if not tenant_id:
                raise ValueError("Tenant ID is required for school users")

            # school_admin is deliberately NOT covered by the generic fallback
            # passphrases below — granting tenant-admin access must always
            # come from a real, targeted invitation (see AuthService.create_invitation),
            # never a shared/guessable code. Without this, anyone who knows a
            # fallback passphrase could self-appoint as admin of any tenant_id
            # they submit, including tenants they have no relationship to.
            if role == "school_admin" and not invitation:
                raise PermissionError(
                    "A school_admin account can only be created from a real invitation issued by "
                    "a super_admin or an existing school_admin for this tenant."
                )

            if not invitation and role in ("teacher", "manager", "event_teacher"):
                from app.core.config import TEACHER_INVITE_CODE

                valid_codes = {
                    TEACHER_INVITE_CODE,
                    "regester123",
                    "register123",
                    "SCHOOL-STAFF-2026",
                }
                if not invite_code or invite_code.strip() not in valid_codes:
                    raise PermissionError("Invalid or missing registration pass")

            tenant_pool = await get_db_pool(tenant_id)
            user_repo = UserRepository(tenant_pool)
            tenant_repo = TenantRepository(tenant_pool)

            if await user_repo.get_user_by_email(email):
                raise ValueError("Email already registered")

            local_user_id = await user_repo.create_user(email, password_hash, role)
            user_name = (name or f"{first_name or ''} {last_name or ''}".strip()) or email.split(
                "@"
            )[0].title()

            # Create corresponding teacher / student details
            if role == "teacher":
                await tenant_repo.create_teacher(local_user_id, user_name)
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
                        t_user_id = await user_repo.create_user(
                            f"teacher_{tenant_id}@school.com",
                            AuthService.hash_password("password"),
                            "teacher",
                        )
                        t_id = await tenant_repo.create_teacher(t_user_id, "Primary Head Teacher")

                    cls_id = await tenant_repo.create_class("General", lvl_id, t_id)

                await tenant_repo.create_student(
                    user_id=local_user_id,
                    name=user_name,
                    class_id=cls_id,
                )

            # Sync user to Keycloak realm
            sync_user_to_keycloak(
                email, password, role, tenant_id, first_name=first_name, last_name=last_name
            )
            # Save email→tenant mapping for Keycloak token resolution
            await cp_repo.upsert_user_tenant_map(email, tenant_id, role)

            if invitation:
                await cp_repo.increment_invitation_uses(invitation["code"])

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
            return AuthService.create_access_token(
                super_admin["id"], tenant_id="", role="super_admin", email=email
            )

        # Auto-resolve tenant_id from control plane or tenant search if not explicitly passed
        if not tenant_id:
            mapped = await cp_repo.get_tenant_for_email(email)
            if mapped and mapped.get("tenant_id"):
                tenant_id = mapped["tenant_id"]
            else:
                all_tenants = await cp_repo.get_all_tenants()
                for t in all_tenants:
                    tid = t.get("tenant_id") or t.get("id")
                    if not tid:
                        continue
                    try:
                        t_pool = await get_db_pool(tid)
                        u_repo = UserRepository(t_pool)
                        u = await u_repo.get_user_by_email(email)
                        if u:
                            tenant_id = tid
                            break
                    except Exception:
                        continue
        if not tenant_id:
            tenant_id = "tenant_a"

        # Check Parents (Global database checks)
        parent = await cp_repo.get_parent_by_email(email)
        if parent:
            if not AuthService.verify_password(password, parent["password_hash"]):
                raise ValueError("Invalid email or password")

            is_linked = await cp_repo.check_parent_tenant_link(parent["id"], tenant_id)
            if not is_linked:
                # Link parent to the resolved tenant automatically
                await cp_repo.create_parent_tenant_link(parent["id"], tenant_id)

            # Check/Create parent locally inside tenant DB to keep consistency
            tenant_pool = await get_db_pool(tenant_id)
            user_repo = UserRepository(tenant_pool)
            tenant_repo = TenantRepository(tenant_pool)
            local_user = await user_repo.get_user_by_email(email)
            if not local_user:
                local_user_id = await user_repo.create_user(
                    email, parent["password_hash"], "parent"
                )
                await tenant_repo.create_parent(
                    local_user_id, email.split("@")[0].title(), parent.get("phone")
                )
            else:
                local_user_id = local_user["id"]
            # Save email→tenant mapping so Keycloak logins resolve correctly
            await cp_repo.upsert_user_tenant_map(email, tenant_id, "parent")
            return AuthService.create_access_token(
                local_user_id, tenant_id=tenant_id, role="parent", email=email
            )

        # Check Tenant users (school_admin, teacher, student, parent)
        tenant_pool = await get_db_pool(tenant_id)
        user_repo = UserRepository(tenant_pool)
        user = await user_repo.get_user_by_email(email)
        if not user or not AuthService.verify_password(password, user["password_hash"]):
            raise ValueError("Invalid email or password")

        # Save email→tenant mapping so Keycloak logins resolve correctly
        await cp_repo.upsert_user_tenant_map(email, tenant_id, user["role"])

        # Include all custom roles and permissions from database
        user_roles = list(
            dict.fromkeys(
                ([user["role"]] if user.get("role") else [])
                + list(user.get("roles") or [])
                + list(user.get("permissions") or [])
            )
        )
        return AuthService.create_access_token(
            user["id"], tenant_id, user["role"], email=email, roles=user_roles
        )

    @staticmethod
    async def list_tenants() -> list[dict]:
        """Fetch list of all tenants from control plane DB."""
        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)
        return await cp_repo.get_all_tenants()

    @staticmethod
    async def create_tenant(tenant_id: str, name: str) -> dict:
        """Create a new tenant record and generate its PostgreSQL schema and tables."""
        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)

        # 1. Insert tenant in control plane
        await cp_repo.create_tenant(tenant_id=tenant_id, name=name)

        # 2. Trigger schema creation and table initialization in PostgreSQL
        await get_db_pool(tenant_id)

        return {
            "tenant_id": tenant_id,
            "name": name,
            "status": "schema_generated",
        }

    @staticmethod
    async def create_invitation(
        tenant_id: str,
        role: str,
        target_email: str | None = None,
        max_uses: int = 1,
        valid_days: int = 7,
        created_by: UUID | None = None,
    ) -> dict:
        """Generate a secure, role- & tenant-scoped invitation token."""
        import secrets

        code = f"INV-{tenant_id.upper()}-{role.upper()}-{secrets.token_hex(4).upper()}"
        expires_at = datetime.now(UTC) + timedelta(days=valid_days)

        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)
        inv = await cp_repo.create_invitation(
            code=code,
            tenant_id=tenant_id,
            role=role,
            target_email=target_email.strip().lower() if target_email else None,
            max_uses=max_uses,
            expires_at=expires_at,
            created_by=created_by,
        )

        # Send an email if a target email was provided
        if target_email:
            from app.utils.email import send_invitation_email

            await send_invitation_email(
                to_email=target_email.strip().lower(), invite_code=code, role=role
            )

        return inv

    @staticmethod
    async def get_invitation(code: str) -> dict:
        """Validate and fetch metadata for an invitation code."""
        code_str = code.strip()
        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)
        inv = await cp_repo.get_invitation_by_code(code_str)

        if not inv:
            async with cp_pool.acquire() as conn:
                ui_row = await conn.fetchrow(
                    """
                    SELECT id, email AS target_email, tenant_id, role, status, created_at
                    FROM user_invitations
                    WHERE (id::text = $1 OR UPPER(email) = UPPER($1) OR $1 LIKE 'INV-%') AND status = 'pending'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    code_str,
                )
                if ui_row:
                    inv = {
                        "code": code_str,
                        "tenant_id": ui_row["tenant_id"],
                        "role": ui_row["role"],
                        "target_email": ui_row["target_email"],
                        "max_uses": 1,
                        "uses_count": 0,
                        "expires_at": None,
                        "is_active": True,
                    }

        fallback_codes = {"school-staff-2026", "regester123", "register123", "teacher-pass-2026"}
        if not inv and code_str.lower() in fallback_codes:
            inv = {
                "code": code_str,
                "tenant_id": None,
                "role": None,
                "target_email": None,
                "max_uses": 999999,
                "uses_count": 0,
                "expires_at": None,
                "is_active": True,
            }

        if not inv or not inv.get("is_active", True):
            raise ValueError("Invalid or inactive invitation code")

        exp = inv.get("expires_at")
        if exp:
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=UTC)
            if exp < datetime.now(UTC):
                raise ValueError("Invitation code has expired")

        if inv.get("uses_count", 0) >= inv.get("max_uses", 1):
            raise ValueError("Invitation code maximum usage limit reached")
        return inv

    @staticmethod
    async def get_pending_users(tenant_id: str) -> list[dict]:
        """Fetch all users in the tenant DB with role='pending'."""
        db_pool = await get_db_pool(tenant_id)
        repo = UserRepository(db_pool)
        return await repo.get_users_by_role("pending")

    @staticmethod
    async def assign_user_role(tenant_id: str, email: str, new_role: str) -> dict:
        """Assign a new role to a pending user (or update an existing role)."""
        valid_roles = (
            "super_admin",
            "school_admin",
            "admin",
            "teacher",
            "parent",
            "student",
            "manager",
            "event_teacher",
            "pending",
        )
        if new_role not in valid_roles:
            raise ValueError(f"Invalid role: {new_role}")

        # 1. Update the user in the tenant DB
        db_pool = await get_db_pool(tenant_id)
        repo = UserRepository(db_pool)
        user = await repo.get_user_by_email(email)
        if not user:
            # JIT provision if missing in tenant
            user_id = await repo.create_user(email=email, password_hash="managed", role=new_role)
            user = {"id": user_id, "email": email}
        else:
            success = await repo.update_user_role(email, new_role)
            if not success:
                raise RuntimeError(f"Failed to update role for {email} in tenant DB")

        user_id = user.get("id")
        user_display_name = email.split("@")[0].replace(".", " ").title()

        # 2. Ensure profile records in tenant DB
        try:
            async with db_pool.acquire() as conn_t:
                if new_role in ("teacher", "event_teacher", "school_admin", "manager", "admin"):
                    await conn_t.execute(
                        "INSERT INTO teachers (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                        user_id,
                        user_display_name,
                    )
                elif new_role == "student":
                    c_id = await conn_t.fetchval("SELECT id FROM class LIMIT 1")
                    if c_id is None:
                        l_id = await conn_t.fetchval("SELECT level_id FROM levels LIMIT 1")
                        if l_id is None:
                            l_id = await conn_t.fetchval(
                                "INSERT INTO levels (name) VALUES ('Grade 1') RETURNING level_id"
                            )
                        t_id = await conn_t.fetchval("SELECT id FROM teachers LIMIT 1")
                        if t_id is None:
                            t_u = await conn_t.fetchval(
                                "INSERT INTO users (email, role, password_hash) VALUES ($1, 'teacher', 'managed') RETURNING id",
                                f"head_teacher_{tenant_id}@school.com",
                            )
                            t_id = await conn_t.fetchval(
                                "INSERT INTO teachers (id, name) VALUES ($1, 'Head Teacher') RETURNING id",
                                t_u,
                            )
                        c_id = await conn_t.fetchval(
                            "INSERT INTO class (name, level_id, head_teacher_id) VALUES ('Default Class', $1, $2) RETURNING id",
                            l_id,
                            t_id,
                        )
                    await conn_t.execute(
                        "INSERT INTO students (id, name, class_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                        user_id,
                        user_display_name,
                        c_id,
                    )
                elif new_role == "parent":
                    try:
                        await conn_t.execute(
                            "INSERT INTO parenets (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                            user_id,
                            user_display_name,
                        )
                    except Exception:
                        pass
        except Exception as _e:
            print(f"[assign_user_role] Warning profile records sync: {_e}")

        # 3. Update the control plane user_tenant_map and super_admins if applicable
        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)
        await cp_repo.upsert_user_tenant_map(email, tenant_id, new_role)

        if new_role == "super_admin":
            try:
                async with cp_pool.acquire() as conn_cp:
                    await conn_cp.execute(
                        "INSERT INTO super_admins (email, password_hash) VALUES ($1, 'managed') ON CONFLICT DO NOTHING",
                        email,
                    )
            except Exception as _e:
                print(f"[assign_user_role] Warning super_admins insert: {_e}")

        # 4. Update Keycloak
        try:
            from app.core.keycloak_admin import update_user_role_in_keycloak

            update_user_role_in_keycloak(email, new_role, tenant_id)
        except Exception as _e:
            print(f"[assign_user_role] Warning Keycloak update for {email}: {_e}")

        return {"status": "ok", "email": email, "role": new_role}
