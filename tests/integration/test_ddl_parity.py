"""DDL parity between the runtime table-creation path and the Alembic path.

invariant: `_initialize_tenant_tables()` (app/core/database.py, used for
newly-provisioned tenants and legacy tenants) and the Alembic `tenant` branch
(back/alembic/versions/, used by apply_all_tenants.py) must create IDENTICAL
schemas. They have already diverged once silently (student_class_history was
missing from the Alembic baseline) -- this test makes that class of bug
unmergeable rather than merely documented.

Builds two throwaway schemas in the doumind_test database: `parity_runtime`
via the runtime path, `parity_alembic` via a real `alembic upgrade` run
(subprocess, with CONTROL_PLANE_DB_NAME pointed at doumind_test so it never
touches the real dev database), then diffs information_schema between them.
"""

import os
import subprocess
import sys

import asyncpg
import pytest

from app.core.database import _initialize_tenant_tables

BACK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_DB = {
    "host": os.getenv("TEST_DB_HOST") or os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("TEST_DB_PORT") or os.getenv("DB_PORT", "5433")),
    "user": os.getenv("TEST_DB_USER") or os.getenv("DB_USER", "admin"),
    "password": os.getenv("TEST_DB_PASSWORD") or os.getenv("DB_PASSWORD", "secure_local_password"),
    "database": "doumind_test",
}

RUNTIME_SCHEMA = "parity_runtime"
ALEMBIC_SCHEMA = "parity_alembic"


def _run_alembic_upgrade(schema: str) -> None:
    """Runs `alembic upgrade tenant@head` against `schema`, in doumind_test,
    via a subprocess so CONTROL_PLANE_DB_NAME can be overridden for this one
    process without affecting the already-imported app.core.config module in
    the test process itself."""
    script = (
        "from alembic.config import Config; from alembic import command; "
        "cfg = Config('alembic.ini'); "
        "cfg.attributes['target'] = 'tenant'; "
        f"cfg.attributes['schema'] = '{schema}'; "
        "command.upgrade(cfg, 'tenant@head')"
    )
    env = dict(os.environ)
    env["CONTROL_PLANE_DB_NAME"] = TEST_DB["database"]
    env["DB_HOST"] = TEST_DB["host"]
    env["DB_PORT"] = str(TEST_DB["port"])
    env["DB_USER"] = TEST_DB["user"]
    env["DB_PASSWORD"] = TEST_DB["password"]
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=BACK_DIR, env=env, capture_output=True, text=True
    )
    assert (
        result.returncode == 0
    ), f"alembic upgrade failed for schema={schema}\nstdout={result.stdout}\nstderr={result.stderr}"


@pytest.mark.asyncio
async def test_ddl_parity_between_runtime_and_alembic():
    conn = await asyncpg.connect(**TEST_DB)
    try:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{RUNTIME_SCHEMA}" CASCADE;')
        await conn.execute(f'DROP SCHEMA IF EXISTS "{ALEMBIC_SCHEMA}" CASCADE;')
    finally:
        await conn.close()

    pool = await asyncpg.create_pool(**TEST_DB, min_size=1, max_size=2)
    try:
        await _initialize_tenant_tables(pool, RUNTIME_SCHEMA)
    finally:
        await pool.close()

    _run_alembic_upgrade(ALEMBIC_SCHEMA)

    conn = await asyncpg.connect(**TEST_DB)
    try:
        cols_runtime = await conn.fetch(
            """
            SELECT table_name, column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = $1
            ORDER BY table_name, column_name
            """,
            RUNTIME_SCHEMA,
        )
        cols_alembic = await conn.fetch(
            """
            SELECT table_name, column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = $1
            ORDER BY table_name, column_name
            """,
            ALEMBIC_SCHEMA,
        )
    finally:
        await conn.close()

    # alembic_version is Alembic's own migration-bookkeeping table, deliberately
    # absent from the runtime path (which doesn't use Alembic) -- not a real gap.
    def _key(row):
        return (row["table_name"], row["column_name"])

    runtime_map = {_key(r): dict(r) for r in cols_runtime}
    alembic_map = {_key(r): dict(r) for r in cols_alembic if r["table_name"] != "alembic_version"}

    only_in_runtime = sorted(set(runtime_map) - set(alembic_map))
    only_in_alembic = sorted(set(alembic_map) - set(runtime_map))
    type_mismatches = [
        (k, runtime_map[k], alembic_map[k])
        for k in sorted(set(runtime_map) & set(alembic_map))
        if runtime_map[k]["data_type"] != alembic_map[k]["data_type"]
        or runtime_map[k]["is_nullable"] != alembic_map[k]["is_nullable"]
    ]

    assert not only_in_runtime, f"columns in runtime path but not Alembic: {only_in_runtime}"
    assert not only_in_alembic, f"columns in Alembic path but not runtime: {only_in_alembic}"
    assert not type_mismatches, f"type/nullability mismatches: {type_mismatches}"
