"""
Unit tests for membership caching and schema migration health endpoint.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import (
    _MEMBERSHIP_CACHE,
    CurrentUser,
    get_current_user,
    invalidate_membership_cache,
)
from app.main import app


def test_membership_cache_invalidation():
    _MEMBERSHIP_CACHE["test@school.com"] = (100.0, [{"tenant_id": "tenant_a", "role": "teacher"}])
    _MEMBERSHIP_CACHE["other@school.com"] = (100.0, [{"tenant_id": "tenant_b", "role": "parent"}])

    invalidate_membership_cache("test@school.com")
    assert "test@school.com" not in _MEMBERSHIP_CACHE
    assert "other@school.com" in _MEMBERSHIP_CACHE

    invalidate_membership_cache()
    assert len(_MEMBERSHIP_CACHE) == 0


@pytest.mark.asyncio
async def test_schema_status_forbidden_for_non_super_admin():
    async def override_user():
        return CurrentUser(user_id="usr_1", tenant_id="tenant_a", role="teacher", roles=["teacher"])

    app.dependency_overrides[get_current_user] = override_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/health/schema-status")
            assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_schema_status_success_for_super_admin():
    async def override_super_admin():
        return CurrentUser(user_id="sa_1", tenant_id=None, role="super_admin", roles=["super_admin"])

    mock_cp_pool = AsyncMock()
    mock_cp_pool.fetchval.return_value = "cp_0008"

    mock_t_pool = AsyncMock()
    mock_t_pool.fetchval.return_value = "tenant_0007"

    mock_cp_repo = MagicMock()
    mock_cp_repo.get_all_tenants = AsyncMock(return_value=[{"tenant_id": "tenant_a"}])

    app.dependency_overrides[get_current_user] = override_super_admin
    try:
        with (
            patch("app.core.database.get_control_plane_pool", new=AsyncMock(return_value=mock_cp_pool)),
            patch("app.domains.tenant.control_plane_repository.ControlPlaneRepository", return_value=mock_cp_repo),
            patch("app.core.database.get_db_pool", new=AsyncMock(return_value=mock_t_pool)),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/health/schema-status")
                assert resp.status_code == 200
                data = resp.json()
                assert data["control_plane"]["current_revision"] == "cp_0008"
                assert data["control_plane"]["status"] == "ok"
                assert len(data["tenants"]) == 1
                assert data["tenants"][0]["tenant_id"] == "tenant_a"
                assert data["tenants"][0]["current_revision"] == "tenant_0007"
                assert data["all_healthy"] is True
    finally:
        app.dependency_overrides.pop(get_current_user, None)
