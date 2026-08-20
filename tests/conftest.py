"""
Pytest fixtures shared across all backend tests.

Strategy:
- A single asyncpg pool is created once per test session pointing at a
  dedicated `doumind_test` database.
- Both control-plane and tenant schemas are initialized in this test DB.
- Between each test, all rows in all tables are TRUNCATED CASCADE.
- The `test_client` fixture patches db_manager's pools so HTTP-level
  tests hit the same test database state.
"""

import asyncio
import os
import sys

import asyncpg
import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

load_dotenv()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.main import app
import app.utils.email as email_module


# ─── Never let a test hit real SMTP ──────────────────────────────────────────
# register_school_admin() (tests/integration/_helpers.py) and several tests
# call POST /api/v1/auth/invitations with a real target_email — that route
# calls app.utils.email.send_invitation_email with zero test-side mocking,
# which sends a REAL message via smtplib.SMTP_SSL using whatever
# GMAIL_SMTP_USER/PASSWORD happen to be set in the environment. Autouse so
# every test gets this without opting in — nothing in this suite should ever
# depend on an email actually leaving the machine.
@pytest.fixture(autouse=True)
def _never_send_real_email(monkeypatch):
    monkeypatch.setattr(email_module, "_send_email_sync", lambda *a, **k: None)

# ─── Test database settings ──────────────────────────────────────────────────
TEST_DB = {
    "host": os.getenv("TEST_DB_HOST") or os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("TEST_DB_PORT") or os.getenv("DB_PORT", "5433")),
    "user": os.getenv("TEST_DB_USER") or os.getenv("DB_USER", "admin"),
    "password": os.getenv("TEST_DB_PASSWORD") or os.getenv("DB_PASSWORD", "secure_local_password"),
    "database": "doumind_test",
}


# ─── Database pool ───────────────────────────────────────────────────────────
@pytest.fixture()
async def db_pool():
    """Create the test database (if needed), initialize tables, yield pool, close on teardown."""
    sys_conn = await asyncpg.connect(
        host=TEST_DB["host"],
        port=TEST_DB["port"],
        user=TEST_DB["user"],
        password=TEST_DB["password"],
        database="postgres",
    )
    try:
        exists = await sys_conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB["database"]
        )
        if not exists:
            await sys_conn.execute(f"CREATE DATABASE {TEST_DB['database']}")
    finally:
        await sys_conn.close()

    async def setup_conn(conn):
        await conn.execute('SET search_path TO "tenant_a", public;')

    pool = await asyncpg.create_pool(**TEST_DB, min_size=1, max_size=5, setup=setup_conn)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DROP TABLE IF EXISTS
                comments, enrollments, notes, users, grade_levels, students, classes, attendance, events,
                event_grade_level_targets, event_class_targets, event_student_targets, notifications,
                student_health_and_records,                parents, super_admins, tenants, parent_child_links, parent_tenant_links, invitations, user_tenant_map, user_invitations,
                levels, class, teachers, parenets, cost_budget, event, event_class_map, enrollment,
                payments, event_feedback, resource_types, resources, resource_cost,
                school_profile, school_campus, school_contact
            CASCADE;
            """
        )
    # Recreate tables from init.sql
    init_sql_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "init.sql")
    with open(init_sql_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    async with pool.acquire() as conn:
        await conn.execute(schema_sql)
    yield pool
    await pool.close()


# ─── HTTP test client ─────────────────────────────────────────────────────────
@pytest.fixture
async def test_client(db_pool: asyncpg.Pool, monkeypatch):
    """
    Async HTTP client for the FastAPI app with the DB pools monkey-patched
    to use the test database instead of the real tenant databases.
    """
    import app.core.database as db_module

    async def _mock_get_pool(_tenant_id: str) -> asyncpg.Pool:
        return db_pool

    async def _mock_get_control_plane_pool() -> asyncpg.Pool:
        return db_pool

    monkeypatch.setattr(db_module.db_manager, "get_pool", _mock_get_pool)
    monkeypatch.setattr(db_module.db_manager, "get_control_plane_pool", _mock_get_control_plane_pool)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client
