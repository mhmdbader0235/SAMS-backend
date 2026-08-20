"""
Unit tests for the X-Tenant-ID / ?tenant_id= override in get_current_user().

That override exists so a super_admin can inspect/manage a tenant other than
their own by sending a header. It must never let a non-super_admin retarget
a request at someone else's tenant just by sending the same header — that
would be a straightforward cross-tenant data leak, since the header/query
param is fully client-controlled.

No real Postgres connection is used: AuthService.decode_access_token and
app.core.database.get_db_pool / get_control_plane_pool are mocked so the
cascade runs against fixed, in-memory data only.
"""

from unittest.mock import AsyncMock, patch

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
    """Stands in for the asyncpg.Pool returned by get_db_pool()/get_control_plane_pool()."""

    def __init__(self, user_row=None):
        self._user_row = user_row

    def acquire(self):
        return FakeAcquireCtx(self._user_row)

    async def fetchrow(self, *_args, **_kwargs):
        return self._user_row

    async def fetchval(self, *_args, **_kwargs):
        return None

    async def execute(self, *_args, **_kwargs):
        return None


def _credentials() -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake-token")


class TestTenantOverrideSecurity:
    async def test_non_super_admin_header_override_is_ignored(self):
        """A school_admin of tenant_a sending X-Tenant-ID: tenant_b must stay on tenant_a."""
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
        fake_pool = FakePool(user_row=own_tenant_user_row)

        with patch.object(AuthService, "decode_access_token", return_value=payload), \
             patch.object(db_module, "get_db_pool", AsyncMock(return_value=fake_pool)), \
             patch.object(db_module, "get_control_plane_pool", AsyncMock(return_value=fake_pool)):
            request = FakeRequest(headers={"x-tenant-id": "tenant_b"})
            user = await get_current_user(request=request, credentials=_credentials())

        assert user.tenant_id == "tenant_a"
        assert user.tenant_id != "tenant_b"
        assert user.role == "school_admin"

    async def test_non_super_admin_query_param_override_is_ignored(self):
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
        fake_pool = FakePool(user_row=own_tenant_user_row)

        with patch.object(AuthService, "decode_access_token", return_value=payload), \
             patch.object(db_module, "get_db_pool", AsyncMock(return_value=fake_pool)), \
             patch.object(db_module, "get_control_plane_pool", AsyncMock(return_value=fake_pool)):
            request = FakeRequest(query_params={"tenant_id": "tenant_b"})
            user = await get_current_user(request=request, credentials=_credentials())

        assert user.tenant_id == "tenant_a"

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

        with patch.object(AuthService, "decode_access_token", return_value=payload), \
             patch.object(db_module, "get_db_pool", AsyncMock(return_value=fake_pool)), \
             patch.object(db_module, "get_control_plane_pool", AsyncMock(return_value=fake_pool)):
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

        with patch.object(AuthService, "decode_access_token", return_value=payload), \
             patch.object(db_module, "get_db_pool", AsyncMock(return_value=fake_pool)), \
             patch.object(db_module, "get_control_plane_pool", AsyncMock(return_value=fake_pool)):
            request = FakeRequest(query_params={"tenant_id": "tenant_c"})
            user = await get_current_user(request=request, credentials=_credentials())

        assert user.tenant_id == "tenant_c"
