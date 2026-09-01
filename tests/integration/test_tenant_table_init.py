"""Integration tests for tenant schema initialization (app/core/database.py)."""

import asyncpg

from app.core.database import _initialize_tenant_tables


class TestInitializeTenantTablesHasLegacyRemoved:
    async def test_stray_legacy_table_elsewhere_does_not_wipe_an_unrelated_tenant(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: initializing (or re-initializing, as happens on every
        backend restart via first-pool-access) one tenant's schema must never
        be influenced by a table named 'classes' or 'grade_levels' existing
        in some OTHER schema -- another tenant, or a stray leftover from an
        old install. The removed has_legacy probe queried
        information_schema.tables with no table_schema filter, so it saw
        every schema in the shared database, and on a false positive ran
        DROP TABLE ... CASCADE across ~24 of the *current* tenant's real
        tables (users, students, levels, class, event, enrollment, payments,
        ...), not just the legacy-named ones being probed for.
        """
        async with db_pool.acquire() as conn:
            # A completely unrelated schema has a stray table literally named
            # 'classes' -- simulating a leftover from an old install, or just
            # another tenant that happens to still carry the old name.
            await conn.execute('CREATE SCHEMA IF NOT EXISTS "stray_legacy_schema";')
            await conn.execute(
                'CREATE TABLE "stray_legacy_schema".classes (id serial primary key);'
            )
            # _initialize_tenant_tables() reads public.super_admins explicitly
            # (schema-qualified on purpose). Guarantee it exists there
            # regardless of which schema this fixture's own init.sql replay
            # happened to create it in (the fixture pins search_path to
            # "tenant_a, public" before replaying init.sql, so an
            # already-existing tenant_a schema from an earlier test in this
            # session can make an unqualified CREATE TABLE land in tenant_a
            # instead of public -- a fixture quirk, not app behavior).
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS public.super_admins (
                    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                    email         CITEXT      UNIQUE NOT NULL,
                    password_hash TEXT        NOT NULL,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

        try:
            # Provision a real tenant and put real data in it.
            await _initialize_tenant_tables(db_pool, tenant_id="tenant_regression")
            async with db_pool.acquire() as conn:
                await conn.execute('SET search_path TO "tenant_regression", public;')
                await conn.execute(
                    "INSERT INTO levels (name, isced_level, age_band_min, age_band_max, ordinal) "
                    "VALUES ('Year 10', 2, 14, 15, 1)"
                )
                level_count_before = await conn.fetchval("SELECT COUNT(*) FROM levels")
            assert level_count_before == 1

            # Simulate the function re-running on next pool access / backend
            # restart, exactly as it does in production, while the stray
            # 'classes' table in the unrelated schema is still sitting there.
            await _initialize_tenant_tables(db_pool, tenant_id="tenant_regression")

            async with db_pool.acquire() as conn:
                await conn.execute('SET search_path TO "tenant_regression", public;')
                level_count_after = await conn.fetchval("SELECT COUNT(*) FROM levels")
            assert level_count_after == 1, (
                "tenant_regression's real data was wiped by a stray 'classes' "
                "table in an unrelated schema"
            )
        finally:
            async with db_pool.acquire() as conn:
                await conn.execute('DROP SCHEMA IF EXISTS "stray_legacy_schema" CASCADE;')
                await conn.execute('DROP SCHEMA IF EXISTS "tenant_regression" CASCADE;')
