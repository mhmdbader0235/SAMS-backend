"""Unit tests for fail-closed tenant resolution in get_current_user().

Tenant resolution used to end in `tenant_id = "tenant_a"`. tenant_a is not a
neutral placeholder -- it is the first real school -- so any token whose tenant
could not be resolved silently read and wrote that school's data, and the
JIT-provisioning branch would create a 'pending' user inside it. These tests pin
the replacement behaviour: an unresolvable tenant is an error, never a guess.

They also cover the Keycloak Organization claim, which is a flat ARRAY of
organization aliases whose order is HashSet iteration over organization UUIDs
(verified against Keycloak 26.7). One membership resolves; several must NOT
resolve to an arbitrary element.

The X-Tenant-ID / ?tenant_id= override itself is covered separately and more
fully in test_tenant_override_security.py; the cases here are the ones that
interact with failing closed.

No real Postgres connection is used -- AuthService.decode_access_token and
app.core.database.get_db_pool / get_control_plane_pool are mocked.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import app.core.database as db_module
from app.core.dependencies import get_current_user
from app.domains.auth.service import AuthService


class FakeRequest:
    """Minimal stand-in for fastapi.Request -- only .headers/.query_params are read."""

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

    def __init__(self, user_row=None, rows=None):
        self._user_row = user_row
        self._rows = rows or []

    def acquire(self):
        return FakeAcquireCtx(self._user_row)

    async def fetchrow(self, *_args, **_kwargs):
        return self._user_row

    async def fetch(self, *_args, **_kwargs):
        return self._rows

    async def fetchval(self, *_args, **_kwargs):
        return None

    async def execute(self, *_args, **_kwargs):
        return None


def _credentials() -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake-token")


def _patches(pool):
    """The three seams get_current_user reaches through."""
    return (
        patch.object(db_module, "get_db_pool", AsyncMock(return_value=pool)),
        patch.object(db_module, "get_control_plane_pool", AsyncMock(return_value=pool)),
    )


class TestFailClosed:
    async def test_unresolvable_tenant_raises_400_and_does_not_fall_back_to_tenant_a(self):
        """The core regression: no tenant anywhere must 400, not silently pick tenant_a.

        The token carries no tenant_id, no organization claim and no attributes;
        user_tenant_map has no row (fetchrow -> None) and the cross-tenant scan
        finds nothing. Previously this produced tenant_id == "tenant_a".
        """
        payload = {
            "sub": "user-orphan",
            "email": "orphan@nowhere.example.com",
            "role": "student",
        }
        fake_pool = FakePool(user_row=None)
        p1, p2 = _patches(fake_pool)

        with (
            patch.object(AuthService, "decode_access_token", return_value=payload),
            p1,
            p2,
            pytest.raises(HTTPException) as exc_info,
        ):
            await get_current_user(request=FakeRequest(), credentials=_credentials())

        assert exc_info.value.status_code == 400
        assert "not associated with a school" in str(exc_info.value.detail)

    async def test_realm_named_tenant_is_rejected_not_defaulted(self):
        """A tenant_id of 'sams'/'schooldesk'/'master' is the realm name, not a school.

        These are treated as unresolved. They used to collapse to tenant_a.
        """
        for bogus in ("sams", "schooldesk", "master"):
            payload = {
                "sub": "user-realm",
                "email": "realm@nowhere.example.com",
                "role": "teacher",
                "tenant_id": bogus,
            }
            fake_pool = FakePool(user_row=None)
            p1, p2 = _patches(fake_pool)

            with (
                patch.object(AuthService, "decode_access_token", return_value=payload),
                p1,
                p2,
                pytest.raises(HTTPException) as exc_info,
            ):
                await get_current_user(request=FakeRequest(), credentials=_credentials())

            assert exc_info.value.status_code == 400, f"{bogus!r} should not resolve"

    async def test_super_admin_without_selection_gets_none_not_tenant_a(self):
        """A super_admin has no home tenant; they must get None, never a real school.

        Login mints their token with tenant_id="". Defaulting that to tenant_a
        pointed every platform-operator request at school A by accident.
        """
        payload = {
            "sub": "user-super",
            "email": "root@platform.example.com",
            "role": "super_admin",
            "tenant_id": "",
        }
        fake_pool = FakePool(user_row=None)
        p1, p2 = _patches(fake_pool)

        with patch.object(AuthService, "decode_access_token", return_value=payload), p1, p2:
            user = await get_current_user(request=FakeRequest(), credentials=_credentials())

        assert user.tenant_id is None
        assert user.tenant_id != "tenant_a"
        assert user.role == "super_admin"

    async def test_super_admin_header_override_still_selects_a_tenant(self):
        """Failing closed must not break the super_admin's ability to pick a school."""
        payload = {
            "sub": "user-super-2",
            "email": "root2@platform.example.com",
            "role": "super_admin",
            "tenant_id": "",
        }
        fake_pool = FakePool(user_row=None)
        p1, p2 = _patches(fake_pool)

        with patch.object(AuthService, "decode_access_token", return_value=payload), p1, p2:
            user = await get_current_user(
                request=FakeRequest(headers={"x-tenant-id": "tenant_b"}),
                credentials=_credentials(),
            )

        assert user.tenant_id == "tenant_b"

    async def test_non_super_admin_header_override_ignored_and_own_tenant_kept(self):
        """A teacher of tenant_a sending X-Tenant-ID: tenant_b stays on tenant_a."""
        payload = {
            "sub": "user-teacher",
            "email": "teacher@tenant-a.example.com",
            "role": "teacher",
            "tenant_id": "tenant_a",
        }
        fake_pool = FakePool(user_row={"id": 42, "role": "teacher", "roles": [], "permissions": []})
        p1, p2 = _patches(fake_pool)

        with patch.object(AuthService, "decode_access_token", return_value=payload), p1, p2:
            user = await get_current_user(
                request=FakeRequest(headers={"x-tenant-id": "tenant_b"}),
                credentials=_credentials(),
            )

        assert user.tenant_id == "tenant_a"
        assert user.tenant_id != "tenant_b"


class TestOrganizationClaim:
    async def test_single_organization_membership_resolves_the_tenant(self):
        """One alias in the array is unambiguous, so it resolves directly."""
        payload = {
            "sub": "kc-user",
            "email": "teacher@schoola.example.com",
            "organization": ["tenant_b"],
        }
        fake_pool = FakePool(user_row={"id": 7, "role": "teacher", "roles": [], "permissions": []})
        p1, p2 = _patches(fake_pool)

        with patch.object(AuthService, "decode_access_token", return_value=payload), p1, p2:
            user = await get_current_user(request=FakeRequest(), credentials=_credentials())

        assert user.tenant_id == "tenant_b"

    async def test_multiple_memberships_do_not_resolve_to_an_arbitrary_element(self):
        """Several memberships must not silently pick one.

        The claim's array order is HashSet iteration over organization UUIDs, so
        element [0] is not "the primary school" -- it is whichever one the JVM
        happened to hash first. The old code did exactly `org[0]`. With nothing
        else able to resolve the tenant, this must now fail closed rather than
        guess between them.
        """
        payload = {
            "sub": "kc-multi",
            "email": "parent-of-two@example.com",
            "organization": ["tenant_a", "tenant_b"],
        }
        fake_pool = FakePool(user_row=None)
        p1, p2 = _patches(fake_pool)

        with (
            patch.object(AuthService, "decode_access_token", return_value=payload),
            p1,
            p2,
            pytest.raises(HTTPException) as exc_info,
        ):
            await get_current_user(request=FakeRequest(), credentials=_credentials())

        assert exc_info.value.status_code == 400

    async def test_organization_claim_as_bare_string_still_resolves(self):
        """Defensive: a non-multivalued mapper config emits a string, not an array."""
        payload = {
            "sub": "kc-user-str",
            "email": "someone@schoolb.example.com",
            "organization": "tenant_c",
        }
        fake_pool = FakePool(user_row={"id": 9, "role": "student", "roles": [], "permissions": []})
        p1, p2 = _patches(fake_pool)

        with patch.object(AuthService, "decode_access_token", return_value=payload), p1, p2:
            user = await get_current_user(request=FakeRequest(), credentials=_credentials())

        assert user.tenant_id == "tenant_c"
