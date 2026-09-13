"""Integration test for GET /api/v1/family/overview -- the multi-school
"all my children, all schools" read (Phase 5 of the multi-school access
plan). This is the one test in the suite that genuinely exercises TWO
tenant schemas at once through the real HTTP path.

The shared `test_client` fixture (tests/conftest.py) cannot express this:
its `_mock_get_pool` ignores the tenant_id argument entirely and always
returns the same tenant_a-pinned pool, which is fine for every other
single-tenant test but would make a cross-tenant scatter-gather test
meaningless (school B's query would silently run against school A's
schema). `two_tenant_client` below routes get_db_pool by tenant_id for
real, to two independently schema-pinned pools -- both already created and
migrated by the shared db_pool fixture via init.sql, which seeds both
tenant_a and tenant_b. Raises on any other tenant id, which also happens to
be the phantom-tenant-auto-register protection this test harness otherwise
lacks.
"""

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

import app.core.database as db_module
from app.main import app
from tests.conftest import TEST_DB


@pytest.fixture
async def two_tenant_client(db_pool: asyncpg.Pool, monkeypatch):
    async def setup_b(conn):
        await conn.execute('SET search_path TO "tenant_b", public;')

    pool_b = await asyncpg.create_pool(**TEST_DB, min_size=1, max_size=5, setup=setup_b)
    pools = {"tenant_a": db_pool, "tenant_b": pool_b}

    async def _routed_get_pool(tenant_id: str):
        if tenant_id not in pools:
            raise ValueError(f"two_tenant_client: unrouted tenant id {tenant_id!r}")
        return pools[tenant_id]

    async def _mock_get_control_plane_pool():
        # user_tenant_map / parents / tenants live in `public`, reachable
        # from either schema-pinned pool since search_path always keeps
        # public second -- tenant_a's pool is as good as tenant_b's here.
        return db_pool

    monkeypatch.setattr(db_module.db_manager, "get_pool", _routed_get_pool)
    monkeypatch.setattr(
        db_module.db_manager, "get_control_plane_pool", _mock_get_control_plane_pool
    )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client
    finally:
        await pool_b.close()


async def _seed_school(pool: asyncpg.Pool, *, school_name: str, currency: str):
    # init.sql seeds a demo school_profile row ("School A"/"School B") into
    # each schema, so this UPDATEs the singleton row ensure_profile_row()
    # would find rather than INSERTing a second one it would never see.
    async with pool.acquire() as conn:
        updated = await conn.fetchval(
            "UPDATE school_profile SET display_name = $1, currency = $2, timezone = 'UTC', "
            "activated_at = CURRENT_TIMESTAMP "
            "WHERE id = (SELECT id FROM school_profile ORDER BY id ASC LIMIT 1) "
            "RETURNING id",
            school_name,
            currency,
        )
        if updated is None:
            await conn.execute(
                "INSERT INTO school_profile (display_name, currency, timezone, activated_at) "
                "VALUES ($1, $2, 'UTC', CURRENT_TIMESTAMP)",
                school_name,
                currency,
            )


async def _seed_parent_with_child(
    pool: asyncpg.Pool, *, email: str, password_hash: str, child_name: str
) -> tuple[int, int]:
    """Creates a local parent `users` row + a linked student, entirely via
    direct SQL against the given tenant-pinned pool -- deliberately not
    going through the registration HTTP endpoint, since that endpoint's own
    control-plane linkage isn't what this test is verifying."""
    async with pool.acquire() as conn:
        parent_id = await conn.fetchval(
            "INSERT INTO users (email, role, password_hash) VALUES ($1, 'parent', $2) "
            "ON CONFLICT (email) DO UPDATE SET role = 'parent' RETURNING id",
            email,
            password_hash,
        )
        await conn.execute(
            "INSERT INTO parenets (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            parent_id,
            email.split("@")[0].title(),
        )
        student_id = await conn.fetchval(
            "INSERT INTO users (email, role, password_hash) VALUES ($1, 'student', $2) RETURNING id",
            f"child-{child_name}-{email}",
            password_hash,
        )
        await conn.execute(
            "INSERT INTO students (id, name) VALUES ($1, $2)", student_id, child_name
        )
        await conn.execute(
            "INSERT INTO student_parent_map (student_id, parent_id, can_approve, is_primary_contact) "
            "VALUES ($1, $2, true, true)",
            student_id,
            parent_id,
        )
    return parent_id, student_id


async def _seed_membership(db_pool: asyncpg.Pool, *, email: str, tenant_id: str, role: str):
    # user_tenant_map.identity_id is NOT NULL (cp_0004) -- reuse the real
    # repository method rather than hand-rolled SQL, since it's the one
    # place that knows how to resolve/create the identity row first.
    from app.domains.tenant.control_plane_repository import ControlPlaneRepository

    await ControlPlaneRepository(db_pool).upsert_user_tenant_map(email, tenant_id, role)


def _login_token(*, user_id, tenant_id: str, email: str, role: str = "parent"):
    """A real 403-vs-200 test needs a real, currently-valid CurrentUser --
    the simplest reliable way to get one in-process is to hand-craft the
    same local JWT AuthService.create_access_token produces, rather than
    driving the full login/redeem HTTP flow (already covered at unit level
    in test_auth_login_multi_school.py) just to obtain a token here.
    """
    from app.domains.auth.service import AuthService

    return AuthService.create_access_token(user_id, tenant_id, role, email=email, roles=[role])


async def test_family_overview_aggregates_across_two_real_schools(
    two_tenant_client, db_pool: asyncpg.Pool
):
    email = "multi.parent@example.com"
    password_hash = "not-checked-in-this-test"

    pool_a = db_pool  # tenant_a
    from app.core.database import db_manager

    pool_b = await db_manager.get_pool("tenant_b")

    await _seed_school(pool_a, school_name="Northgate School", currency="USD")
    await _seed_school(pool_b, school_name="Southgate School", currency="JOD")

    parent_a_id, _ = await _seed_parent_with_child(
        pool_a, email=email, password_hash=password_hash, child_name="Alex"
    )
    parent_b_id, _ = await _seed_parent_with_child(
        pool_b, email=email, password_hash=password_hash, child_name="Sam"
    )

    await _seed_membership(db_pool, email=email, tenant_id="tenant_a", role="parent")
    await _seed_membership(db_pool, email=email, tenant_id="tenant_b", role="parent")

    token = _login_token(user_id=parent_a_id, tenant_id="tenant_a", email=email)

    resp = await two_tenant_client.get(
        "/api/v1/family/overview", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["degraded"] == []
    schools = {s["tenant_id"]: s for s in data["schools"]}
    assert set(schools) == {"tenant_a", "tenant_b"}

    assert schools["tenant_a"]["status"] == "ok"
    assert schools["tenant_a"]["school_name"] == "Northgate School"
    assert schools["tenant_a"]["currency"] == "USD"
    assert [c["name"] for c in schools["tenant_a"]["children"]] == ["Alex"]

    assert schools["tenant_b"]["status"] == "ok"
    assert schools["tenant_b"]["school_name"] == "Southgate School"
    assert schools["tenant_b"]["currency"] == "JOD"
    assert [c["name"] for c in schools["tenant_b"]["children"]] == ["Sam"]

    # Both schools' children coexist in one response, kept distinct by
    # (tenant_id, id) -- bare ids are per-tenant serials and DO collide
    # (both schemas' first-ever student row is id=1 here, by design of
    # this test's own fresh-truncated fixtures) -- exactly the hazard
    # front/src/tenantKey.js exists to guard the frontend against.
    all_child_keys = [(s["tenant_id"], c["id"]) for s in data["schools"] for c in s["children"]]
    assert len(all_child_keys) == len(set(all_child_keys)) == 2

    del parent_b_id  # seeded for realism; not asserted on directly


async def test_family_overview_degrades_one_school_without_losing_the_other(
    two_tenant_client, db_pool: asyncpg.Pool, monkeypatch
):
    """A wedged/erroring school must show up as status:"error", not silently
    vanish from the response -- the correctness bug this design deliberately
    avoids (unlike AnalyticsService.get_platform_analytics' silent drop)."""
    email = "degraded.parent@example.com"
    password_hash = "x"

    pool_a = db_pool
    from app.core.database import db_manager

    pool_b = await db_manager.get_pool("tenant_b")

    await _seed_school(pool_a, school_name="Working School", currency="USD")
    parent_a_id, _ = await _seed_parent_with_child(
        pool_a, email=email, password_hash=password_hash, child_name="Jamie"
    )
    await _seed_membership(db_pool, email=email, tenant_id="tenant_a", role="parent")
    await _seed_membership(db_pool, email=email, tenant_id="tenant_b", role="parent")

    from app.domains.family import service as family_service_module

    real_fetch = family_service_module.FamilyService._fetch_one_school

    async def _flaky_fetch(tenant_id, role, email_):
        if tenant_id == "tenant_b":
            raise RuntimeError("simulated tenant_b outage")
        return await real_fetch(tenant_id, role, email_)

    monkeypatch.setattr(family_service_module.FamilyService, "_fetch_one_school", _flaky_fetch)

    token = _login_token(user_id=parent_a_id, tenant_id="tenant_a", email=email)
    resp = await two_tenant_client.get(
        "/api/v1/family/overview", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["degraded"] == ["tenant_b"]
    schools = {s["tenant_id"]: s for s in data["schools"]}
    assert schools["tenant_a"]["status"] == "ok"
    assert [c["name"] for c in schools["tenant_a"]["children"]] == ["Jamie"]
    assert schools["tenant_b"]["status"] == "error"
    assert schools["tenant_b"]["children"] == []

    del pool_b


async def test_family_overview_denies_a_non_parent(two_tenant_client, db_pool: asyncpg.Pool):
    email = "just.a.teacher@example.com"
    async with db_pool.acquire() as conn:
        teacher_id = await conn.fetchval(
            "INSERT INTO users (email, role, password_hash) VALUES ($1, 'teacher', 'x') RETURNING id",
            email,
        )
    await _seed_membership(db_pool, email=email, tenant_id="tenant_a", role="teacher")

    token = _login_token(user_id=teacher_id, tenant_id="tenant_a", email=email, role="teacher")
    resp = await two_tenant_client.get(
        "/api/v1/family/overview", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403
