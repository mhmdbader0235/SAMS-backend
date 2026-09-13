"""
Database layer — multi-tenant connection pool manager.

Each tenant (school) gets its own isolated PostgreSQL database.
On first request for a tenant, the database is auto-created and tables are
initialized via _initialize_tenant_tables(). Subsequent requests reuse the pool.

The Control-Plane DB manages global metadata including parents, super-admins,
tenant configurations, and parent-student links.
"""

import asyncio
import contextlib
import os

import asyncpg

from app.core.config import (
    CONTROL_PLANE_DB_NAME,
    CP_POOL_ACQUIRE_TIMEOUT,
    CP_POOL_MAX,
    CP_POOL_MIN,
    DB_HOST,
    DB_PASSWORD,
    DB_PORT,
    DB_USER,
    TENANT_POOL_ACQUIRE_TIMEOUT,
    TENANT_POOL_MAX,
    TENANT_POOL_MIN,
)


# =============================================================================
# Control-Plane Table Initialization
# =============================================================================
async def _initialize_control_plane_tables(pool: asyncpg.Pool) -> None:
    """Create all control-plane tables if they do not exist, and seed tenants."""
    async with pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS citext;")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        await conn.execute("CREATE SCHEMA IF NOT EXISTS keycloak;")

        # Tenants table
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                tenant_id   VARCHAR(50) PRIMARY KEY,
                name        TEXT        NOT NULL,
                -- db_host/db_port/db_name: DEAD. get_pool() in database.py always
                -- force-overwrites these back to the control-plane host/port/database
                -- ("Force database name to use control plane database"), so the
                -- stored value is never used to open a connection. Queued for
                -- removal in the Step 6 Alembic migration (see FIX_PLAN.md Step 6) —
                -- don't wire new code to them.
                db_host     TEXT        NOT NULL,
                db_port     INTEGER     NOT NULL,
                -- db_user/db_password: reserved for a future database-per-tenant
                -- model. Unlike db_host/db_port above, get_pool() DOES read these on
                -- every call — every tenant just happens to be seeded with the same
                -- admin credentials today, so it's currently a no-op in practice.
                -- Live code, not dead code — don't remove without also updating
                -- get_pool().
                db_user     TEXT        NOT NULL,
                db_password TEXT        NOT NULL,
                db_name     TEXT        NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # Parents table
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS parents (
                id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                email         CITEXT      UNIQUE NOT NULL,
                password_hash TEXT        NOT NULL,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                phone         VARCHAR(50) DEFAULT NULL,
                address       TEXT DEFAULT NULL
            );
            """
        )
        await conn.execute(
            "ALTER TABLE parents ADD COLUMN IF NOT EXISTS phone VARCHAR(50) DEFAULT NULL;"
        )
        await conn.execute(
            "ALTER TABLE parents ADD COLUMN IF NOT EXISTS address TEXT DEFAULT NULL;"
        )

        # Super-admins table
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS super_admins (
                id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                email         CITEXT      UNIQUE NOT NULL,
                password_hash TEXT        NOT NULL,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # Parent-child cross-db link table
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS parent_child_links (
                id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                parent_id  UUID        NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
                tenant_id  VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
                student_id UUID        NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (parent_id, tenant_id, student_id)
            );
            """
        )

        # Parent-tenant link table
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS parent_tenant_links (
                parent_id  UUID        NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
                tenant_id  VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (parent_id, tenant_id)
            );
            """
        )

        # Invitations table
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invitations (
                id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                code          VARCHAR(100) UNIQUE NOT NULL,
                tenant_id     VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
                role          TEXT        NOT NULL CHECK (role IN ('school_admin', 'teacher', 'parent', 'student', 'manager', 'super_admin')),
                target_email  CITEXT      DEFAULT NULL,
                max_uses      INTEGER     NOT NULL DEFAULT 1,
                uses_count    INTEGER     NOT NULL DEFAULT 0,
                expires_at    TIMESTAMPTZ NOT NULL,
                created_by    UUID        DEFAULT NULL,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                is_active     BOOLEAN     NOT NULL DEFAULT TRUE
            );
            """
        )

        # User-to-tenant mapping table — used to resolve which tenant a Keycloak user belongs to
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_tenant_map (
                email      CITEXT      NOT NULL,
                tenant_id  VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
                role       TEXT        NOT NULL DEFAULT 'student',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (email, tenant_id)
            );
            """
        )

        # Widen an already-existing user_tenant_map from PRIMARY KEY (email) to
        # (email, tenant_id). This has to live here as well as in Alembic
        # (cp_0002): the CREATE above is IF NOT EXISTS, so an existing database
        # keeps whatever key it already had, and upsert_user_tenant_map's
        # ON CONFLICT (email, tenant_id) raises
        # "no unique or exclusion constraint matching the ON CONFLICT
        # specification" against the old single-column key. Every membership
        # write would 500 until the constraint matches, so the two must move
        # together. Safe to re-run, and safe on the old key because
        # PRIMARY KEY (email) guaranteed at most one row per email.
        await pool.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM   pg_constraint c
                    JOIN   pg_class t ON t.oid = c.conrelid
                    JOIN   pg_namespace n ON n.oid = t.relnamespace
                    WHERE  c.conname = 'user_tenant_map_pkey'
                    AND    t.relname = 'user_tenant_map'
                    AND    n.nspname = current_schema()
                    AND    c.contype = 'p'
                    AND    array_length(c.conkey, 1) = 1
                ) THEN
                    ALTER TABLE user_tenant_map DROP CONSTRAINT user_tenant_map_pkey;
                    ALTER TABLE user_tenant_map ADD CONSTRAINT user_tenant_map_pkey
                        PRIMARY KEY (email, tenant_id);
                END IF;
            END $$;
            """
        )

        # Audit log for pre-provisioned user invitations
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_invitations (
                id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                email         CITEXT      NOT NULL,
                tenant_id     VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
                role          TEXT        NOT NULL,
                inviter_id    TEXT        DEFAULT NULL,
                status        TEXT        NOT NULL DEFAULT 'pending',
                created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # ─── Multi-School Membership (mirrors alembic cp_0003-cp_0008) ──────
        # This inline path is what a fresh dev/test database actually gets
        # provisioned from (tests/conftest.py's db_pool fixture calls this,
        # not Alembic) -- so it has to carry the same schema Alembic does,
        # same as user_tenant_map's PK-widening DO $$ block above already
        # does for cp_0002. The migrations remain the source of truth for a
        # real deployment; this keeps a fresh local/test database from
        # silently missing tables the migrations already describe.
        #
        # The migration-time human-review gate (identity_merge_decisions +
        # the collision abort in cp_0003) is deliberately NOT reproduced
        # here: that gate protects against merging two different people's
        # data on a live database with real history, which a freshly
        # provisioned dev/test database has none of. The ledger table itself
        # is still created, so `scripts/check_identity_clashes.py` and the
        # repository methods that reference it work in every environment.
        await conn.execute("CREATE EXTENSION IF NOT EXISTS citext;")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
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

        # Seed default tenants if table is empty
        row_count = await conn.fetchval("SELECT COUNT(*) FROM tenants")
        if row_count == 0:
            for tid, tcfg in TENANT_DB_CONFIG.items():
                await conn.execute(
                    """
                    INSERT INTO tenants (tenant_id, name, db_host, db_port, db_user, db_password, db_name)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    tid,
                    tid.replace("_", " ").title(),
                    tcfg["host"],
                    tcfg["port"],
                    tcfg["user"],
                    tcfg["password"],
                    tcfg["database"],
                )

        # Seed default super admin (sa@desk.com / password123)
        sa_exists = await conn.fetchval(
            "SELECT id FROM super_admins WHERE email = $1", "sa@desk.com"
        )
        if not sa_exists:
            from app.core.keycloak_admin import sync_user_to_keycloak
            from app.domains.auth.service import AuthService

            pass_hash = AuthService.hash_password("password123")
            await conn.execute(
                "INSERT INTO super_admins (email, password_hash) VALUES ($1, $2)",
                "sa@desk.com",
                pass_hash,
            )
            with contextlib.suppress(Exception):
                sync_user_to_keycloak("sa@desk.com", "password123", "super_admin", "tenant_a")


# =============================================================================
# Tenant Table Initialization
# =============================================================================
# tenant_id is required, with no default. It used to default to "tenant_a" -- the
# first real school -- and this function issues CREATE SCHEMA, DDL and destructive
# ALTERs against whatever schema it is handed, so an accidental call with the
# argument omitted would have operated on a live tenant. Every existing caller
# already passes it explicitly.
async def _initialize_tenant_tables(pool: asyncpg.Pool, tenant_id: str) -> None:
    """Create all tables in a newly provisioned tenant schema."""
    async with pool.acquire() as conn:
        # Ensure schema exists and isolate search_path to tenant schema during DDL execution
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{tenant_id}";')
        await conn.execute(f'SET search_path TO "{tenant_id}", public;')

        # Extensions
        await conn.execute("CREATE EXTENSION IF NOT EXISTS citext;")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

        # 1. users
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            BIGSERIAL   PRIMARY KEY,
                email         CITEXT      UNIQUE NOT NULL,
                role          TEXT        NOT NULL CHECK (role IN ('school_admin', 'teacher', 'parent', 'student', 'manager', 'finance', 'event_teacher', 'pending', 'super_admin')),
                roles         TEXT[]      DEFAULT '{}',
                permissions   TEXT[]      DEFAULT '{}',
                password_hash TEXT        NOT NULL,
                phone         VARCHAR(50) DEFAULT NULL,
                address       TEXT        DEFAULT NULL,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;")
        await conn.execute(
            "ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('school_admin', 'teacher', 'parent', 'student', 'manager', 'finance', 'event_teacher', 'pending', 'super_admin'));"
        )
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS roles TEXT[] DEFAULT '{}';")
        await conn.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS permissions TEXT[] DEFAULT '{}';"
        )
        await conn.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_language TEXT DEFAULT NULL;"
        )
        await conn.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_timezone TEXT DEFAULT NULL;"
        )

        # 2. levels
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS levels (
                level_id       BIGSERIAL   PRIMARY KEY,
                name           TEXT        NOT NULL,
                isced_level    INTEGER     DEFAULT NULL,
                age_band_min   INTEGER     DEFAULT NULL,
                age_band_max   INTEGER     DEFAULT NULL,
                ordinal        INTEGER     DEFAULT NULL,
                is_active      BOOLEAN     NOT NULL DEFAULT TRUE
            );
            ALTER TABLE levels ADD COLUMN IF NOT EXISTS isced_level INTEGER DEFAULT NULL;
            ALTER TABLE levels ADD COLUMN IF NOT EXISTS age_band_min INTEGER DEFAULT NULL;
            ALTER TABLE levels ADD COLUMN IF NOT EXISTS age_band_max INTEGER DEFAULT NULL;
            ALTER TABLE levels ADD COLUMN IF NOT EXISTS ordinal INTEGER DEFAULT NULL;
            ALTER TABLE levels ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
            """
        )

        # Academic settings & blackout dates
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS academic_settings (
                id               BIGSERIAL   PRIMARY KEY,
                academic_year    TEXT        NOT NULL,
                start_month      INTEGER     NOT NULL,
                weekend_days     TEXT[]      NOT NULL DEFAULT '{}',
                system           TEXT        NOT NULL DEFAULT 'US',
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            ALTER TABLE academic_settings ADD COLUMN IF NOT EXISTS system TEXT DEFAULT 'US';

            CREATE TABLE IF NOT EXISTS blackout_dates (
                id            BIGSERIAL   PRIMARY KEY,
                date          DATE        NOT NULL,
                title         TEXT        NOT NULL,
                tags          TEXT[]      NOT NULL DEFAULT '{}',
                created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # 3. teachers
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS teachers (
                id   BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                name TEXT   NOT NULL
            );
            """
        )

        # 4. parenets
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS parenets (
                id    BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                name  TEXT   NOT NULL,
                phone TEXT   DEFAULT NULL
            );
            """
        )
        await conn.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(50) DEFAULT NULL;"
        )
        await conn.execute("ALTER TABLE parenets ADD COLUMN IF NOT EXISTS phone TEXT DEFAULT NULL;")

        # 5. class
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS class (
                id              BIGSERIAL   PRIMARY KEY,
                name            TEXT        NOT NULL,
                level_id        BIGINT      NOT NULL REFERENCES levels(level_id) ON DELETE RESTRICT,
                head_teacher_id BIGINT      REFERENCES teachers(id) ON DELETE RESTRICT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            ALTER TABLE class ALTER COLUMN head_teacher_id DROP NOT NULL;
            """
        )

        # 6. students
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS students (
                id         BIGINT      PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                name       TEXT        NOT NULL,
                class_id   BIGINT      NOT NULL REFERENCES class(id) ON DELETE RESTRICT,
                gender     TEXT        DEFAULT NULL,
                birth_data TEXT        DEFAULT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # 7. student_parent_map
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS student_parent_map (
                student_id BIGINT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                parent_id  BIGINT NOT NULL REFERENCES parenets(id) ON DELETE CASCADE,
                PRIMARY KEY (student_id, parent_id)
            );
            ALTER TABLE student_parent_map ADD COLUMN IF NOT EXISTS relationship_type TEXT DEFAULT NULL;
            ALTER TABLE student_parent_map ADD COLUMN IF NOT EXISTS is_primary_contact BOOLEAN NOT NULL DEFAULT FALSE;
            ALTER TABLE student_parent_map ADD COLUMN IF NOT EXISTS can_approve BOOLEAN NOT NULL DEFAULT TRUE;
            ALTER TABLE student_parent_map ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;
            """
        )

        # 9. event
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event (
                id             BIGSERIAL      PRIMARY KEY,
                title          TEXT           NOT NULL,
                description    TEXT           NOT NULL DEFAULT '',
                address        TEXT           DEFAULT NULL,
                event_map_id   BIGINT         DEFAULT NULL,
                school_subsidy NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
                date           TIMESTAMPTZ    NOT NULL,
                created_by     BIGINT         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at     TIMESTAMPTZ    NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # 10. event_class_map
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_class_map (
                id            BIGSERIAL      PRIMARY KEY,
                event_id      BIGINT         NOT NULL REFERENCES event(id) ON DELETE CASCADE,
                class_id      BIGINT         NOT NULL REFERENCES class(id) ON DELETE CASCADE,
                ticket_price  NUMERIC(10, 2) NOT NULL DEFAULT 0.00
            );
            """
        )

        # 11. enrollment
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS enrollment (
                id                 BIGSERIAL   PRIMARY KEY,
                student_id         BIGINT      NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                event_class_map_id BIGINT      NOT NULL REFERENCES event_class_map(id) ON DELETE CASCADE,
                state              TEXT        NOT NULL CHECK (state IN ('requested_by_student', 'approved_by_parent', 'approved_by_teacher', 'rejected_by_parent', 'rejected_by_teacher')),
                teacher_id         BIGINT      DEFAULT NULL REFERENCES teachers(id) ON DELETE SET NULL,
                parent_id          BIGINT      DEFAULT NULL REFERENCES parenets(id) ON DELETE SET NULL,
                created_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (student_id, event_class_map_id)
            );
            """
        )

        # 12. payments
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id            BIGSERIAL      PRIMARY KEY,
                enrollment_id BIGINT         NOT NULL REFERENCES enrollment(id) ON DELETE CASCADE,
                amount        NUMERIC(10, 2) NOT NULL,
                status        TEXT           NOT NULL CHECK (status IN ('pending', 'paid', 'refunded')),
                created_at    TIMESTAMPTZ    NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # 13. event_feedback
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_feedback (
                id         BIGSERIAL   PRIMARY KEY,
                event_id   BIGINT      NOT NULL REFERENCES event(id) ON DELETE CASCADE,
                user_id    BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                rating     INT         NOT NULL CHECK (rating BETWEEN 1 AND 5),
                comments   TEXT        DEFAULT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # 14. student_health_and_records (PII table)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS student_health_and_records (
                id                            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                student_id                    BIGINT      UNIQUE NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                national_id_encrypted         TEXT        NOT NULL,
                medical_conditions_encrypted  TEXT        NOT NULL,
                emergency_contact_encrypted   TEXT        NOT NULL
            );
            """
        )

        # 15. notifications
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                event_id          BIGINT      NOT NULL REFERENCES event(id) ON DELETE CASCADE,
                recipient_user_id BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                delivered_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                read_at           TIMESTAMPTZ DEFAULT NULL,
                title_override    VARCHAR(255) DEFAULT NULL
            );
            """
        )

        # 16. resource_types (workflow & resource schema)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resource_types (
                id              SERIAL PRIMARY KEY,
                name            VARCHAR(120) NOT NULL,
                category        VARCHAR(30)  NOT NULL DEFAULT 'other',
                is_custom       BOOLEAN      NOT NULL DEFAULT false,
                created_by_user_id BIGINT    NULL REFERENCES users(id) ON DELETE SET NULL,
                is_active       BOOLEAN      NOT NULL DEFAULT true,
                created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
            );
            """
        )

        # 17. resources
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resources (
                id                SERIAL PRIMARY KEY,
                event_id          BIGINT NOT NULL REFERENCES event(id) ON DELETE CASCADE,
                resource_type_id  INTEGER NOT NULL REFERENCES resource_types(id),
                description       TEXT NULL,
                quantity          INTEGER NOT NULL CHECK (quantity > 0),
                added_by_user_id  BIGINT NOT NULL REFERENCES users(id),
                updated_by_user_id BIGINT NULL REFERENCES users(id),
                created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS ix_resources_event ON resources(event_id);
            """
        )

        # 18. resource_cost
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resource_cost (
                id              SERIAL PRIMARY KEY,
                event_id        BIGINT NOT NULL REFERENCES event(id) ON DELETE CASCADE,
                resource_id     INTEGER NOT NULL UNIQUE REFERENCES resources(id) ON DELETE CASCADE,
                unit_price      NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
                total_cost      NUMERIC(12,2) NOT NULL,
                currency        VARCHAR(3) NOT NULL DEFAULT 'JOD',
                set_by_user_id  BIGINT NOT NULL REFERENCES users(id),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )

        # 19. event alterations (workflow status and review timestamps)
        await conn.execute(
            """
            DO $$ BEGIN
                CREATE TYPE event_status AS ENUM ('draft', 'resource_planning', 'proposed', 'approved', 'finance_approval', 'final_review', 'published');
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
            """
        )
        await conn.execute(
            "ALTER TYPE event_status ADD VALUE IF NOT EXISTS 'approved' AFTER 'proposed';"
        )

        await conn.execute(
            """
            ALTER TABLE event ADD COLUMN IF NOT EXISTS status event_status NOT NULL DEFAULT 'draft';
            ALTER TABLE event ADD COLUMN IF NOT EXISTS predicted_attendance INTEGER NULL;
            ALTER TABLE event ADD COLUMN IF NOT EXISTS manager_reviewer_id BIGINT NULL REFERENCES users(id);
            ALTER TABLE event ADD COLUMN IF NOT EXISTS finance_reviewer_id BIGINT NULL REFERENCES users(id);
            ALTER TABLE event ADD COLUMN IF NOT EXISTS total_cost NUMERIC(12,2) NULL;
            ALTER TABLE event ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ NULL;
            ALTER TABLE event ADD COLUMN IF NOT EXISTS manager_approved_at TIMESTAMPTZ NULL;
            ALTER TABLE event ADD COLUMN IF NOT EXISTS finance_priced_at TIMESTAMPTZ NULL;
            ALTER TABLE event ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ NULL;
            ALTER TABLE event ADD COLUMN IF NOT EXISTS rejection_reason TEXT NULL;
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS ix_events_status ON event(status);")

        # 21. Drop deprecated cost_budget table and references
        await conn.execute(
            """
            ALTER TABLE event_class_map DROP COLUMN IF EXISTS costbudget_id CASCADE;
            DROP TABLE IF EXISTS cost_budget CASCADE;
            ALTER TABLE students ALTER COLUMN class_id DROP NOT NULL;
            ALTER TABLE class ADD COLUMN IF NOT EXISTS capacity INTEGER DEFAULT 25;
            ALTER TABLE class ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
            UPDATE class SET capacity = 25 WHERE capacity IS NULL OR capacity <= 0;
            ALTER TABLE class ALTER COLUMN capacity SET NOT NULL;
            ALTER TABLE class DROP CONSTRAINT IF EXISTS class_capacity_positive;
            ALTER TABLE class ADD CONSTRAINT class_capacity_positive CHECK (capacity > 0);
            """
        )

        # 21b. student_class_history -- append-only log of every class_id
        # transition, so placement moves have a "who/when/from/to" record
        # instead of only the current snapshot on students.class_id.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS student_class_history (
                id           BIGSERIAL   PRIMARY KEY,
                student_id   BIGINT      NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                old_class_id BIGINT      REFERENCES class(id) ON DELETE SET NULL,
                new_class_id BIGINT      REFERENCES class(id) ON DELETE SET NULL,
                changed_by   BIGINT      REFERENCES users(id) ON DELETE SET NULL,
                changed_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_sch_student ON student_class_history(student_id, changed_at);
            """
        )

        # 21c/21d. academic_years / class.academic_year_id (ADR 0002) plus year
        # rollover (ADR 0003), as ONE coherent block -- an earlier version of
        # this function (this session's ADR 0002 pass) shipped 21c with a
        # boolean `is_active` column; a still-earlier tenant may have run that
        # version already. `status` is the only lifecycle representation from
        # here on, so the legacy shape is converted (and disarms itself, since
        # the conversion drops the column its own IF guard checks for) before
        # anything below assumes `status` exists. Guarded throughout with
        # `IF NOT EXISTS` / `WHERE ... IS NULL` because, unlike an Alembic
        # revision, this function re-runs on every tenant pool access after
        # every backend restart.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS academic_years (
                id         BIGSERIAL   PRIMARY KEY,
                name       TEXT        NOT NULL,
                status     TEXT,
                start_date DATE        DEFAULT NULL,
                end_date   DATE        DEFAULT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'academic_years'
                      AND column_name = 'is_active'
                ) THEN
                    UPDATE academic_years SET status = CASE WHEN is_active THEN 'active' ELSE 'closed' END;
                    DROP INDEX IF EXISTS one_active_academic_year;
                    ALTER TABLE academic_years DROP COLUMN is_active;
                END IF;
            END $$;

            INSERT INTO academic_years (name, status)
            SELECT
                COALESCE(
                    (SELECT academic_year FROM academic_settings ORDER BY id DESC LIMIT 1),
                    '2026-2027'
                ),
                'active'
            WHERE NOT EXISTS (SELECT 1 FROM academic_years);

            ALTER TABLE academic_years ALTER COLUMN status SET NOT NULL;
            ALTER TABLE academic_years DROP CONSTRAINT IF EXISTS academic_years_status_check;
            ALTER TABLE academic_years ADD CONSTRAINT academic_years_status_check
                CHECK (status IN ('planned', 'active', 'closed'));
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_academic_year
                ON academic_years (status) WHERE status = 'active';
            ALTER TABLE academic_years ADD COLUMN IF NOT EXISTS rolled_from_id BIGINT
                REFERENCES academic_years(id) ON DELETE SET NULL;

            ALTER TABLE class
                ADD COLUMN IF NOT EXISTS academic_year_id BIGINT
                    REFERENCES academic_years(id) ON DELETE RESTRICT;

            UPDATE class
            SET academic_year_id = (SELECT id FROM academic_years WHERE status = 'active' LIMIT 1)
            WHERE academic_year_id IS NULL;

            ALTER TABLE class ALTER COLUMN academic_year_id SET NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_class_academic_year ON class(academic_year_id);

            ALTER TABLE class DROP CONSTRAINT IF EXISTS class_name_academic_year_unique;
            ALTER TABLE class ADD CONSTRAINT class_name_academic_year_unique UNIQUE (name, academic_year_id);

            -- Best-effort from either live naming convention: "<Grade> - <Section>"
            -- (the Curriculum Ladder Wizard) or "<Grade> Section <Section>"
            -- (seed_data.py) -- confirmed as a real divergence by testing against
            -- the actually-running app's seeded tenant_a data.
            ALTER TABLE class ADD COLUMN IF NOT EXISTS section_label TEXT;
            UPDATE class SET section_label = TRIM(SUBSTRING(name FROM '(?i)(?:-|Section)\\s*(\\S+)\\s*$'))
            WHERE section_label IS NULL AND name ~ '(?i)(?:-|Section)\\s*\\S+\\s*$';

            ALTER TABLE students ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'enrolled';
            ALTER TABLE students DROP CONSTRAINT IF EXISTS students_status_check;
            ALTER TABLE students ADD CONSTRAINT students_status_check
                CHECK (status IN ('enrolled', 'graduated', 'withdrawn'));
            ALTER TABLE students ADD COLUMN IF NOT EXISTS exited_on DATE;

            CREATE TABLE IF NOT EXISTS year_rollover (
                id           BIGSERIAL   PRIMARY KEY,
                from_year_id BIGINT      NOT NULL REFERENCES academic_years(id) ON DELETE RESTRICT,
                to_year_id   BIGINT      NOT NULL REFERENCES academic_years(id) ON DELETE RESTRICT,
                state        TEXT        NOT NULL DEFAULT 'draft'
                             CHECK (state IN ('draft', 'previewed', 'committing', 'committed')),
                summary      JSONB       DEFAULT NULL,
                created_by   BIGINT      REFERENCES users(id) ON DELETE SET NULL,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (from_year_id, to_year_id)
            );

            CREATE TABLE IF NOT EXISTS year_rollover_line (
                id               BIGSERIAL   PRIMARY KEY,
                rollover_id      BIGINT      NOT NULL REFERENCES year_rollover(id) ON DELETE CASCADE,
                student_id       BIGINT      NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                from_class_id    BIGINT      REFERENCES class(id) ON DELETE SET NULL,
                to_level_id      BIGINT      REFERENCES levels(level_id) ON DELETE SET NULL,
                to_section_label TEXT        DEFAULT NULL,
                to_class_id      BIGINT      REFERENCES class(id) ON DELETE SET NULL,
                proposed_action  TEXT        NOT NULL CHECK (proposed_action IN ('promote', 'graduate', 'hold')),
                override_action  TEXT        CHECK (override_action IN ('promote', 'graduate', 'hold', 'withdraw')),
                exception_code   TEXT        CHECK (exception_code IN ('no_next_level', 'no_placement', 'over_capacity')),
                applied_at       TIMESTAMPTZ DEFAULT NULL,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (rollover_id, student_id)
            );
            CREATE INDEX IF NOT EXISTS idx_yrl_rollover ON year_rollover_line(rollover_id);
            """
        )

        # 22. School setup domain (Day-1 onboarding): profile, campus, contacts
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS school_profile (
                id                      BIGSERIAL   PRIMARY KEY,
                legal_name              TEXT        DEFAULT NULL,
                display_name            TEXT        DEFAULT NULL,
                school_code             TEXT        DEFAULT NULL,
                school_type             TEXT        DEFAULT NULL,
                regulator               TEXT        DEFAULT NULL,
                licence_number          TEXT        DEFAULT NULL,
                licence_expiry          DATE        DEFAULT NULL,
                tax_registration        TEXT        DEFAULT NULL,
                country                 TEXT        DEFAULT NULL,
                timezone                TEXT        DEFAULT NULL,
                hemisphere              TEXT        DEFAULT NULL,
                default_language        TEXT        DEFAULT NULL,
                additional_languages    TEXT[]      NOT NULL DEFAULT '{}',
                currency                TEXT        NOT NULL DEFAULT 'JOD',
                logo_url                TEXT        DEFAULT NULL,
                logo_dark_url           TEXT        DEFAULT NULL,
                primary_color           TEXT        DEFAULT NULL,
                website                 TEXT        DEFAULT NULL,
                profile_committed_at    TIMESTAMPTZ DEFAULT NULL,
                structure_committed_at  TIMESTAMPTZ DEFAULT NULL,
                curriculum_locked_at    TIMESTAMPTZ DEFAULT NULL,
                activated_at            TIMESTAMPTZ DEFAULT NULL,
                created_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS school_campus (
                id                      BIGSERIAL     PRIMARY KEY,
                name                    TEXT          NOT NULL,
                address_line1           TEXT          DEFAULT NULL,
                area                    TEXT          DEFAULT NULL,
                city                    TEXT          DEFAULT NULL,
                state_region            TEXT          DEFAULT NULL,
                country                 TEXT          DEFAULT NULL,
                po_box                  TEXT          DEFAULT NULL,
                postal_code             TEXT          DEFAULT NULL,
                latitude                NUMERIC(10,7) DEFAULT NULL,
                longitude               NUMERIC(10,7) DEFAULT NULL,
                day_start               TEXT          DEFAULT NULL,
                day_end                 TEXT          DEFAULT NULL,
                access_notes            TEXT          DEFAULT NULL,
                accessibility_notes     TEXT          DEFAULT NULL,
                is_primary              BOOLEAN       NOT NULL DEFAULT TRUE,
                created_at              TIMESTAMPTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS school_contact (
                id                      BIGSERIAL   PRIMARY KEY,
                role_title              TEXT        NOT NULL,
                name                    TEXT        NOT NULL,
                phone                   TEXT        DEFAULT NULL,
                email                   TEXT        DEFAULT NULL,
                is_emergency_contact    BOOLEAN     NOT NULL DEFAULT FALSE,
                escalation_order        INTEGER     DEFAULT NULL,
                visible_to              TEXT[]      NOT NULL DEFAULT '{staff}',
                created_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        # Guarantee exactly one profile row always exists. A brand-new tenant
        # starts un-activated (status "setup") until the onboarding wizard
        # completes; a tenant that already ships an activated row via init.sql
        # (tenant_a / tenant_b demo schemas) is left untouched by this no-op.
        await conn.execute(
            """
            INSERT INTO school_profile (currency)
            SELECT 'JOD'
            WHERE NOT EXISTS (SELECT 1 FROM school_profile)
            """
        )

        # 20. Seed default (non-custom) resource types, one CATEGORY at a time
        # so a tenant provisioned before a category existed in this seed (the
        # "staffing" rows below were added after some tenants were already
        # provisioned) gets that category backfilled on its next connection,
        # instead of being stuck with an empty "Staffing" section in the
        # wizard forever. The old version of this block only checked "does
        # ANY system resource type exist for this tenant" -- once true, it
        # never ran again for that tenant, no matter which categories were
        # actually present.
        #
        # Guarding per-CATEGORY (not per exact name) is deliberate: some
        # tenants already have transport/meals rows seeded under older names
        # ("Bus (20-seat)" vs. this list's "20-Seat Bus", "Kid Meal" vs.
        # "Kids Meal"). Guarding per-name would insert a second, differently
        # named row alongside each existing one instead of leaving an
        # already-populated category alone.
        _DEFAULT_RESOURCE_TYPES = [
            ("20-Seat Bus", "transport"),
            ("40-Seat Bus", "transport"),
            ("Male Supervisor", "staffing"),
            ("Female Supervisor", "staffing"),
            ("Kids Meal", "meals"),
            ("Adult Meal", "meals"),
        ]
        for _category in {c for _, c in _DEFAULT_RESOURCE_TYPES}:
            _category_has_rows = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM resource_types WHERE category = $1 AND is_custom = false)",
                _category,
            )
            if _category_has_rows:
                continue
            for _name, _row_category in _DEFAULT_RESOURCE_TYPES:
                if _row_category != _category:
                    continue
                await conn.execute(
                    """
                    INSERT INTO resource_types (name, category, is_custom, created_by_user_id, is_active)
                    VALUES ($1, $2, false, NULL, true)
                    """,
                    _name,
                    _row_category,
                )

        # 21. Every registered super_admin gets a real, visible row in this
        # tenant's own `users` table (role='super_admin'), so they show up in
        # ManageUsersView / the permissions matrix like any other staff
        # account, and so tenant-scoped FKs that reference users(id) --
        # event.created_by, resource_cost.set_by_user_id, and similar
        # reviewer/actor columns -- have a real row to point at if a
        # super_admin ever performs one of those actions while inspecting
        # this tenant. `public.super_admins` is readable here because a
        # tenant connection's search_path always includes `public` (see
        # CLAUDE.md's tenancy notes) -- schema-qualified below to be explicit
        # rather than rely on search_path ordering. Guarded by NOT EXISTS so
        # this stays a no-op on every later call for a tenant that already
        # has the row (this function reruns on every fresh-process first
        # touch of a tenant, not just on tenant creation).
        await conn.execute(
            """
            INSERT INTO users (email, role, password_hash)
            SELECT sa.email, 'super_admin', sa.password_hash
            FROM public.super_admins sa
            WHERE NOT EXISTS (
                SELECT 1 FROM users u WHERE u.email = sa.email
            )
            """
        )

        # 22. audit_log -- immutable, tenant-scoped audit trail (ADR 0006).
        # No PII: actor_email_hmac not the raw email, changed_fields stores
        # field NAMES only. See alembic/versions/tenant_0005_audit_log.py for
        # the full rationale.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id BIGSERIAL PRIMARY KEY,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                correlation_id UUID NULL,
                actor_user_id BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
                actor_email_hmac TEXT NULL,
                actor_role TEXT NOT NULL,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NULL,
                outcome TEXT NOT NULL CHECK (outcome IN ('allow','deny','error')),
                changed_fields TEXT[] NULL,
                metadata JSONB NOT NULL DEFAULT '{}',
                retention_until DATE NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_occurred ON audit_log (occurred_at DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_entity
                ON audit_log (entity_type, entity_id, occurred_at DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log (actor_user_id, occurred_at DESC);
            """
        )

        # 23. Money precision -- widen every money column from scale 2 to
        # scale 4. Scale 2 cannot represent JOD/KWD/BHD's 3-decimal minor
        # unit; scale 4 covers every currency in ISO_4217_MINOR_UNITS,
        # including CLF's 4. ALTER COLUMN TYPE is safe to re-run (a no-op
        # once already widened). payments.currency is backfilled from the
        # school's own currency, not a literal, before being made NOT NULL.
        await conn.execute(
            "ALTER TABLE event_class_map ALTER COLUMN ticket_price TYPE NUMERIC(14, 4);"
        )
        await conn.execute("ALTER TABLE event ALTER COLUMN school_subsidy TYPE NUMERIC(14, 4);")
        await conn.execute("ALTER TABLE event ALTER COLUMN total_cost TYPE NUMERIC(14, 4);")
        await conn.execute("ALTER TABLE resource_cost ALTER COLUMN unit_price TYPE NUMERIC(14, 4);")
        await conn.execute("ALTER TABLE resource_cost ALTER COLUMN total_cost TYPE NUMERIC(14, 4);")
        await conn.execute("ALTER TABLE payments ALTER COLUMN amount TYPE NUMERIC(14, 4);")
        await conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS currency VARCHAR(3);")
        await conn.execute(
            """
            UPDATE payments SET currency = COALESCE(
                (SELECT currency FROM school_profile ORDER BY id ASC LIMIT 1), 'USD'
            )
            WHERE currency IS NULL;
            """
        )
        await conn.execute("ALTER TABLE payments ALTER COLUMN currency SET NOT NULL;")


# =============================================================================
# Database Existence Guard
# =============================================================================
async def _ensure_database_exists(config: dict) -> None:
    """Create the database if it does not already exist."""
    db_name = config["database"]
    try:
        conn = await asyncpg.connect(
            host=config["host"],
            port=config["port"],
            user=config["user"],
            password=config["password"],
            database="postgres",
        )
        try:
            exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
            if not exists:
                await conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await conn.close()
    except Exception as exc:
        print(f"[database] Warning: could not ensure database '{db_name}' exists: {exc}")


async def _ensure_schema_exists(config: dict, schema_name: str) -> None:
    """Create the schema inside the database if it does not already exist."""
    try:
        conn = await asyncpg.connect(
            host=config["host"],
            port=config["port"],
            user=config["user"],
            password=config["password"],
            database=config["database"],
        )
        try:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')
        finally:
            await conn.close()
    except Exception as exc:
        print(f"[database] Warning: could not ensure schema '{schema_name}' exists: {exc}")


# =============================================================================
# Static/Seed Tenant Registry Configuration
# =============================================================================
shared_db_name = CONTROL_PLANE_DB_NAME

TENANT_DB_CONFIG: dict[str, dict] = {
    "tenant_a": {
        # host/port: DEAD — get_pool() always overwrites these back to the global
        # DB_HOST/DB_PORT before opening a connection (see "Force database name to
        # use control plane database" there). Queued for removal in the Step 6
        # Alembic migration — see FIX_PLAN.md Step 6.
        "host": os.getenv("TENANT_A_DB_HOST") or DB_HOST,
        "port": int(os.getenv("TENANT_A_DB_PORT") or DB_PORT),
        # user/password: reserved for a future database-per-tenant model. These DO
        # reach get_pool()'s connection config (unlike host/port above) — they just
        # currently resolve to the same admin credentials as everything else, since
        # every tenant here shares one Postgres instance. Live code, not dead code.
        "user": os.getenv("TENANT_A_DB_USER") or DB_USER,
        "password": os.getenv("TENANT_A_DB_PASSWORD") or DB_PASSWORD,
        # "database" is always forced to the control-plane DB name at every call
        # site below — TENANT_A_DB_NAME in .env.example is never read anywhere.
        # Dead env var, queued for removal alongside TENANT_B_DB_NAME/TENANT_C_DB_NAME
        # in the Step 6 Alembic migration.
        "database": shared_db_name,
    },
    "tenant_b": {
        "host": os.getenv("TENANT_B_DB_HOST") or DB_HOST,
        "port": int(os.getenv("TENANT_B_DB_PORT") or DB_PORT),
        "user": os.getenv("TENANT_B_DB_USER") or DB_USER,
        "password": os.getenv("TENANT_B_DB_PASSWORD") or DB_PASSWORD,
        "database": shared_db_name,
    },
    "tenant_c": {
        "host": os.getenv("TENANT_C_DB_HOST") or DB_HOST,
        "port": int(os.getenv("TENANT_C_DB_PORT") or DB_PORT),
        "user": os.getenv("TENANT_C_DB_USER") or DB_USER,
        "password": os.getenv("TENANT_C_DB_PASSWORD") or DB_PASSWORD,
        "database": shared_db_name,
    },
}

CONTROL_PLANE_DB_CONFIG: dict = {
    "host": DB_HOST,
    "port": DB_PORT,
    "user": DB_USER,
    "password": DB_PASSWORD,
    "database": CONTROL_PLANE_DB_NAME,
}


# =============================================================================
# Single-Tenant/Control-Plane Database Wrapper
# =============================================================================
class Database:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        schema_name: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._database = database
        self._schema_name = schema_name
        self.pool: asyncpg.Pool | None = None

    async def connect(
        self,
        init_fn,
        setup_conn_fn=None,
        min_size: int = 1,
        max_size: int = 5,
        acquire_timeout: float | None = None,
    ) -> asyncpg.Pool:
        if self.pool is None:
            server_settings = {}
            if self._schema_name:
                server_settings["search_path"] = f'"{self._schema_name}", public'

            self.pool = await asyncpg.create_pool(
                host=self._host,
                port=self._port,
                user=self._user,
                password=self._password,
                database=self._database,
                min_size=min_size,
                max_size=max_size,
                # NOTE: this is asyncpg's per-connection *establishment*
                # timeout (forwarded to asyncpg.connect() for each new
                # physical connection the pool opens), not an acquire-from-a-
                # saturated-pool timeout -- asyncpg has no pool-wide setting
                # for that; it exists per call as `timeout=` on
                # pool.acquire()/fetchrow()/execute(), which none of this
                # codebase's call sites currently pass. So an unreachable
                # database now fails fast instead of hanging, but a pool that
                # is merely saturated (every connection in use, max_size
                # already reached) still queues an acquire indefinitely --
                # closing that fully means threading `timeout=` through the
                # call sites themselves, not done here.
                timeout=acquire_timeout,
                server_settings=server_settings if server_settings else None,
                init=setup_conn_fn,
            )
            await init_fn(self.pool)
        return self.pool

    async def disconnect(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None


# =============================================================================
# Multi-Tenant & Control-Plane Database Manager
# =============================================================================
class DatabaseManager:
    """Thread-safe manager for per-tenant and control-plane connection pools."""

    def __init__(self) -> None:
        self._databases: dict[str, Database] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self._control_plane_db: Database | None = None
        self._control_plane_lock = asyncio.Lock()

    async def get_control_plane_pool(self) -> asyncpg.Pool:
        """Get the connection pool for the Control-Plane database."""
        if self._control_plane_db and self._control_plane_db.pool is not None:
            return self._control_plane_db.pool

        async with self._control_plane_lock:
            if self._control_plane_db is None:
                await _ensure_database_exists(CONTROL_PLANE_DB_CONFIG)
                self._control_plane_db = Database(**CONTROL_PLANE_DB_CONFIG)
            return await self._control_plane_db.connect(
                _initialize_control_plane_tables,
                min_size=CP_POOL_MIN,
                max_size=CP_POOL_MAX,
                acquire_timeout=CP_POOL_ACQUIRE_TIMEOUT,
            )

    async def get_pool(self, tenant_id: str) -> asyncpg.Pool:
        """Get the connection pool for the requested tenant."""
        # Fast path
        db = self._databases.get(tenant_id)
        if db and db.pool is not None:
            return db.pool

        # Slow path: create DB and pool under tenant lock
        async with self._locks_guard:
            lock = self._locks.setdefault(tenant_id, asyncio.Lock())

        async with lock:
            if tenant_id not in self._databases:
                # Dynamic tenant discovery from control plane
                cp_pool = await self.get_control_plane_pool()
                # db_host/db_port/db_name are selected but never used below (see the
                # forced overwrite further down) — dead columns, queued for removal
                # in the Step 6 Alembic migration (FIX_PLAN.md Step 6). db_user/
                # db_password ARE used — reserved/live, see TENANT_DB_CONFIG above.
                row = await cp_pool.fetchrow(
                    "SELECT db_host, db_port, db_user, db_password, db_name FROM tenants WHERE tenant_id = $1",
                    tenant_id,
                )
                if row:
                    config = {
                        "host": DB_HOST,
                        "port": DB_PORT,
                        "user": row["db_user"],
                        "password": row["db_password"],
                        "database": CONTROL_PLANE_DB_CONFIG["database"],
                    }
                else:
                    if tenant_id in TENANT_DB_CONFIG:
                        config = TENANT_DB_CONFIG[tenant_id]
                    else:
                        # Auto-register dynamic tenant in control plane
                        config = {
                            "host": DB_HOST,
                            "port": DB_PORT,
                            "user": DB_USER,
                            "password": DB_PASSWORD,
                            "database": CONTROL_PLANE_DB_CONFIG["database"],
                        }
                        await cp_pool.execute(
                            """
                            INSERT INTO tenants (tenant_id, name, db_host, db_port, db_user, db_password, db_name)
                            VALUES ($1, $2, $3, $4, $5, $6, $7)
                            ON CONFLICT (tenant_id) DO NOTHING
                            """,
                            tenant_id,
                            tenant_id.replace("_", " ").title(),
                            config["host"],
                            config["port"],
                            config["user"],
                            config["password"],
                            config["database"],
                        )

                # Force database name to use control plane database. This is what
                # makes tenants.db_host/db_port/db_name dead (see the comments on
                # that table and on the SELECT above) — every tenant lives in this
                # one physical database, isolated by schema, not a separate
                # per-tenant Postgres instance.
                config["host"] = DB_HOST
                config["port"] = DB_PORT
                config["database"] = CONTROL_PLANE_DB_CONFIG["database"]

                await _ensure_database_exists(config)
                await _ensure_schema_exists(config, tenant_id)
                self._databases[tenant_id] = Database(**config, schema_name=tenant_id)

            async def setup_conn_fn(conn):
                await conn.execute(f'SET search_path TO "{tenant_id}", public;')

            async def init_tenant_tables(pool):
                await _initialize_tenant_tables(pool, tenant_id)

            return await self._databases[tenant_id].connect(
                init_tenant_tables,
                setup_conn_fn=setup_conn_fn,
                min_size=TENANT_POOL_MIN,
                max_size=TENANT_POOL_MAX,
                acquire_timeout=TENANT_POOL_ACQUIRE_TIMEOUT,
            )

    async def disconnect_all(self) -> None:
        """Close all tenant and control plane connection pools."""
        for db in self._databases.values():
            await db.disconnect()
        if self._control_plane_db is not None:
            await self._control_plane_db.disconnect()


# Module-level singleton
db_manager = DatabaseManager()


async def get_db_pool(tenant_id: str) -> asyncpg.Pool:
    """Convenience function used by routers to obtain the correct tenant pool."""
    return await db_manager.get_pool(tenant_id)


async def get_control_plane_pool() -> asyncpg.Pool:
    """Convenience function to obtain the control plane pool."""
    return await db_manager.get_control_plane_pool()
