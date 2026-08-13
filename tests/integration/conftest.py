import asyncpg
import pytest


@pytest.fixture(autouse=True)
async def clean_db(db_pool: asyncpg.Pool):
    """
    Truncate all tables after each test to guarantee integration test isolation.
    Runs automatically for every test in the tests/integration/ folder.
    """
    yield
    async with db_pool.acquire() as conn:
        try:
            await conn.execute('SET search_path TO "tenant_a", public;')
            # Truncate tenant and control plane tables safely
            tables = [
                "user_tenant_map", "parent_child_links", "parent_tenant_links",
                "invitations", "parents", "super_admins", "event_feedback",
                "payments", "enrollment", "event_class_map", "resource_cost",
                "resources", "resource_types", "notifications", "student_health_and_records",
                "student_parent_map", "students", "class", "teachers", "parenets",
                "levels", "users"
            ]
            for t in tables:
                try:
                    await conn.execute(f"TRUNCATE TABLE {t} RESTART IDENTITY CASCADE;")
                except Exception:
                    pass
        except Exception:
            pass
