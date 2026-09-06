"""Regression test for the audit log's same-transaction guarantee.

invariant: an audit row must be written if and only if the business write it
audits actually commits. `TenantRepository.delete_class`/`delete_level` (and
any future audited write) call `AuditService.record` on the SAME connection
and transaction as their own SQL -- so a rollback anywhere in that
transaction takes the audit row down with it, and there is no code path
where "audited" and "actually happened" can disagree.

`delete_class` itself opens and commits its own transaction internally (by
design -- see its docstring), so there is no externally-observable rollback
path through the public service API to exercise here. This test instead
proves the underlying mechanism directly: an audit write sharing a
connection with a business write rolls back together, and commits together,
exactly the guarantee `delete_class`'s `audit_hook` relies on.
"""

import asyncpg
from httpx import AsyncClient

from app.domains.audit.service import AuditService
from tests.integration._helpers import register_school_admin


class _IntentionalRollback(Exception):
    """Raised inside a transaction block purely to force asyncpg to roll
    back everything in it; caught immediately by the test itself."""


async def test_audit_row_is_rolled_back_with_its_business_write(db_pool: asyncpg.Pool, clean_db):
    try:
        async with db_pool.acquire() as conn, conn.transaction():
            await AuditService.record(
                conn,
                actor=None,
                action="class.delete",
                entity_type="class",
                entity_id=999,
                outcome="allow",
            )
            await conn.execute("SELECT 1")  # stand-in for the business write
            raise _IntentionalRollback
    except _IntentionalRollback:
        pass

    count = await db_pool.fetchval("SELECT count(*) FROM audit_log WHERE entity_id = '999'")
    assert count == 0


async def test_audit_row_commits_with_its_business_write(db_pool: asyncpg.Pool, clean_db):
    async with db_pool.acquire() as conn, conn.transaction():
        await AuditService.record(
            conn,
            actor=None,
            action="class.delete",
            entity_type="class",
            entity_id=1000,
            outcome="allow",
        )

    count = await db_pool.fetchval("SELECT count(*) FROM audit_log WHERE entity_id = '1000'")
    assert count == 1


async def test_deleting_a_class_writes_exactly_one_audit_row(
    test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
):
    """End-to-end proof that TenantService.delete_class's audit_hook (module
    roadmap A4.3) actually fires through the real HTTP path, not just in the
    isolated mechanism tests above."""
    token = await register_school_admin(test_client, "admin_audit_delete@school.com")
    headers = {"Authorization": f"Bearer {token}"}

    level_resp = await test_client.post(
        "/api/v1/students/levels",
        json={
            "name": "Grade 6",
            "isced_level": 1,
            "age_band_min": 11,
            "age_band_max": 12,
            "ordinal": 6,
        },
        headers=headers,
    )
    assert level_resp.status_code == 200, level_resp.text
    level_id = level_resp.json()["level_id"]

    class_resp = await test_client.post(
        "/api/v1/students/classes",
        json={"name": "6A", "level_id": level_id},
        headers=headers,
    )
    assert class_resp.status_code == 200, class_resp.text
    class_id = class_resp.json()["id"]

    delete_resp = await test_client.delete(f"/api/v1/students/classes/{class_id}", headers=headers)
    assert delete_resp.status_code == 200, delete_resp.text

    rows = await db_pool.fetch(
        "SELECT action, entity_type, outcome FROM audit_log WHERE entity_type = 'class' AND entity_id = $1",
        str(class_id),
    )
    assert len(rows) == 1
    assert rows[0]["action"] == "class.delete"
    assert rows[0]["outcome"] == "allow"
