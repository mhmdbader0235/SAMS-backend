import pytest
import asyncpg

@pytest.fixture(autouse=True)
async def clean_db(db_pool: asyncpg.Pool):
    """
    Truncate all tables after each test to guarantee integration test isolation.
    Runs automatically for every test in the tests/integration/ folder.
    """
    yield
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE TABLE 
                parents, super_admins, tenants, parent_child_links,
                users, levels, students, class, teachers, parenets,
                event, event_class_map, enrollment,
                payments, event_feedback, notifications, student_health_and_records,
                resource_types, resources, resource_cost, student_parent_map
            CASCADE
            """
        )
