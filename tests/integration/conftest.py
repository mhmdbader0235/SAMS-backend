import contextlib

import asyncpg
import pytest


@pytest.fixture(autouse=True)
async def clean_db(db_pool: asyncpg.Pool):
    """
    Truncate all tables after each test to guarantee integration test isolation.
    Runs automatically for every test in the tests/integration/ folder.
    """
    yield
    # Truncate tenant and control plane tables safely
    tables = [
        "user_tenant_map",
        "parent_child_links",
        "parent_tenant_links",
        "invitations",
        "parents",
        "super_admins",
        "event_feedback",
        "payments",
        "enrollment",
        "event_class_map",
        "resource_cost",
        "resources",
        "resource_types",
        "notifications",
        "student_health_and_records",
        "student_parent_map",
        "student_class_history",
        "audit_log",
        "students",
        "class",
        "academic_years",
        "teachers",
        "parenets",
        "levels",
        "users",
        "academic_settings",
        "blackout_dates",
    ]
    # tenant_b too, not just tenant_a -- a small number of tests (the
    # multi-school family overview) write into BOTH schemas via a
    # two_tenant_client fixture that routes get_db_pool by tenant_id for
    # real, unlike this suite's default single-schema-blind mock. Without
    # this, tenant_b rows from one such test silently leak into the next.
    for schema in ("tenant_a", "tenant_b"):
        async with db_pool.acquire() as conn:
            try:
                await conn.execute(f'SET search_path TO "{schema}", public;')
                for t in tables:
                    with contextlib.suppress(Exception):
                        await conn.execute(f"TRUNCATE TABLE {t} RESTART IDENTITY CASCADE;")
            except Exception:
                pass
