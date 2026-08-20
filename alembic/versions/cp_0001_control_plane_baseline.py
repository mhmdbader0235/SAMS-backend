"""control plane baseline

Reproduces the control-plane schema (public) exactly as it exists today,
built by app/core/database.py's _initialize_control_plane_tables() and
mirrored in init.sql. Schema only — no seed data (default tenants / the
bootstrap super_admin are an application-startup concern, not a migration
concern, and stay in database.py for now).

Revision ID: cp_0001
Revises:
Create Date: 2026-08-20
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "cp_0001"
down_revision = None
branch_labels = ("control_plane",)
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS citext;')
    op.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto;')
    op.execute('CREATE SCHEMA IF NOT EXISTS keycloak;')

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id   VARCHAR(50) PRIMARY KEY,
            name        TEXT        NOT NULL,
            -- db_host/db_port/db_name: dead (get_pool() always overwrites these
            -- back to the control-plane host/port/database). db_user/db_password
            -- are read on every get_pool() call but currently always resolve to
            -- the same admin credentials. See FIX_PLAN.md Step 6 / Step 5.
            db_host     TEXT        NOT NULL,
            db_port     INTEGER     NOT NULL,
            db_user     TEXT        NOT NULL,
            db_password TEXT        NOT NULL,
            db_name     TEXT        NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS parents (
            id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            email         CITEXT      UNIQUE NOT NULL,
            password_hash TEXT        NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            phone         VARCHAR(50) DEFAULT NULL,
            address       TEXT        DEFAULT NULL
        );
        """
    )
    # Redundant with the UNIQUE constraint above (which already indexes email),
    # but init.sql has always created it explicitly — kept for byte-for-byte
    # fidelity with the schema as it exists today, not because it's needed.
    op.execute('CREATE INDEX IF NOT EXISTS idx_parents_email ON parents(email);')

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admins (
            id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            email         CITEXT      UNIQUE NOT NULL,
            password_hash TEXT        NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    op.execute('CREATE INDEX IF NOT EXISTS idx_super_admins_email ON super_admins(email);')

    op.execute(
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
    op.execute('CREATE INDEX IF NOT EXISTS idx_pcl_parent_id ON parent_child_links(parent_id);')

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS parent_tenant_links (
            parent_id  UUID        NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
            tenant_id  VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (parent_id, tenant_id)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS invitations (
            id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            code          VARCHAR(100) UNIQUE NOT NULL,
            tenant_id     VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            role          TEXT        NOT NULL CHECK (role IN ('school_admin', 'teacher', 'parent', 'student', 'manager', 'finance', 'event_teacher', 'pending', 'super_admin')),
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

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_tenant_map (
            email      CITEXT      NOT NULL,
            tenant_id  VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            role       TEXT        NOT NULL DEFAULT 'student',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (email)
        );
        """
    )

    op.execute(
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


def downgrade() -> None:
    # Reverse dependency order. Extensions and the keycloak schema are left in
    # place — they're shared, low-risk resources, not something a schema
    # rollback needs to tear down.
    op.execute("DROP TABLE IF EXISTS user_invitations;")
    op.execute("DROP TABLE IF EXISTS user_tenant_map;")
    op.execute("DROP TABLE IF EXISTS invitations;")
    op.execute("DROP TABLE IF EXISTS parent_tenant_links;")
    op.execute("DROP TABLE IF EXISTS parent_child_links;")
    op.execute("DROP TABLE IF EXISTS super_admins;")
    op.execute("DROP TABLE IF EXISTS parents;")
    op.execute("DROP TABLE IF EXISTS tenants;")
