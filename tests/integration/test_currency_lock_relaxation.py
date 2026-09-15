"""Currency change after activation — relaxed, but only for a super_admin,
only before any money has been recorded, and only to a valid ISO-4217 code.

tenant_a ships pre-activated with currency 'JOD' (see back/init.sql). These
tests always restore that in a finally block, since school_profile is not
truncated by the clean_db fixture and would otherwise leak into other tests.
"""

import asyncpg
from httpx import AsyncClient

from tests.integration._helpers import get_super_admin_token, register_school_admin


async def _register_super_admin(test_client: AsyncClient) -> dict:
    # Shared helper, not a fresh registration: register_school_admin (called
    # later in some of these tests) also needs the platform's one super_admin
    # identity, and a second /register attempt for the same email 400s.
    token = await get_super_admin_token(test_client)
    return {"Authorization": f"Bearer {token}"}


async def test_super_admin_can_change_currency_on_a_clean_activated_tenant(
    test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
):
    headers = await _register_super_admin(test_client)
    try:
        resp = await test_client.put(
            "/api/v1/school/profile", json={"currency": "USD"}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["currency"] == "USD"
    finally:
        await db_pool.execute("UPDATE school_profile SET currency = 'JOD'")


async def test_currency_change_blocked_once_money_is_recorded(
    test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
):
    sa_headers = await _register_super_admin(test_client)
    try:
        # Build a minimal level -> class -> event chain with a non-zero
        # ticket price, i.e. real money recorded via the normal API path
        # rather than a raw insert bypassing it. event.created_by is a
        # BIGINT (a users.id), which a super_admin account (a UUID row in
        # a separate table) can't satisfy -- a school_admin creates these.
        admin_token = await register_school_admin(test_client, "admin_currency_blocked@school.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        lvl_r = await test_client.post(
            "/api/v1/students/levels", json={"name": "Currency Test Grade"}, headers=admin_headers
        )
        assert lvl_r.status_code == 200, lvl_r.text
        level_id = lvl_r.json()["level_id"]

        cls_r = await test_client.post(
            "/api/v1/students/classes",
            json={"name": "Currency Test Class", "level_id": level_id},
            headers=admin_headers,
        )
        assert cls_r.status_code == 200, cls_r.text
        class_id = cls_r.json()["id"]

        event_r = await test_client.post(
            "/api/v1/events",
            json={
                "title": "Currency Test Trip",
                "date": "2027-01-01T09:00:00Z",
                "class_mappings": [{"class_id": class_id, "ticket_price": 10.0}],
            },
            headers=admin_headers,
        )
        assert event_r.status_code == 200, event_r.text

        resp = await test_client.put(
            "/api/v1/school/profile", json={"currency": "USD"}, headers=sa_headers
        )
        assert resp.status_code == 403
    finally:
        await db_pool.execute("UPDATE school_profile SET currency = 'JOD'")


async def test_school_admin_cannot_change_currency_after_activation(
    test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
):
    token = await register_school_admin(test_client, "sadmin_currency@school.com")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = await test_client.put(
            "/api/v1/school/profile", json={"currency": "USD"}, headers=headers
        )
        assert resp.status_code == 403
    finally:
        await db_pool.execute("UPDATE school_profile SET currency = 'JOD'")


async def test_invalid_currency_code_rejected(
    test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
):
    headers = await _register_super_admin(test_client)
    resp = await test_client.put(
        "/api/v1/school/profile", json={"currency": "XYZ"}, headers=headers
    )
    assert resp.status_code == 422
