"""
AuthService — business logic for authentication.

Does not import FastAPI or asyncpg directly. Enforces 3-tier layering and
Control-Plane vs. Tenant DB boundaries.
"""

import asyncio
import contextlib
import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from passlib.context import CryptContext
from passlib.exc import UnknownHashError
from starlette.concurrency import run_in_threadpool

from app.core.config import (
    JWT_EXPIRATION_MINUTES,
    JWT_PRIVATE_KEY_PATH,
    JWT_PUBLIC_KEY_PATH,
    JWT_SECRET,
    REFRESH_TOKEN_EXPIRATION_DAYS,
)
from app.core.database import get_control_plane_pool, get_db_pool
from app.core.keycloak_admin import (
    create_keycloak_organization,
    sync_user_to_keycloak,
    update_user_password_in_keycloak,
)
from app.domains.tenant.control_plane_repository import ControlPlaneRepository
from app.domains.tenant.tenant_repository import TenantRepository
from app.domains.tenant.user_repository import UserRepository

_pwd_context = CryptContext(schemes=["bcrypt_sha256"], deprecated="auto")

# Holds references to fire-and-forget background tasks (see
# AuthService._schedule_keycloak_sync) so asyncio cannot garbage-collect one
# mid-flight -- a bare asyncio.create_task() result with nothing else
# referencing it is only weakly held by the event loop.
_background_tasks: set[asyncio.Task] = set()


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
        """Return True iff plain_password matches the stored hash.

        A stored value that isn't a recognizable bcrypt_sha256 hash (e.g. a
        'keycloak_managed'/'managed' placeholder written by a JIT-provisioning
        path, or any other malformed value) makes passlib raise
        UnknownHashError instead of returning False. Left uncaught, that
        propagates as a raw "hash could not be identified" 401 detail —
        leaking an internal implementation detail to the client instead of
        the same generic auth failure a wrong password produces.
        """
        try:
            return _pwd_context.verify(plain_password, hashed_password)
        except UnknownHashError:
            return False

    @staticmethod
    async def hash_password_async(password: str) -> str:
        """hash_password, off the event loop.

        bcrypt-sha256 hashing is synchronous and CPU-bound (~150-300ms). Called
        straight from an `async def` request handler it blocks the entire event
        loop for the duration -- every concurrent request stalls, not just the
        caller's. Every call site inside a live request (registration, password
        change) must use this instead of the sync method; one-off scripts/seed
        data/tests that run outside the event loop can keep calling the sync
        version directly.
        """
        return await run_in_threadpool(AuthService.hash_password, password)

    @staticmethod
    async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
        """verify_password, off the event loop. See hash_password_async."""
        return await run_in_threadpool(AuthService.verify_password, plain_password, hashed_password)

    @staticmethod
    def create_access_token(
        user_id,
        tenant_id: str | None,
        role: str,
        email: str = "",
        roles: list[str] | None = None,
        identity_id: str | None = None,
    ) -> str:
        """Create a signed JWT containing user_id, tenant_id, role, roles, and email.

        `identity_id` (added for the Multi-School Membership work) is the
        immutable owner from `identities` -- a companion to `sub`, not a
        replacement for it, since `sub` stays the tenant-local user id every
        existing permission check and audit row already keys on. It is
        optional and omitted (None) for callers that mint a token without an
        identity to hand (there are none left after login_user/register_user,
        but the default keeps this signature backward compatible for any
        other caller). Role/permissions are still baked in at mint time and
        still only prove "who you were and what you could do at login" --
        dependencies.py treats the token's role as a stale hint and reads the
        live membership row as authority, exactly as before this field
        existed.
        """
        expires_at = datetime.now(UTC) + timedelta(minutes=JWT_EXPIRATION_MINUTES)
        payload = {
            "sub": str(user_id),
            "tenant_id": tenant_id or "",
            "role": role,
            "roles": roles or ([role] if role else []),
            "email": email,
            "exp": int(expires_at.timestamp()),
        }
        if identity_id:
            payload["identity_id"] = str(identity_id)
        key, algorithm = _get_signing_key()
        return jwt.encode(payload, key, algorithm=algorithm)

    # ─── Refresh tokens (rotation) ──────────────────────────────────────────
    # Opaque, high-entropy, stored hashed -- same reasoning as the selection
    # challenge below: for as long as it lives, it is momentarily equivalent
    # to a password, and the database is not a place to keep one in the
    # clear. sha256 rather than bcrypt: this is a 256-bit random secret, not
    # a human-chosen password, so there is no dictionary to defend against
    # and no reason to pay bcrypt's deliberate slowness on every refresh.
    @staticmethod
    def _hash_opaque_token(raw: str) -> bytes:
        return hashlib.sha256(raw.encode("utf-8")).digest()

    @staticmethod
    def _new_opaque_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    async def issue_refresh_token(
        cp_repo, identity_id, tenant_id: str, role: str, client_ip: str | None = None
    ) -> str:
        """Mint the first refresh token in a new rotation family."""
        raw = AuthService._new_opaque_token()
        family_id = uuid4()
        expires_at = datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_EXPIRATION_DAYS)
        await cp_repo.create_refresh_token(
            token_hash=AuthService._hash_opaque_token(raw),
            identity_id=identity_id,
            tenant_id=tenant_id or "",
            role=role,
            family_id=family_id,
            expires_at=expires_at,
            created_ip=client_ip,
        )
        return raw

    @staticmethod
    async def _resolve_local_account(
        cp_repo, identity_id, tenant_id: str, role: str
    ) -> tuple[str, str, list[str]]:
        """The tenant-local `sub` a fresh access token must carry, plus the
        role/roles to bake in -- re-derived from the account itself rather
        than trusted from a refresh_tokens/challenge row, since neither
        stores a tenant-local user id (that id lives in a per-tenant schema
        the control-plane refresh_tokens table has no business joining
        against). Shared by rotate_refresh_token and login_redeem so the two
        session-issuing paths that don't start from a fresh password check
        agree on how to look an account back up.

        Raises ValueError if the account can no longer be found -- e.g. a
        super_admin/user row deleted after the refresh/selection token was
        issued -- which the caller maps to the same 401 an invalid token gets.
        """
        identity = await cp_repo.get_identity_by_id(identity_id)
        if not identity:
            raise ValueError("Account no longer exists")
        email = identity["email"]

        if tenant_id == "" or role == "super_admin":
            super_admin = await cp_repo.get_super_admin_by_email(email)
            if not super_admin:
                raise ValueError("Account no longer exists")
            return str(super_admin["id"]), "super_admin", ["super_admin"]

        tenant_pool = await get_db_pool(tenant_id)
        user_repo = UserRepository(tenant_pool)
        user = await user_repo.get_user_by_email(email)
        if not user:
            raise ValueError("Account no longer exists")

        if role == "parent" and user.get("role") == "parent":
            return str(user["id"]), "parent", ["parent"]

        user_roles = list(
            dict.fromkeys(
                ([user["role"]] if user.get("role") else [])
                + list(user.get("roles") or [])
                + list(user.get("permissions") or [])
            )
        )
        return str(user["id"]), user["role"], user_roles

    @staticmethod
    async def rotate_refresh_token(cp_repo, raw_token: str, client_ip: str | None = None) -> dict:
        """Redeem a refresh token for a new access token + a new refresh token,
        revoking the one just presented (rotation) so it cannot be replayed.

        A presented token that is ALREADY revoked is not treated as an
        ordinary expiry/race -- it is the reuse signal rotation exists to
        catch (someone else redeemed this exact token first, or the
        legitimate holder did and this is a stolen copy), so the entire
        family is revoked rather than just failing this one request. Raises
        ValueError on any invalid/expired/reused/unknown token -- the router
        maps that to 401, same as an invalid access token.
        """
        token_hash = AuthService._hash_opaque_token(raw_token)
        row = await cp_repo.get_refresh_token(token_hash)
        if not row:
            raise ValueError("Invalid refresh token")

        if row["revoked_at"] is not None:
            await cp_repo.revoke_refresh_token_family(row["family_id"])
            raise ValueError("Invalid refresh token")

        if row["expires_at"] <= datetime.now(UTC):
            raise ValueError("Refresh token expired")

        user_id, role, roles = await AuthService._resolve_local_account(
            cp_repo, row["identity_id"], row["tenant_id"], row["role"]
        )

        new_raw = AuthService._new_opaque_token()
        expires_at = datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_EXPIRATION_DAYS)
        await cp_repo.create_refresh_token(
            token_hash=AuthService._hash_opaque_token(new_raw),
            identity_id=row["identity_id"],
            tenant_id=row["tenant_id"],
            role=role,
            family_id=row["family_id"],
            expires_at=expires_at,
            created_ip=client_ip,
        )
        new_row = await cp_repo.get_refresh_token(AuthService._hash_opaque_token(new_raw))
        await cp_repo.rotate_refresh_token(row["id"], new_row["id"])

        access_token = AuthService.create_access_token(
            user_id,
            tenant_id=row["tenant_id"] or None,
            role=role,
            roles=roles,
            identity_id=row["identity_id"],
        )
        return {"access_token": access_token, "refresh_token": new_raw}

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

        password_hash = await AuthService.hash_password_async(password)

        if role == "super_admin":
            from app.core.config import SUPER_ADMIN_ALLOWED_EMAIL

            # The platform has exactly one super_admin identity. This check runs
            # unconditionally -- ahead of, and independent from, the bootstrap-code
            # and invitation checks below -- so neither a leaked/shared bootstrap
            # code nor a super_admin-role invitation can mint a second one.
            if email.strip().lower() != SUPER_ADMIN_ALLOWED_EMAIL.strip().lower():
                raise PermissionError(
                    "super_admin registration is restricted to the platform's single "
                    "designated operator account."
                )
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
                if not await AuthService.verify_password_async(
                    password, global_parent["password_hash"]
                ):
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
                            await AuthService.hash_password_async("password"),
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
    async def _enumerate_login_tenants(
        cp_repo, email: str
    ) -> tuple[set[str], dict | None, dict | None]:
        """Which tenant_ids this email could plausibly authenticate against,
        WITHOUT fetching any per-tenant user row yet -- a cheap membership
        check (matches the pre-Phase-1 code's own laziness: an explicitly
        requested tenant_id that isn't even a member gets rejected here,
        before ever opening that tenant's pool). Fetching the actual
        password hash to verify against is `_fetch_login_candidate`'s job,
        called only for tenant_ids this function says are worth checking.

        Returns (tenant_ids, parent_record, super_admin_record). "" is
        included in tenant_ids when a super_admin record exists, matching
        create_access_token's own "" convention for a super_admin's tenant.
        """
        super_admin = await cp_repo.get_super_admin_by_email(email)
        membership_tenants = {
            m["tenant_id"] for m in await cp_repo.get_tenants_for_email(email) if m.get("tenant_id")
        }
        parent = await cp_repo.get_parent_by_email(email)
        # A parent record short-circuits the tenant-local `users` lookup
        # entirely for every tenant this email is linked to from EITHER
        # source (user_tenant_map or parent_tenant_links) -- exactly the
        # pre-existing branch order (a global parents row was always
        # checked before, never alongside, a tenant's own users table).
        # `check_parent_tenant_link` (in _fetch_login_candidate) is the
        # actual authorization gate for each of these; excluding a
        # membership-only tenant_id here just because its user_tenant_map
        # role happens to say 'parent' would drop exactly the case that
        # gate exists to catch -- a claimed relationship parent_tenant_links
        # does not confirm.
        if parent:
            membership_tenants |= set(await cp_repo.get_tenants_for_parent(parent["id"]))

        tenant_ids = set(membership_tenants)
        if super_admin:
            tenant_ids.add("")
        return tenant_ids, parent, super_admin

    @staticmethod
    async def _fetch_login_candidate(
        cp_repo, email: str, tenant_id: str, parent: dict | None, super_admin: dict | None
    ) -> dict | None:
        """The real password hash (and account record) to check for one
        already-enumerated tenant_id. None if the account backing this
        tenant_id has since disappeared (e.g. a users row deleted between
        enumeration and this fetch) -- treated as a non-match, not an error."""
        if tenant_id == "" and super_admin:
            return {
                "tenant_id": "",
                "kind": "super_admin",
                "hash": super_admin["password_hash"],
                "record": super_admin,
            }
        # A global `parents` row is keyed by email alone, not per tenant --
        # the same email can ALSO be a distinct tenant-local user (e.g. a
        # teacher) in a tenant this parent identity has no link to. Gating on
        # check_parent_tenant_link, not just `parent`'s existence, is what
        # keeps that tenant's candidate on its own local password hash
        # instead of silently being checked against the parent's hash --
        # otherwise a shared/coincidentally-matching password lets the parent
        # branch "win" a tenant where the account is actually a teacher, and
        # every choice for that email gets mislabeled "parent" in the
        # selection challenge (redeem still resolves the role correctly via
        # _resolve_local_account, so this was a mislabeling + spurious-match
        # bug, not a privilege escalation -- but a real bug all the same).
        if parent and await cp_repo.check_parent_tenant_link(parent["id"], tenant_id):
            return {
                "tenant_id": tenant_id,
                "kind": "parent",
                "hash": parent["password_hash"],
                "record": parent,
            }
        try:
            pool = await get_db_pool(tenant_id)
            u = await UserRepository(pool).get_user_by_email(email)
        except Exception:
            return None
        if u and u.get("password_hash"):
            return {
                "tenant_id": tenant_id,
                "kind": "tenant_user",
                "hash": u["password_hash"],
                "record": u,
            }
        return None

    @staticmethod
    async def _legacy_scan_for_candidate(cp_repo, email: str) -> dict | None:
        """No membership row anywhere. Last resort: locate the account by
        scanning tenant `users` tables. This only exists for accounts that
        predate user_tenant_map, and it is a tenant-enumeration primitive --
        but a hit here is not by itself trust: it still has to verify
        against that tenant's own password hash like any other candidate."""
        for t in await cp_repo.get_all_tenants():
            tid = t.get("tenant_id") or t.get("id")
            if not tid:
                continue
            try:
                pool = await get_db_pool(tid)
                u = await UserRepository(pool).get_user_by_email(email)
            except Exception:
                continue
            if u and u.get("password_hash"):
                print(
                    f"[login_user] Note: '{email}' has no user_tenant_map/"
                    f"parent_tenant_links row; found via tenant users table "
                    f"scan '{tid}'. This fallback is deprecated."
                )
                return {
                    "tenant_id": tid,
                    "kind": "tenant_user",
                    "hash": u["password_hash"],
                    "record": u,
                }
        return None

    @staticmethod
    async def _verify_candidates_padded(candidates: list[dict], password: str) -> list[dict]:
        """Verify concurrently, padded to a fixed floor, so a FAILED login's
        wall-clock time does not reveal how many schools this email belongs
        to. Bcrypt is slow by design: N sequential comparisons take N times
        as long as one, and an attacker submitting garbage passwords could
        otherwise read the membership count straight off the response time.
        Concurrency removes the "N times" and padding removes the "N" --
        one candidate and PADDING_FLOOR candidates cost the same wall-clock.
        """
        PADDING_FLOOR = 3
        dummy_hash = AuthService.hash_password("padding-comparison-not-a-real-account")
        pad_count = max(0, PADDING_FLOOR - len(candidates))

        results = await asyncio.gather(
            *[AuthService.verify_password_async(password, c["hash"]) for c in candidates],
            *[AuthService.verify_password_async(password, dummy_hash) for _ in range(pad_count)],
        )
        return [c for c, ok in zip(candidates, results[: len(candidates)], strict=False) if ok]

    @staticmethod
    def _schedule_keycloak_sync(email: str, password: str, chosen: dict) -> None:
        """Fire-and-forget: guarantee that any account which just proved its
        password locally also exists in Keycloak.

        This is what closes the gap for accounts that were ever written
        straight into Postgres -- seed_data.py, a manual SQL insert, a
        migration, or a registration that happened while Keycloak was down
        -- and so never went through sync_user_to_keycloak. Without this, such
        an account can log in locally forever but can never use Keycloak SSO,
        and nothing here would ever notice.

        Runs on every successful local password check, not just once, but
        that is safe: sync_user_to_keycloak only sets a Keycloak credential
        when it CREATES the Keycloak user (see keycloak_admin.py); an
        existing Keycloak user's password is left alone on every later call,
        so this cannot clobber a password someone set directly in Keycloak.
        Deliberately not awaited -- sync_user_to_keycloak already swallows
        its own errors and logs them, and a slow or unreachable Keycloak must
        never add latency to, or fail, the login response that already
        succeeded against Postgres.
        """
        role = chosen["record"].get("role") if chosen["kind"] == "tenant_user" else chosen["kind"]
        tenant_id = chosen["tenant_id"] or None
        task = asyncio.create_task(
            run_in_threadpool(sync_user_to_keycloak, email, password, role, tenant_id)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    @staticmethod
    async def _complete_login(
        cp_repo, chosen: dict, email: str, parent: dict | None, client_ip: str | None
    ) -> dict:
        """Mint a real session for exactly one already-password-verified
        candidate. Reuses the per-kind side effects (parent local-user
        provisioning, upsert_user_tenant_map) the single-tenant code path
        always had -- only how `chosen` was arrived at has changed."""
        tenant_id = chosen["tenant_id"]
        kind = chosen["kind"]
        identity = await cp_repo.get_or_create_identity(email)

        if kind == "super_admin":
            super_admin = chosen["record"]
            access_token = AuthService.create_access_token(
                super_admin["id"],
                tenant_id="",
                role="super_admin",
                email=email,
                identity_id=identity["id"],
            )
            refresh_token = await AuthService.issue_refresh_token(
                cp_repo, identity["id"], "", "super_admin", client_ip
            )
            return {"access_token": access_token, "refresh_token": refresh_token}

        if kind == "parent":
            # Membership is REQUIRED here, never created. This used to
            # auto-link the parent into whatever school `tenant_id` named --
            # see git history on this branch for why that was an
            # escalation; parent_tenant_links membership is checked, never
            # written, by a login attempt.
            is_linked = await cp_repo.check_parent_tenant_link(parent["id"], tenant_id)
            if not is_linked:
                raise ValueError("Invalid email or password")

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
            await cp_repo.upsert_user_tenant_map(email, tenant_id, "parent")
            access_token = AuthService.create_access_token(
                local_user_id,
                tenant_id=tenant_id,
                role="parent",
                email=email,
                identity_id=identity["id"],
            )
            refresh_token = await AuthService.issue_refresh_token(
                cp_repo, identity["id"], tenant_id, "parent", client_ip
            )
            return {"access_token": access_token, "refresh_token": refresh_token}

        # kind == "tenant_user"
        user = chosen["record"]
        await cp_repo.upsert_user_tenant_map(email, tenant_id, user["role"])
        user_roles = list(
            dict.fromkeys(
                ([user["role"]] if user.get("role") else [])
                + list(user.get("roles") or [])
                + list(user.get("permissions") or [])
            )
        )
        access_token = AuthService.create_access_token(
            user["id"],
            tenant_id,
            user["role"],
            email=email,
            roles=user_roles,
            identity_id=identity["id"],
        )
        refresh_token = await AuthService.issue_refresh_token(
            cp_repo, identity["id"], tenant_id, user["role"], client_ip
        )
        return {"access_token": access_token, "refresh_token": refresh_token}

    @staticmethod
    async def _issue_login_selection_challenge(
        cp_repo, email: str, matched: list[dict], client_ip: str | None
    ) -> dict:
        """The step between "password verified against several schools" and
        "session issued for one of them" -- an opaque, single-use, server-side
        secret (see cp_0006_selection_challenges) naming ONLY the schools
        that matched, never the account's full membership set."""
        identity = await cp_repo.get_or_create_identity(email)
        raw = AuthService._new_opaque_token()
        expires_at = datetime.now(UTC) + timedelta(minutes=2)
        await cp_repo.create_selection_challenge(
            token_hash=AuthService._hash_opaque_token(raw),
            identity_id=identity["id"],
            matched_tenants=[c["tenant_id"] for c in matched],
            expires_at=expires_at,
            created_ip=client_ip,
        )
        choices = [
            {
                "tenant_id": c["tenant_id"],
                "role": c["record"].get("role") if c["kind"] == "tenant_user" else c["kind"],
            }
            for c in matched
        ]
        return {
            "status": "select_tenant",
            "selection_token": raw,
            "expires_in": 120,
            "choices": choices,
        }

    @staticmethod
    async def login_user(
        email: str, password: str, tenant_id: str | None = None, client_ip: str | None = None
    ) -> dict:
        """Business logic for user login.

        Returns EITHER a session dict {"access_token": str, "refresh_token": str}
        -- exactly one school matched the submitted password, or the caller
        explicitly named a school that matched -- OR, when the password
        verifies against more than one candidate school and none was named,
        a selection-required dict {"status": "select_tenant",
        "selection_token": str, "expires_in": int, "choices": [...]}. Only
        raises ValueError for a genuine credential/account failure; ambiguity
        is a return value, not an exception (see login_redeem for how a
        selection_token becomes a real session).
        """
        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)
        email = email.strip().lower()

        tenant_ids, parent, super_admin = await AuthService._enumerate_login_tenants(cp_repo, email)

        if tenant_id:
            # `tenant_id` reaches here straight from the request body
            # (UserLoginRequest.tenant_id -> auth/router.py), so it is
            # arbitrary caller-controlled input naming any school on the
            # platform. Rejected on membership alone, before ever fetching a
            # per-tenant password hash -- the message is deliberately the
            # generic credential error: whether a given school exists, and
            # whether this person belongs to it, are both things an
            # unauthenticated caller must not be able to probe.
            if tenant_id not in tenant_ids:
                raise ValueError("Invalid email or password")
            chosen = await AuthService._fetch_login_candidate(
                cp_repo, email, tenant_id, parent, super_admin
            )
            if not chosen or not await AuthService.verify_password_async(password, chosen["hash"]):
                raise ValueError("Invalid email or password")
            AuthService._schedule_keycloak_sync(email, password, chosen)
            return await AuthService._complete_login(cp_repo, chosen, email, parent, client_ip)

        if not tenant_ids:
            # No membership row anywhere -- try the deprecated tenant-scan
            # fallback before giving up. FAIL CLOSED either way: this used
            # to be `tenant_id = "tenant_a"` -- the first real school -- so
            # any login whose tenant could not be resolved was
            # authenticated against school A's users table.
            fallback = await AuthService._legacy_scan_for_candidate(cp_repo, email)
            if not fallback:
                raise ValueError(
                    "This account is not associated with a school. Ask an "
                    "administrator to invite you to one."
                )
            if not await AuthService.verify_password_async(password, fallback["hash"]):
                raise ValueError("Invalid email or password")
            AuthService._schedule_keycloak_sync(email, password, fallback)
            return await AuthService._complete_login(cp_repo, fallback, email, parent, client_ip)

        # Bounds both the threadpool consumption and the timing surface below --
        # an address with an implausible number of memberships cannot turn one
        # login attempt into an ever-larger verification burst.
        candidates = [
            c
            for c in await asyncio.gather(
                *[
                    AuthService._fetch_login_candidate(cp_repo, email, tid, parent, super_admin)
                    for tid in list(tenant_ids)[:8]
                ]
            )
            if c
        ]
        matched = await AuthService._verify_candidates_padded(candidates, password)
        if not matched:
            raise ValueError("Invalid email or password")
        # Every matched candidate just had this password verified against it
        # (whether or not it ends up being the one the caller lands in), so
        # each is a legitimate opportunity to self-heal a missing Keycloak
        # account -- not just the one eventually chosen via _complete_login
        # or a later login_redeem, which never sees the plaintext password.
        for candidate in matched:
            AuthService._schedule_keycloak_sync(email, password, candidate)
        if len(matched) == 1:
            return await AuthService._complete_login(cp_repo, matched[0], email, parent, client_ip)

        # Refuse to guess. Which school a two-school user lands in must be
        # their choice, not an ORDER BY.
        return await AuthService._issue_login_selection_challenge(
            cp_repo, email, matched, client_ip
        )

    @staticmethod
    async def login_redeem(
        selection_token: str, tenant_id: str, client_ip: str | None = None
    ) -> dict:
        """Exchange a single-use selection challenge for a real session.

        Redemption (the atomic UPDATE ... RETURNING in
        redeem_selection_challenge) is itself the credential proof -- the
        password was already verified against this exact tenant_id's store
        when the challenge was minted, seconds ago, inside its 2-minute
        window. No second password check happens here.
        """
        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)

        row = await cp_repo.redeem_selection_challenge(
            AuthService._hash_opaque_token(selection_token)
        )
        if not row or tenant_id not in row["matched_tenants"]:
            raise ValueError("Invalid or expired selection token")

        identity_id = row["identity_id"]
        try:
            # membership.role (when there is one) tells _resolve_local_account
            # whether this is the parent branch; a matched super_admin
            # candidate carries tenant_id == "" and no membership row.
            membership = None
            if tenant_id:
                membership = await cp_repo.get_membership(identity_id, tenant_id)
            hint_role = "super_admin" if tenant_id == "" else (membership or {}).get("role", "")
            user_id, role, roles = await AuthService._resolve_local_account(
                cp_repo, identity_id, tenant_id, hint_role
            )
        except ValueError as exc:
            raise ValueError("Invalid or expired selection token") from exc

        email = (await cp_repo.get_identity_by_id(identity_id))["email"]
        access_token = AuthService.create_access_token(
            user_id,
            tenant_id=tenant_id or None,
            role=role,
            email=email,
            roles=roles,
            identity_id=identity_id,
        )
        if tenant_id:
            await cp_repo.touch_membership_last_active(identity_id, tenant_id)

        refresh_token = await AuthService.issue_refresh_token(
            cp_repo, identity_id, tenant_id, role, client_ip
        )
        return {"access_token": access_token, "refresh_token": refresh_token}

    @staticmethod
    async def change_password(
        user_id,
        email: str,
        role: str,
        tenant_id: str | None,
        current_password: str,
        new_password: str,
    ) -> bool:
        """Self-service password change: verify current_password against the
        SAME record login_user reads for this role, then overwrite it there.

        There are three disjoint password stores depending on role (mirroring
        login_user's own branching): public.super_admins, the global
        public.parents row, and each tenant's own users table. Writing to the
        wrong one would let the change "succeed" while login keeps checking
        the untouched hash -- so which store to hit is dictated by role, not
        by which schema happens to be on the connection's search_path.

        Returns whether the Keycloak SSO credential was also updated, so the
        caller can tell an account with no Keycloak record (or a Keycloak
        that's temporarily unreachable) from a fully-synced one -- the local
        change always succeeds or raises before this matters, this return
        value only distinguishes "SSO login has the new password too" from
        "SSO login still has the old one, try again shortly."
        """
        if new_password == current_password:
            raise ValueError("New password must be different from the current password")

        managed_placeholders = {"managed", "keycloak_managed"}
        sso_managed_message = (
            "This account's password is managed by single sign-on and cannot " "be changed here."
        )

        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)

        if role == "super_admin":
            record = await cp_repo.get_super_admin_by_email(email)
            if not record:
                raise ValueError("Account not found")
            stored_hash = record["password_hash"]
            if stored_hash in managed_placeholders:
                raise PermissionError(sso_managed_message)
            if not await AuthService.verify_password_async(current_password, stored_hash):
                raise ValueError("Current password is incorrect")
            await cp_repo.update_super_admin_password(
                record["id"], await AuthService.hash_password_async(new_password)
            )
            return update_user_password_in_keycloak(email, new_password)

        if role == "parent":
            record = await cp_repo.get_parent_by_email(email)
            if not record:
                raise ValueError("Account not found")
            stored_hash = record["password_hash"]
            if stored_hash in managed_placeholders:
                raise PermissionError(sso_managed_message)
            if not await AuthService.verify_password_async(current_password, stored_hash):
                raise ValueError("Current password is incorrect")
            new_hash = await AuthService.hash_password_async(new_password)
            await cp_repo.update_parent_password(record["id"], new_hash)
            # Best-effort: keep the tenant-local mirror row (created at first
            # login/registration into this school) from drifting, even though
            # login_user's parent branch never reads it for authentication.
            if tenant_id:
                tenant_pool = await get_db_pool(tenant_id)
                user_repo = UserRepository(tenant_pool)
                local_user = await user_repo.get_user_by_email(email)
                if local_user:
                    await user_repo.update_user_password_hash(local_user["id"], new_hash)
            return update_user_password_in_keycloak(email, new_password)

        # Every other tenant-scoped role (school_admin, teacher, student,
        # manager, event_teacher, pending) authenticates against the tenant's
        # own users table.
        if not tenant_id:
            raise ValueError("Account not found")
        tenant_pool = await get_db_pool(tenant_id)
        user_repo = UserRepository(tenant_pool)
        user = await user_repo.get_user_by_email(email)
        if not user:
            raise ValueError("Account not found")
        stored_hash = user["password_hash"]
        if stored_hash in managed_placeholders:
            raise PermissionError(sso_managed_message)
        if not await AuthService.verify_password_async(current_password, stored_hash):
            raise ValueError("Current password is incorrect")
        await user_repo.update_user_password_hash(
            user["id"], await AuthService.hash_password_async(new_password)
        )
        return update_user_password_in_keycloak(email, new_password)

    @staticmethod
    async def list_tenants() -> list[dict]:
        """Fetch list of all tenants from control plane DB."""
        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)
        return await cp_repo.get_all_tenants()

    @staticmethod
    async def create_tenant(tenant_id: str, name: str) -> dict:
        """Create a new tenant: control-plane row, Postgres schema, Keycloak org.

        A tenant and its Keycloak Organization are provisioned together or not at
        all. The organization alias is what Keycloak puts in the `organization`
        claim, and that claim is how a request resolves to a tenant -- so a school
        created without one is a school whose users authenticate successfully and
        then fail tenant resolution on every single request. Provisioning it first
        means a Keycloak outage refuses the create loudly instead of leaving that
        state behind.
        """
        # 1. Keycloak organization first -- this is the step allowed to fail.
        #    create_keycloak_organization raises rather than warning, and is a
        #    no-op if the organization already exists.
        create_keycloak_organization(tenant_id, name=name)

        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)

        # 2. Insert tenant in control plane
        await cp_repo.create_tenant(tenant_id=tenant_id, name=name)

        # 3. Trigger schema creation and table initialization in PostgreSQL
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
    async def assign_user_role(
        tenant_id: str,
        email: str,
        new_role: str,
        requesting_user_roles: str | list[str] | None = None,
    ) -> dict:
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

        if new_role == "super_admin":
            caller_roles = (
                {requesting_user_roles}
                if isinstance(requesting_user_roles, str)
                else set(requesting_user_roles or [])
            )
            if "super_admin" not in caller_roles:
                raise PermissionError("Only a super_admin can grant the super_admin role")

            from app.core.config import SUPER_ADMIN_ALLOWED_EMAIL

            # Same single-identity invariant as AuthService.register_user: an
            # existing super_admin granting the role through the Permission
            # Matrix editor must not be able to mint a second one either.
            if email.strip().lower() != SUPER_ADMIN_ALLOWED_EMAIL.strip().lower():
                raise PermissionError(
                    "super_admin is restricted to the platform's single designated "
                    "operator account and cannot be granted to another email."
                )

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
                    with contextlib.suppress(Exception):
                        await conn_t.execute(
                            "INSERT INTO parenets (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                            user_id,
                            user_display_name,
                        )
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
