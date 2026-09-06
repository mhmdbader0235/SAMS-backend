"""GET/PUT /api/v1/auth/me/preferences — a user's own locale override."""

import asyncpg
from httpx import AsyncClient

from tests.integration._helpers import register_school_admin


async def test_preferences_default_to_null(
    test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
):
    token = await register_school_admin(test_client, "prefs_admin@desk.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await test_client.get("/api/v1/auth/me/preferences", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["preferred_language"] is None
    assert body["preferred_timezone"] is None


async def test_put_then_get_reflects_the_new_value(
    test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
):
    token = await register_school_admin(test_client, "prefs_admin2@desk.com")
    headers = {"Authorization": f"Bearer {token}"}

    put_resp = await test_client.put(
        "/api/v1/auth/me/preferences",
        json={"preferred_timezone": "Asia/Amman"},
        headers=headers,
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["preferred_timezone"] == "Asia/Amman"

    get_resp = await test_client.get("/api/v1/auth/me/preferences", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["preferred_timezone"] == "Asia/Amman"


async def test_invalid_timezone_rejected(test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
    token = await register_school_admin(test_client, "prefs_admin3@desk.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await test_client.put(
        "/api/v1/auth/me/preferences",
        json={"preferred_timezone": "Not/AZone"},
        headers=headers,
    )
    assert resp.status_code == 422
