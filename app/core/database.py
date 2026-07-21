"""
Database layer — multi-tenant connection pool manager.

Each tenant (school) gets its own isolated PostgreSQL database.
On first request for a tenant, the database is auto-created and tables are
initialized via _initialize_tenant_tables(). Subsequent requests reuse the pool.

The Control-Plane DB manages global metadata including parents, super-admins,
tenant configurations, and parent-student links.
"""

import asyncio
import os

import asyncpg

from app.core.config import CONTROL_PLANE_DB_NAME, DB_HOST, DB_PASSWORD, DB_PORT, DB_USER


# =============================================================================
# Control-Plane Table Initialization
# =============================================================================
async def _initialize_control_plane_tables(pool: asyncpg.Pool) -> None:
    """Create all control-plane tables if they do not exist, and seed tenants."""
    async with pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS citext;")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

        # Tenants table
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                tenant_id   VARCHAR(50) PRIMARY KEY,
                name        TEXT        NOT NULL,
                db_host     TEXT        NOT NULL,
                db_port     INTEGER     NOT NULL,
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
        await conn.execute("ALTER TABLE parents ADD COLUMN IF NOT EXISTS phone VARCHAR(50) DEFAULT NULL;")
        await conn.execute("ALTER TABLE parents ADD COLUMN IF NOT EXISTS address TEXT DEFAULT NULL;")

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


# =============================================================================
# Tenant Table Initialization
# =============================================================================
async def _initialize_tenant_tables(pool: asyncpg.Pool) -> None:
    """Create all 14 tables in a newly provisioned tenant database."""
    async with pool.acquire() as conn:
        # Check if legacy tables exist. If so, drop them to avoid conflicts with new schema
        has_legacy = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 
                FROM information_schema.tables 
                WHERE table_name = 'grade_levels' OR table_name = 'classes'
            )
            """
        )
        if has_legacy:
            print("[database] Legacy table schema detected. Dropping old tables to recreate clean new schema...")
            await conn.execute(
                """
                DROP TABLE IF EXISTS 
                    comments, enrollments, notes, users, grade_levels, students, classes, attendance, events, 
                    event_grade_level_targets, event_class_targets, event_student_targets, notifications, 
                    student_health_and_records, levels, class, teachers, parenets, cost_budget, event, 
                    event_class_map, enrollment, payments, event_feedback CASCADE;
                """
            )

        # Extensions
        await conn.execute("CREATE EXTENSION IF NOT EXISTS citext;")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

        # 1. users
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            BIGSERIAL   PRIMARY KEY,
                email         CITEXT      UNIQUE NOT NULL,
                role          TEXT        NOT NULL CHECK (role IN ('school_admin', 'teacher', 'parent', 'student', 'manager', 'finance')),
                password_hash TEXT        NOT NULL,
                phone         VARCHAR(50) DEFAULT NULL,
                address       TEXT        DEFAULT NULL,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;")
        await conn.execute("ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('school_admin', 'teacher', 'parent', 'student', 'manager', 'finance'));")

        # 2. levels
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS levels (
                level_id   BIGSERIAL   PRIMARY KEY,
                name       TEXT        NOT NULL
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
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(50) DEFAULT NULL;")
        await conn.execute("ALTER TABLE parenets ADD COLUMN IF NOT EXISTS phone TEXT DEFAULT NULL;")

        # 5. class
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS class (
                id              BIGSERIAL   PRIMARY KEY,
                name            TEXT        NOT NULL,
                level_id        BIGINT      NOT NULL REFERENCES levels(level_id) ON DELETE RESTRICT,
                head_teacher_id BIGINT      NOT NULL REFERENCES teachers(id) ON DELETE RESTRICT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
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
                CREATE TYPE event_status AS ENUM ('draft', 'proposed', 'finance_approval', 'final_review', 'published');
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
            """
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
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS ix_events_status ON event(status);")

        # 21. Drop deprecated cost_budget table and references
        await conn.execute(
            """
            ALTER TABLE event_class_map DROP COLUMN IF EXISTS costbudget_id CASCADE;
            DROP TABLE IF EXISTS cost_budget CASCADE;
            """
        )

        # 20. Seed resource types if not present
        has_system_types = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM resource_types WHERE is_custom = false)")
        if not has_system_types:
            await conn.execute(
                """
                INSERT INTO resource_types (name, category, is_custom, created_by_user_id, is_active)
                VALUES
                ('20-Seat Bus', 'transport', false, NULL, true),
                ('40-Seat Bus', 'transport', false, NULL, true),
                ('Male Supervisor', 'staffing', false, NULL, true),
                ('Female Supervisor', 'staffing', false, NULL, true),
                ('Kids Meal', 'meals', false, NULL, true),
                ('Adult Meal', 'meals', false, NULL, true);
                """
            )



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
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", db_name
            )
            if not exists:
                await conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await conn.close()
    except Exception as exc:
        print(f"[database] Warning: could not ensure database '{db_name}' exists: {exc}")


# =============================================================================
# Static/Seed Tenant Registry Configuration
# =============================================================================
TENANT_DB_CONFIG: dict[str, dict] = {
    "tenant_a": {
        "host": os.getenv("TENANT_A_DB_HOST") or DB_HOST,
        "port": int(os.getenv("TENANT_A_DB_PORT") or DB_PORT),
        "user": os.getenv("TENANT_A_DB_USER") or DB_USER,
        "password": os.getenv("TENANT_A_DB_PASSWORD") or DB_PASSWORD,
        "database": os.getenv("TENANT_A_DB_NAME", "tenant_a_db"),
    },
    "tenant_b": {
        "host": os.getenv("TENANT_B_DB_HOST") or DB_HOST,
        "port": int(os.getenv("TENANT_B_DB_PORT") or DB_PORT),
        "user": os.getenv("TENANT_B_DB_USER") or DB_USER,
        "password": os.getenv("TENANT_B_DB_PASSWORD") or DB_PASSWORD,
        "database": os.getenv("TENANT_B_DB_NAME", "tenant_b_db"),
    },
    "tenant_c": {
        "host": os.getenv("TENANT_C_DB_HOST") or DB_HOST,
        "port": int(os.getenv("TENANT_C_DB_PORT") or DB_PORT),
        "user": os.getenv("TENANT_C_DB_USER") or DB_USER,
        "password": os.getenv("TENANT_C_DB_PASSWORD") or DB_PASSWORD,
        "database": os.getenv("TENANT_C_DB_NAME", "tenant_c_db"),
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
    def __init__(self, host: str, port: int, user: str, password: str, database: str) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._database = database
        self.pool: asyncpg.Pool | None = None

    async def connect(self, init_fn) -> asyncpg.Pool:
        if self.pool is None:
            self.pool = await asyncpg.create_pool(
                host=self._host,
                port=self._port,
                user=self._user,
                password=self._password,
                database=self._database,
                min_size=1,
                max_size=5,  # Enforces connection pooling limit of 5 max
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
            return await self._control_plane_db.connect(_initialize_control_plane_tables)

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
                row = await cp_pool.fetchrow(
                    "SELECT db_host, db_port, db_user, db_password, db_name FROM tenants WHERE tenant_id = $1",
                    tenant_id
                )
                if row:
                    config = {
                        "host": row["db_host"],
                        "port": row["db_port"],
                        "user": row["db_user"],
                        "password": row["db_password"],
                        "database": row["db_name"],
                    }
                else:
                    if tenant_id not in TENANT_DB_CONFIG:
                        raise ValueError(
                            f"Unknown tenant_id '{tenant_id}'. "
                            f"Available tenants: {list(TENANT_DB_CONFIG.keys())}"
                        )
                    config = TENANT_DB_CONFIG[tenant_id]

                await _ensure_database_exists(config)
                self._databases[tenant_id] = Database(**config)

            return await self._databases[tenant_id].connect(_initialize_tenant_tables)

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
