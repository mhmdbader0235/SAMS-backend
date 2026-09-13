"""
Unit tests for the X-Tenant-ID / ?tenant_id= override in get_current_user().

That header/query param selects which tenant a request acts in. For a
super_admin it is an ASSERTION — trusted, because their access is already
total. For everyone else it is a SELECTION, verified against their own
`user_tenant_map`/`parent_tenant_links` membership set: naming a tenant
they're not a member of is a 403, never a silent fallback to their own
tenant — a silent fallback would let a client mistake "my header was
ignored" for "my header succeeded" and mislabel one school's data as
another's. See the multi-school access plan, Phase 3.

No real Postgres connection is used: AuthService.decode_access_token and
app.core.database.get_db_pool / get_control_plane_pool are mocked so the
cascade runs against fixed, in-memory data only.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import app.core.database as db_module
from app.core.dependencies import get_current_user
from app.domains.auth.service import AuthService


class FakeRequest:
    """Minimal stand-in for fastapi.Request — only .headers/.query_params are read."""

    def __init__(self, headers=None, query_params=None):
        self.headers = headers or {}
        self.query_params = query_params or {}


class FakeConn:
    def __init__(self, user_row):
        self._user_row = user_row

    async def fetchrow(self, *_args, **_kwargs):
        return self._user_row

    async def fetchval(self, *_args, **_kwargs):
        return None

    async def execute(self, *_args, **_kwargs):
        return None


class FakeAcquireCtx:
    def __init__(self, user_row):
        self._conn = FakeConn(user_row)

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_exc):
        return False


class FakePool:
    """Stands in for the asyncpg.Pool returned by get_db_pool()/get_control_plane_pool().

    `membership_rows` backs ControlPlaneRepository.get_tenants_for_email (a
    plain `self.pool.fetch(...)` call) — the membership set the X-Tenant-ID
    selection tier verifies against. Empty by default, matching "this user
    belongs to no other school."
    """

    def __init__(self, user_row=None, membership_rows=None):
        self._user_row = user_row
        self._membership_rows = membership_rows or []

    def acquire(self):
        return FakeAcquireCtx(self._user_row)

    async def fetchrow(self, *_args, **_kwargs):
        return self._user_row

    async def fetchval(self, *_args, **_kwargs):
        return None

    async def fetch(self, *_args, **_kwargs):
        return self._membership_rows

    async def execute(self, *_args, **_kwargs):
        return None


def _credentials() -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake-token")


class TestTenantOverrideSecurity:
    async def test_non_member_header_selection_is_rejected(self):
        """A school_admin of tenant_a with NO membership at tenant_b gets 403, not a silent keep.

        Renamed from test_non_super_admin_header_override_is_ignored: the
        security property (a client-controlled header can't reach another
        tenant) is unchanged, but it's now enforced by a verified membership
        lookup rather than by role, and a loud 403 proves that more directly
        than a silent fallback would.
        """
        payload = {
            "sub": "user-admin-a",
            "email": "admin@tenant-a.example.com",
            "role": "school_admin",
            "tenant_id": "tenant_a",
        }
        own_tenant_user_row = {
            "id": 501,
            "role": "school_admin",
            "roles": [],
            "permissions": [],
        }
        fake_pool = FakePool(user_row=own_tenant_user_row, membership_rows=[])

        with (
            patch.object(AuthService, "decode_access_token", return_value=payload),
            patch.object(db_module, "get_db_pool", AsyncMock(return_value=fake_pool)),
            patch.object(db_module, "get_control_plane_pool", AsyncMock(return_value=fake_pool)),
        ):
            request = FakeRequest(headers={"x-tenant-id": "tenant_b"})
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(request=request, credentials=_credentials())

        assert exc_info.value.status_code == 403

    async def test_non_member_query_param_selection_is_rejected(self):
        """Same guarantee for the ?tenant_id= query param path, not just the header."""
        payload = {
            "sub": "user-teacher-a",
            "email": "teacher@tenant-a.example.com",
            "role": "teacher",
            "tenant_id": "tenant_a",
        }
        own_tenant_user_row = {
            "id": 777,
            "role": "teacher",
            "roles": [],
            "permissions": [],
        }
        fake_pool = FakePool(user_row=own_tenant_user_row, membership_rows=[])

        with (
            patch.object(AuthService, "decode_access_token", return_value=payload),
            patch.object(db_module, "get_db_pool", AsyncMock(return_value=fake_pool)),
            patch.object(db_module, "get_control_plane_pool", AsyncMock(return_value=fake_pool)),
        ):
            request = FakeRequest(query_params={"tenant_id": "tenant_b"})
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(request=request, credentials=_credentials())

        assert exc_info.value.status_code == 403

    async def test_member_can_select_second_tenant_via_header(self):
        """A teacher at tenant_a who is also a parent at tenant_b can select tenant_b.

        Pins Phase 3's two halves together: the header is accepted AND the
        resulting role is rebuilt from tenant_b's own `users` row (parent),
        not unioned with tenant_a's (teacher).
        """
        payload = {
            "sub": "user-multi",
            "email": "multi@example.com",
            "role": "teacher",
            "tenant_id": "tenant_a",
        }
        # get_db_pool is patched to always return this same fake pool
        # regardless of which tenant_id it's called with, so its user_row
        # models tenant_b's own `users` row -- the one the selection tier
        # must resolve role from.
        tenant_b_user_row = {
            "id": 42,
            "role": "parent",
            "roles": [],
            "permissions": [],
        }
        fake_pool = FakePool(
            user_row=tenant_b_user_row,
            membership_rows=[{"tenant_id": "tenant_b", "role": "parent"}],
        )

        with (
            patch.object(AuthService, "decode_access_token", return_value=payload),
            patch.object(db_module, "get_db_pool", AsyncMock(return_value=fake_pool)),
            patch.object(db_module, "get_control_plane_pool", AsyncMock(return_value=fake_pool)),
        ):
            request = FakeRequest(headers={"x-tenant-id": "tenant_b"})
            user = await get_current_user(request=request, credentials=_credentials())

        assert user.tenant_id == "tenant_b"
        assert user.role == "parent"
        assert "teacher" not in user.roles

    async def test_super_admin_header_override_still_works(self):
        """A super_admin sending X-Tenant-ID must still be able to switch tenant context."""
        payload = {
            "sub": "user-super",
            "email": "root@platform.example.com",
            "role": "super_admin",
            "tenant_id": "tenant_a",
        }
        # super_admin has no real per-tenant `users` row in most schools (see the
        # comment in get_current_user) — fetchrow returning None exercises that path.
        fake_pool = FakePool(user_row=None)

        with (
            patch.object(AuthService, "decode_access_token", return_value=payload),
            patch.object(db_module, "get_db_pool", AsyncMock(return_value=fake_pool)),
            patch.object(db_module, "get_control_plane_pool", AsyncMock(return_value=fake_pool)),
        ):
            request = FakeRequest(headers={"x-tenant-id": "tenant_c"})
            user = await get_current_user(request=request, credentials=_credentials())

        assert user.tenant_id == "tenant_c"
        assert user.role == "super_admin"

    async def test_super_admin_query_param_override_still_works(self):
        payload = {
            "sub": "user-super-2",
            "email": "root2@platform.example.com",
            "role": "super_admin",
            "tenant_id": "tenant_a",
        }
        fake_pool = FakePool(user_row=None)

        with (
            patch.object(AuthService, "decode_access_token", return_value=payload),
            patch.object(db_module, "get_db_pool", AsyncMock(return_value=fake_pool)),
            patch.object(db_module, "get_control_plane_pool", AsyncMock(return_value=fake_pool)),
        ):
            request = FakeRequest(query_params={"tenant_id": "tenant_c"})
            user = await get_current_user(request=request, credentials=_credentials())

        assert user.tenant_id == "tenant_c"
