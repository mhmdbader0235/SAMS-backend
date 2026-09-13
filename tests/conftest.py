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

import app.utils.email as email_module  # noqa: E402 -- must follow the event-loop-policy fix above
from app.main import app  # noqa: E402 -- must follow the event-loop-policy fix above


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
                school_profile, school_campus, school_contact,
                identities, identity_merge_decisions, access_requests,
                auth_selection_challenges, refresh_tokens
            CASCADE;
            """
        )
    # Recreate tables from init.sql
    init_sql_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "init.sql"
    )
    with open(init_sql_path, encoding="utf-8") as f:
        schema_sql = f.read()
    async with pool.acquire() as conn:
        await conn.execute(schema_sql)
    # init.sql predates alembic cp_0003+ (identities, user_tenant_map.identity_id,
    # access_requests, refresh_tokens, tenants.school_code) and never gained them --
    # any code path that writes identity_id (e.g. register/login) otherwise 500s here
    # with "column does not exist" while working fine against a real, migrated database.
    # This mirrors _initialize_control_plane_tables's schema DDL only -- deliberately
    # NOT calling that function itself, since its tenant/super-admin seeding blocks
    # collide with this suite's own super-admin bootstrap convention (get_super_admin_token
    # register/login-fallback in tests/integration/_helpers.py) on the same default
    # SUPER_ADMIN_ALLOWED_EMAIL, sa@desk.com, but with a different seeded password.
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS identity_merge_decisions (
                email       CITEXT      PRIMARY KEY,
                decision    TEXT        NOT NULL CHECK (decision IN ('same_person', 'distinct_people')),
                keeps_email TEXT,
                decided_by  TEXT        NOT NULL,
                note        TEXT,
                decided_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS identities (
                id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                email            CITEXT      UNIQUE NOT NULL,
                keycloak_user_id TEXT        UNIQUE,
                display_name     TEXT,
                phone            TEXT,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.execute(
            """
            INSERT INTO identities (email)
            SELECT email FROM user_tenant_map
            UNION SELECT email FROM parents
            UNION SELECT email FROM super_admins
            ON CONFLICT (email) DO NOTHING;
            """
        )
        await conn.execute(
            """
            ALTER TABLE user_tenant_map
                ADD COLUMN IF NOT EXISTS identity_id     UUID REFERENCES identities(id) ON DELETE CASCADE,
                ADD COLUMN IF NOT EXISTS status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('invited', 'pending_approval', 'active', 'suspended', 'revoked')),
                ADD COLUMN IF NOT EXISTS roles           TEXT[] NOT NULL DEFAULT '{}',
                ADD COLUMN IF NOT EXISTS invited_by      TEXT,
                ADD COLUMN IF NOT EXISTS decided_by      TEXT,
                ADD COLUMN IF NOT EXISTS decided_at      TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS decline_reason  TEXT,
                ADD COLUMN IF NOT EXISTS created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ADD COLUMN IF NOT EXISTS last_active_at  TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS is_default      BOOLEAN NOT NULL DEFAULT FALSE;
            """
        )
        await conn.execute(
            """
            UPDATE user_tenant_map m
            SET    identity_id = i.id,
                   roles       = CASE WHEN m.roles = '{}' THEN ARRAY[m.role] ELSE m.roles END
            FROM   identities i
            WHERE  i.email = m.email
            AND    m.identity_id IS NULL;
            """
        )
        await conn.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM user_tenant_map WHERE identity_id IS NULL) THEN
                    IF EXISTS (
                        SELECT 1
                        FROM   pg_constraint c
                        JOIN   pg_class t ON t.oid = c.conrelid
                        JOIN   pg_namespace n ON n.oid = t.relnamespace
                        WHERE  c.conname = 'user_tenant_map_pkey'
                        AND    t.relname = 'user_tenant_map'
                        AND    n.nspname = current_schema()
                        AND    c.contype = 'p'
                        AND    (
                            SELECT array_agg(a.attname::text ORDER BY a.attname::text)
                            FROM   unnest(c.conkey) AS k(attnum)
                            JOIN   pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                        ) != ARRAY['identity_id', 'tenant_id']
                    ) THEN
                        ALTER TABLE user_tenant_map ALTER COLUMN identity_id SET NOT NULL;
                        ALTER TABLE user_tenant_map DROP CONSTRAINT user_tenant_map_pkey;
                        ALTER TABLE user_tenant_map ADD CONSTRAINT user_tenant_map_pkey
                            PRIMARY KEY (identity_id, tenant_id);
                    END IF;
                END IF;
            END $$;
            """
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_utm_email_tenant ON user_tenant_map (email, tenant_id);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_utm_tenant_status ON user_tenant_map (tenant_id, status);"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_utm_one_default ON user_tenant_map (identity_id) "
            "WHERE is_default;"
        )
        await conn.execute(
            """
            INSERT INTO user_tenant_map (identity_id, email, tenant_id, role, roles, status)
            SELECT i.id, p.email, l.tenant_id, 'parent', ARRAY['parent'], 'active'
            FROM   parent_tenant_links l
            JOIN   parents p    ON p.id = l.parent_id
            JOIN   identities i ON i.email = p.email
            ON CONFLICT (identity_id, tenant_id) DO NOTHING;
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS access_requests (
                id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                identity_id     UUID        NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
                tenant_id       VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
                requested_role  TEXT        NOT NULL,
                note            TEXT,
                status          TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected', 'withdrawn')),
                reject_reason   TEXT,
                decided_by      TEXT,
                decided_at      TIMESTAMPTZ,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_ar_one_open ON access_requests (identity_id, tenant_id) "
            "WHERE status = 'pending';"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ar_queue ON access_requests (tenant_id, status, created_at DESC);"
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_selection_challenges (
                id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                token_hash      BYTEA       NOT NULL UNIQUE,
                identity_id     UUID        NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
                matched_tenants TEXT[]      NOT NULL,
                consumed_at     TIMESTAMPTZ,
                expires_at      TIMESTAMPTZ NOT NULL,
                created_ip      INET,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_asc_identity ON auth_selection_challenges (identity_id);"
        )
        await conn.execute(
            """
            ALTER TABLE tenants
                ADD COLUMN IF NOT EXISTS school_code   CITEXT UNIQUE,
                ADD COLUMN IF NOT EXISTS display_name  TEXT,
                ADD COLUMN IF NOT EXISTS brand_color   TEXT;
            """
        )
        await conn.execute("UPDATE tenants SET display_name = name WHERE display_name IS NULL;")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                token_hash   BYTEA       NOT NULL UNIQUE,
                identity_id  UUID        NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
                tenant_id    VARCHAR(50) NOT NULL DEFAULT '',
                role         TEXT        NOT NULL,
                family_id    UUID        NOT NULL,
                revoked_at   TIMESTAMPTZ,
                replaced_by  UUID        REFERENCES refresh_tokens(id),
                expires_at   TIMESTAMPTZ NOT NULL,
                created_ip   INET,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_identity ON refresh_tokens (identity_id);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_family ON refresh_tokens (family_id) "
            "WHERE revoked_at IS NULL;"
        )
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
    monkeypatch.setattr(
        db_module.db_manager, "get_control_plane_pool", _mock_get_control_plane_pool
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client
