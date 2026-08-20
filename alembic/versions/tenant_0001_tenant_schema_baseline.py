"""tenant schema baseline

Reproduces the per-tenant schema exactly as it exists today, as built by
app/core/database.py's _initialize_tenant_tables() and mirrored in init.sql
for tenant_a/tenant_b. Runs once per tenant schema — see
alembic/apply_all_tenants.py to apply it to every registered tenant, or run
directly with:
    alembic -x target=tenant -x schema=<tenant_id> upgrade tenant@head

Table/column names are UNQUALIFIED throughout (no "tenant_x." prefix): the
env.py runner sets search_path to the target schema before this file's SQL
ever runs, which is what makes one script reusable across every tenant.

Deliberately NOT included, because they aren't part of "the current schema",
they're one-time historical cleanup steps from an older version of this app:
  - The has_legacy / DROP TABLE (grade_levels, classes, ...) block in
    _initialize_tenant_tables() — irrelevant to a schema created from scratch.
  - The `parenets` typo is kept AS-IS on purpose (see FIX_PLAN.md Step 5/6) —
    the rename is a separate, clearly-labeled follow-up migration.

Two real discrepancies were found between init.sql and database.py for this
baseline (see FIX_PLAN.md Step 6 for the full writeup) — both resolved in
favor of database.py, because _initialize_tenant_tables() re-runs against
EVERY tenant schema on first pool access after every backend restart
(including init.sql-seeded ones), so its final state always wins in practice:
  - class.capacity: nullable here (database.py's ADD COLUMN has no NOT NULL),
    not NOT NULL as init.sql and the ad-hoc copies in tenant_repository.py claim.
  - students.class_id: nullable here (database.py drops the NOT NULL later in
    the same init function), not NOT NULL as init.sql's inline definition claims.

Revision ID: tenant_0001
Revises:
Create Date: 2026-08-20
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "tenant_0001"
down_revision = None
branch_labels = ("tenant",)
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS citext;')
    op.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto;')

    # Table 1: users
    op.execute(
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
    op.execute('CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);')

    # Table 2: levels
    op.execute(
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
        """
    )

    # Academic settings & blackout dates
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS academic_settings (
            id               BIGSERIAL   PRIMARY KEY,
            academic_year    TEXT        NOT NULL,
            start_month      INTEGER     NOT NULL,
            weekend_days     TEXT[]      NOT NULL DEFAULT '{}',
            system           TEXT        NOT NULL DEFAULT 'US',
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS blackout_dates (
            id            BIGSERIAL   PRIMARY KEY,
            date          DATE        NOT NULL,
            title         TEXT        NOT NULL,
            tags          TEXT[]      NOT NULL DEFAULT '{}',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # Table 3: teachers
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS teachers (
            id   BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            name TEXT   NOT NULL
        );
        """
    )

    # Table 4: parenets (sic — typo kept intentionally, see docstring above)
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS parenets (
            id    BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            name  TEXT   NOT NULL,
            phone TEXT   DEFAULT NULL
        );
        """
    )

    # Table 5: class
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS class (
            id              BIGSERIAL   PRIMARY KEY,
            name            TEXT        NOT NULL,
            level_id        BIGINT      NOT NULL REFERENCES levels(level_id) ON DELETE RESTRICT,
            head_teacher_id BIGINT      NULL REFERENCES teachers(id) ON DELETE RESTRICT,
            capacity        INTEGER     DEFAULT 25,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    op.execute('CREATE INDEX IF NOT EXISTS idx_class_level ON class(level_id);')
    op.execute('CREATE INDEX IF NOT EXISTS idx_class_teacher ON class(head_teacher_id);')

    # Table 6: students
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS students (
            id         BIGINT      PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            name       TEXT        NOT NULL,
            class_id   BIGINT      NULL REFERENCES class(id) ON DELETE RESTRICT,
            gender     TEXT        DEFAULT NULL,
            birth_data TEXT        DEFAULT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    op.execute('CREATE INDEX IF NOT EXISTS idx_students_class ON students(class_id);')

    # Table 7: student_parent_map
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS student_parent_map (
            student_id BIGINT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            parent_id  BIGINT NOT NULL REFERENCES parenets(id) ON DELETE CASCADE,
            PRIMARY KEY (student_id, parent_id)
        );
        """
    )

    # Enum type event_status — created with its final value set in one shot
    # (database.py builds this incrementally via a later ALTER TYPE ... ADD
    # VALUE 'approved', a backward-compat step for pre-existing deployments
    # that predate 'approved'; irrelevant for a fresh baseline).
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE event_status AS ENUM ('draft', 'resource_planning', 'proposed', 'approved', 'finance_approval', 'final_review', 'published');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )

    # Table 8: event
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS event (
            id                   BIGSERIAL      PRIMARY KEY,
            title                TEXT           NOT NULL,
            description          TEXT           NOT NULL DEFAULT '',
            address              TEXT           DEFAULT NULL,
            event_map_id         BIGINT         DEFAULT NULL,
            school_subsidy       NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
            date                 TIMESTAMPTZ    NOT NULL,
            created_by           BIGINT         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at           TIMESTAMPTZ    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            status               event_status   NOT NULL DEFAULT 'draft',
            predicted_attendance INTEGER        NULL,
            manager_reviewer_id  BIGINT         NULL REFERENCES users(id),
            finance_reviewer_id  BIGINT         NULL REFERENCES users(id),
            total_cost           NUMERIC(12,2)  NULL,
            submitted_at         TIMESTAMPTZ    NULL,
            manager_approved_at  TIMESTAMPTZ    NULL,
            finance_priced_at    TIMESTAMPTZ    NULL,
            published_at         TIMESTAMPTZ    NULL,
            rejection_reason     TEXT           NULL
        );
        """
    )
    op.execute('CREATE INDEX IF NOT EXISTS idx_event_creator ON event(created_by);')
    op.execute('CREATE INDEX IF NOT EXISTS idx_event_date ON event(date);')
    op.execute('CREATE INDEX IF NOT EXISTS ix_events_status ON event(status);')

    # Table 9: event_class_map
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS event_class_map (
            id            BIGSERIAL      PRIMARY KEY,
            event_id      BIGINT         NOT NULL REFERENCES event(id) ON DELETE CASCADE,
            class_id      BIGINT         NOT NULL REFERENCES class(id) ON DELETE CASCADE,
            ticket_price  NUMERIC(10, 2) NOT NULL DEFAULT 0.00
        );
        """
    )
    op.execute('CREATE INDEX IF NOT EXISTS idx_ecm_event ON event_class_map(event_id);')
    op.execute('CREATE INDEX IF NOT EXISTS idx_ecm_class ON event_class_map(class_id);')

    # Table 10: enrollment
    op.execute(
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
    op.execute('CREATE INDEX IF NOT EXISTS idx_enrollment_student ON enrollment(student_id);')
    op.execute('CREATE INDEX IF NOT EXISTS idx_enrollment_ecm ON enrollment(event_class_map_id);')

    # Table 11: payments
    op.execute(
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

    # Table 12: event_feedback
    op.execute(
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

    # Table 13: student_health_and_records (PII)
    op.execute(
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

    # Table 14: notifications
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            event_id          BIGINT       NOT NULL REFERENCES event(id) ON DELETE CASCADE,
            recipient_user_id BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            delivered_at      TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
            read_at           TIMESTAMPTZ  DEFAULT NULL,
            title_override    VARCHAR(255) DEFAULT NULL
        );
        """
    )
    op.execute('CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications(recipient_user_id);')

    # Table 15: resource_types
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS resource_types (
            id                 SERIAL       PRIMARY KEY,
            name               VARCHAR(120) NOT NULL,
            category           VARCHAR(30)  NOT NULL DEFAULT 'other',
            is_custom          BOOLEAN      NOT NULL DEFAULT false,
            created_by_user_id BIGINT       NULL REFERENCES users(id) ON DELETE SET NULL,
            is_active          BOOLEAN      NOT NULL DEFAULT true,
            created_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
        """
    )

    # Table 16: resources
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS resources (
            id                 SERIAL      PRIMARY KEY,
            event_id           BIGINT      NOT NULL REFERENCES event(id) ON DELETE CASCADE,
            resource_type_id   INTEGER     NOT NULL REFERENCES resource_types(id),
            description        TEXT        NULL,
            quantity           INTEGER     NOT NULL CHECK (quantity > 0),
            added_by_user_id   BIGINT      NOT NULL REFERENCES users(id),
            updated_by_user_id BIGINT      NULL REFERENCES users(id),
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute('CREATE INDEX IF NOT EXISTS ix_resources_event ON resources(event_id);')

    # Table 17: resource_cost
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS resource_cost (
            id             SERIAL        PRIMARY KEY,
            event_id       BIGINT        NOT NULL REFERENCES event(id) ON DELETE CASCADE,
            resource_id    INTEGER       NOT NULL UNIQUE REFERENCES resources(id) ON DELETE CASCADE,
            unit_price     NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
            total_cost     NUMERIC(12,2) NOT NULL,
            currency       VARCHAR(3)    NOT NULL DEFAULT 'JOD',
            set_by_user_id BIGINT        NOT NULL REFERENCES users(id),
            updated_at     TIMESTAMPTZ   NOT NULL DEFAULT now()
        );
        """
    )

    # Table 18: school_profile / school_campus / school_contact (Day-1 onboarding)
    op.execute(
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
        """
    )
    op.execute(
        """
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
        """
    )
    op.execute(
        """
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
    # Guarantee exactly one profile row exists (mirrors _initialize_tenant_tables()
    # — this one insert is idempotent bootstrap, not per-tenant demo content, so
    # unlike the tenant_a/tenant_b seed data it belongs here, not in seed_data.py).
    op.execute(
        """
        INSERT INTO school_profile (currency)
        SELECT 'JOD'
        WHERE NOT EXISTS (SELECT 1 FROM school_profile);
        """
    )

    # Seed system (non-custom) resource types — same rationale as the
    # school_profile row above: idempotent bootstrap data every tenant needs,
    # not tenant-specific demo content.
    op.execute(
        """
        INSERT INTO resource_types (name, category, is_custom, created_by_user_id, is_active)
        SELECT * FROM (VALUES
            ('20-Seat Bus',       'transport', false, NULL::BIGINT, true),
            ('40-Seat Bus',       'transport', false, NULL::BIGINT, true),
            ('Male Supervisor',   'staffing',  false, NULL::BIGINT, true),
            ('Female Supervisor', 'staffing',  false, NULL::BIGINT, true),
            ('Kids Meal',         'meals',     false, NULL::BIGINT, true),
            ('Adult Meal',        'meals',     false, NULL::BIGINT, true)
        ) AS seed(name, category, is_custom, created_by_user_id, is_active)
        WHERE NOT EXISTS (SELECT 1 FROM resource_types WHERE is_custom = false);
        """
    )


def downgrade() -> None:
    # Reverse dependency order.
    op.execute("DROP TABLE IF EXISTS school_contact;")
    op.execute("DROP TABLE IF EXISTS school_campus;")
    op.execute("DROP TABLE IF EXISTS school_profile;")
    op.execute("DROP TABLE IF EXISTS resource_cost;")
    op.execute("DROP TABLE IF EXISTS resources;")
    op.execute("DROP TABLE IF EXISTS resource_types;")
    op.execute("DROP TABLE IF EXISTS notifications;")
    op.execute("DROP TABLE IF EXISTS student_health_and_records;")
    op.execute("DROP TABLE IF EXISTS event_feedback;")
    op.execute("DROP TABLE IF EXISTS payments;")
    op.execute("DROP TABLE IF EXISTS enrollment;")
    op.execute("DROP TABLE IF EXISTS event_class_map;")
    op.execute("DROP TABLE IF EXISTS event;")
    op.execute("DROP TYPE IF EXISTS event_status;")
    op.execute("DROP TABLE IF EXISTS student_parent_map;")
    op.execute("DROP TABLE IF EXISTS students;")
    op.execute("DROP TABLE IF EXISTS class;")
    op.execute("DROP TABLE IF EXISTS parenets;")
    op.execute("DROP TABLE IF EXISTS teachers;")
    op.execute("DROP TABLE IF EXISTS blackout_dates;")
    op.execute("DROP TABLE IF EXISTS academic_settings;")
    op.execute("DROP TABLE IF EXISTS levels;")
    op.execute("DROP TABLE IF EXISTS users;")
