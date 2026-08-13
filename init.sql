-- =============================================================================
-- SchoolDesk — PostgreSQL Target Schema Initialization Script
-- Runs automatically when a new Postgres container starts (Docker init.d)
-- Also used as the reference DDL for manual DB setup.
-- =============================================================================

-- Extensions (must be in public schema)
CREATE EXTENSION IF NOT EXISTS citext;     -- Case-insensitive text (email)
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

-- Keycloak Schema (isolated namespace for OIDC data)
CREATE SCHEMA IF NOT EXISTS keycloak;

-- =============================================================================
-- 1. CONTROL-PLANE TABLES (in public schema — the default)
-- =============================================================================

-- Tenants Table
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

-- Seed default tenants
INSERT INTO tenants (tenant_id, name, db_host, db_port, db_user, db_password, db_name)
VALUES
    ('tenant_a', 'Tenant A', '127.0.0.1', 5433, 'admin', 'secure_local_password', 'user_service_db'),
    ('tenant_b', 'Tenant B', '127.0.0.1', 5433, 'admin', 'secure_local_password', 'user_service_db'),
    ('tenant_c', 'Tenant C', '127.0.0.1', 5433, 'admin', 'secure_local_password', 'user_service_db')
ON CONFLICT DO NOTHING;

-- Parents Table
CREATE TABLE IF NOT EXISTS parents (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email         CITEXT      UNIQUE NOT NULL,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    phone         VARCHAR(50) DEFAULT NULL,
    address       TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_parents_email ON parents(email);

-- Super Admins Table
CREATE TABLE IF NOT EXISTS super_admins (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email         CITEXT      UNIQUE NOT NULL,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_super_admins_email ON super_admins(email);

-- Parent-Child Cross-SCHEMA Link Table
CREATE TABLE IF NOT EXISTS parent_child_links (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id  UUID        NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
    tenant_id  VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    student_id UUID        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (parent_id, tenant_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_pcl_parent_id ON parent_child_links(parent_id);

-- Parent-Tenant Link Table
CREATE TABLE IF NOT EXISTS parent_tenant_links (
    parent_id  UUID        NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
    tenant_id  VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (parent_id, tenant_id)
);

-- Invitations Table
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

-- User-to-tenant mapping table — used to resolve which tenant a Keycloak user belongs to
CREATE TABLE IF NOT EXISTS user_tenant_map (
    email      CITEXT      NOT NULL,
    tenant_id  VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    role       TEXT        NOT NULL DEFAULT 'student',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (email)
);

-- Audit log for pre-provisioned user invitations
CREATE TABLE IF NOT EXISTS user_invitations (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email         CITEXT      NOT NULL,
    tenant_id     VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    role          TEXT        NOT NULL,
    inviter_id    TEXT        DEFAULT NULL,
    status        TEXT        NOT NULL DEFAULT 'pending',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);



-- =============================================================================
-- 2. TENANT SCHEMA: tenant_a (isolated logical schema)
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS tenant_a;

-- Table 1: users
CREATE TABLE IF NOT EXISTS tenant_a.users (
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

CREATE INDEX IF NOT EXISTS idx_users_email ON tenant_a.users(email);

-- Table 2: levels
CREATE TABLE IF NOT EXISTS tenant_a.levels (
    level_id   BIGSERIAL   PRIMARY KEY,
    name       TEXT        NOT NULL
);

-- Table 3: teachers
CREATE TABLE IF NOT EXISTS tenant_a.teachers (
    id   BIGINT PRIMARY KEY REFERENCES tenant_a.users(id) ON DELETE CASCADE,
    name TEXT   NOT NULL
);

-- Table 4: parenets (note: typo preserved from original schema)
CREATE TABLE IF NOT EXISTS tenant_a.parenets (
    id    BIGINT PRIMARY KEY REFERENCES tenant_a.users(id) ON DELETE CASCADE,
    name  TEXT   NOT NULL,
    phone TEXT   DEFAULT NULL
);

-- Table 5: class
CREATE TABLE IF NOT EXISTS tenant_a.class (
    id              BIGSERIAL   PRIMARY KEY,
    name            TEXT        NOT NULL,
    level_id        BIGINT      NOT NULL REFERENCES tenant_a.levels(level_id) ON DELETE RESTRICT,
    head_teacher_id BIGINT      NOT NULL REFERENCES tenant_a.teachers(id) ON DELETE RESTRICT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_class_level ON tenant_a.class(level_id);
CREATE INDEX IF NOT EXISTS idx_class_teacher ON tenant_a.class(head_teacher_id);

-- Table 6: students
CREATE TABLE IF NOT EXISTS tenant_a.students (
    id         BIGINT      PRIMARY KEY REFERENCES tenant_a.users(id) ON DELETE CASCADE,
    name       TEXT        NOT NULL,
    class_id   BIGINT      NOT NULL REFERENCES tenant_a.class(id) ON DELETE RESTRICT,
    gender     TEXT        DEFAULT NULL,
    birth_data TEXT        DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_students_class ON tenant_a.students(class_id);

-- Table 7: student_parent_map
CREATE TABLE IF NOT EXISTS tenant_a.student_parent_map (
    student_id BIGINT NOT NULL REFERENCES tenant_a.students(id) ON DELETE CASCADE,
    parent_id  BIGINT NOT NULL REFERENCES tenant_a.parenets(id) ON DELETE CASCADE,
    PRIMARY KEY (student_id, parent_id)
);

-- Enum Type event_status
DO $$ BEGIN
    CREATE TYPE tenant_a.event_status AS ENUM ('draft', 'resource_planning', 'proposed', 'approved', 'finance_approval', 'final_review', 'published');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Table 8: event
CREATE TABLE IF NOT EXISTS tenant_a.event (
    id             BIGSERIAL                PRIMARY KEY,
    title          TEXT                     NOT NULL,
    description    TEXT                     NOT NULL DEFAULT '',
    address        TEXT                     DEFAULT NULL,
    event_map_id   BIGINT                   DEFAULT NULL,
    school_subsidy NUMERIC(10, 2)           NOT NULL DEFAULT 0.00,
    date           TIMESTAMPTZ              NOT NULL,
    created_by     BIGINT                   NOT NULL REFERENCES tenant_a.users(id) ON DELETE CASCADE,
    created_at     TIMESTAMPTZ              NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status         tenant_a.event_status    NOT NULL DEFAULT 'draft',
    predicted_attendance INTEGER            NULL,
    manager_reviewer_id BIGINT              NULL REFERENCES tenant_a.users(id),
    finance_reviewer_id BIGINT              NULL REFERENCES tenant_a.users(id),
    total_cost     NUMERIC(12,2)            NULL,
    submitted_at   TIMESTAMPTZ              NULL,
    manager_approved_at TIMESTAMPTZ         NULL,
    finance_priced_at TIMESTAMPTZ           NULL,
    published_at   TIMESTAMPTZ              NULL,
    rejection_reason TEXT                   NULL
);

CREATE INDEX IF NOT EXISTS idx_event_creator ON tenant_a.event(created_by);
CREATE INDEX IF NOT EXISTS idx_event_date ON tenant_a.event(date);
CREATE INDEX IF NOT EXISTS ix_events_status ON tenant_a.event(status);

-- Table 9: event_class_map
CREATE TABLE IF NOT EXISTS tenant_a.event_class_map (
    id            BIGSERIAL      PRIMARY KEY,
    event_id      BIGINT         NOT NULL REFERENCES tenant_a.event(id) ON DELETE CASCADE,
    class_id      BIGINT         NOT NULL REFERENCES tenant_a.class(id) ON DELETE CASCADE,
    ticket_price  NUMERIC(10, 2) NOT NULL DEFAULT 0.00
);

CREATE INDEX IF NOT EXISTS idx_ecm_event ON tenant_a.event_class_map(event_id);
CREATE INDEX IF NOT EXISTS idx_ecm_class ON tenant_a.event_class_map(class_id);

-- Table 10: enrollment
CREATE TABLE IF NOT EXISTS tenant_a.enrollment (
    id                 BIGSERIAL   PRIMARY KEY,
    student_id         BIGINT      NOT NULL REFERENCES tenant_a.students(id) ON DELETE CASCADE,
    event_class_map_id BIGINT      NOT NULL REFERENCES tenant_a.event_class_map(id) ON DELETE CASCADE,
    state              TEXT        NOT NULL CHECK (state IN ('requested_by_student', 'approved_by_parent', 'approved_by_teacher', 'rejected_by_parent', 'rejected_by_teacher')),
    teacher_id         BIGINT      DEFAULT NULL REFERENCES tenant_a.teachers(id) ON DELETE SET NULL,
    parent_id          BIGINT      DEFAULT NULL REFERENCES tenant_a.parenets(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (student_id, event_class_map_id)
);

CREATE INDEX IF NOT EXISTS idx_enrollment_student ON tenant_a.enrollment(student_id);
CREATE INDEX IF NOT EXISTS idx_enrollment_ecm ON tenant_a.enrollment(event_class_map_id);

-- Table 11: payments
CREATE TABLE IF NOT EXISTS tenant_a.payments (
    id            BIGSERIAL      PRIMARY KEY,
    enrollment_id BIGINT         NOT NULL REFERENCES tenant_a.enrollment(id) ON DELETE CASCADE,
    amount        NUMERIC(10, 2) NOT NULL,
    status        TEXT           NOT NULL CHECK (status IN ('pending', 'paid', 'refunded')),
    created_at    TIMESTAMPTZ    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Table 12: event_feedback
CREATE TABLE IF NOT EXISTS tenant_a.event_feedback (
    id         BIGSERIAL   PRIMARY KEY,
    event_id   BIGINT      NOT NULL REFERENCES tenant_a.event(id) ON DELETE CASCADE,
    user_id    BIGINT      NOT NULL REFERENCES tenant_a.users(id) ON DELETE CASCADE,
    rating     INT         NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comments   TEXT        DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Table 13: student_health_and_records
CREATE TABLE IF NOT EXISTS tenant_a.student_health_and_records (
    id                            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id                    BIGINT      UNIQUE NOT NULL REFERENCES tenant_a.students(id) ON DELETE CASCADE,
    national_id_encrypted         TEXT        NOT NULL,
    medical_conditions_encrypted  TEXT        NOT NULL,
    emergency_contact_encrypted   TEXT        NOT NULL
);

-- Table 14: notifications
CREATE TABLE IF NOT EXISTS tenant_a.notifications (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id          BIGINT       NOT NULL REFERENCES tenant_a.event(id) ON DELETE CASCADE,
    recipient_user_id BIGINT       NOT NULL REFERENCES tenant_a.users(id) ON DELETE CASCADE,
    delivered_at      TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at           TIMESTAMPTZ  DEFAULT NULL,
    title_override    VARCHAR(255) DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON tenant_a.notifications(recipient_user_id);

-- Table 15: resource_types
CREATE TABLE IF NOT EXISTS tenant_a.resource_types (
    id                 SERIAL       PRIMARY KEY,
    name               VARCHAR(120) NOT NULL,
    category           VARCHAR(30)  NOT NULL DEFAULT 'other',
    is_custom          BOOLEAN      NOT NULL DEFAULT false,
    created_by_user_id BIGINT       NULL REFERENCES tenant_a.users(id) ON DELETE SET NULL,
    is_active          BOOLEAN      NOT NULL DEFAULT true,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Table 16: resources
CREATE TABLE IF NOT EXISTS tenant_a.resources (
    id                 SERIAL      PRIMARY KEY,
    event_id           BIGINT      NOT NULL REFERENCES tenant_a.event(id) ON DELETE CASCADE,
    resource_type_id   INTEGER     NOT NULL REFERENCES tenant_a.resource_types(id),
    description        TEXT        NULL,
    quantity           INTEGER     NOT NULL CHECK (quantity > 0),
    added_by_user_id   BIGINT      NOT NULL REFERENCES tenant_a.users(id),
    updated_by_user_id BIGINT      NULL REFERENCES tenant_a.users(id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_resources_event ON tenant_a.resources(event_id);

-- Table 17: resource_cost
CREATE TABLE IF NOT EXISTS tenant_a.resource_cost (
    id             SERIAL        PRIMARY KEY,
    event_id       BIGINT        NOT NULL REFERENCES tenant_a.event(id) ON DELETE CASCADE,
    resource_id    INTEGER       NOT NULL UNIQUE REFERENCES tenant_a.resources(id) ON DELETE CASCADE,
    unit_price     NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    total_cost     NUMERIC(12,2) NOT NULL,
    currency       VARCHAR(3)    NOT NULL DEFAULT 'JOD',
    set_by_user_id BIGINT        NOT NULL REFERENCES tenant_a.users(id),
    updated_at     TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- Seed System Resource Types for tenant_a
INSERT INTO tenant_a.resource_types (name, category, is_custom, created_by_user_id, is_active)
VALUES
    ('20-Seat Bus',       'transport', false, NULL, true),
    ('40-Seat Bus',       'transport', false, NULL, true),
    ('Male Supervisor',   'staffing',  false, NULL, true),
    ('Female Supervisor', 'staffing',  false, NULL, true),
    ('Kids Meal',         'meals',     false, NULL, true),
    ('Adult Meal',        'meals',     false, NULL, true)
ON CONFLICT DO NOTHING;


-- =============================================================================
-- 3. TENANT SCHEMA: tenant_b (isolated logical schema)
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS tenant_b;

-- Table 1: users
CREATE TABLE IF NOT EXISTS tenant_b.users (
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

CREATE INDEX IF NOT EXISTS idx_users_email_b ON tenant_b.users(email);

-- Table 2: levels
CREATE TABLE IF NOT EXISTS tenant_b.levels (
    level_id   BIGSERIAL   PRIMARY KEY,
    name       TEXT        NOT NULL
);

-- Table 3: teachers
CREATE TABLE IF NOT EXISTS tenant_b.teachers (
    id   BIGINT PRIMARY KEY REFERENCES tenant_b.users(id) ON DELETE CASCADE,
    name TEXT   NOT NULL
);

-- Table 4: parenets
CREATE TABLE IF NOT EXISTS tenant_b.parenets (
    id    BIGINT PRIMARY KEY REFERENCES tenant_b.users(id) ON DELETE CASCADE,
    name  TEXT   NOT NULL,
    phone TEXT   DEFAULT NULL
);

-- Table 5: class
CREATE TABLE IF NOT EXISTS tenant_b.class (
    id              BIGSERIAL   PRIMARY KEY,
    name            TEXT        NOT NULL,
    level_id        BIGINT      NOT NULL REFERENCES tenant_b.levels(level_id) ON DELETE RESTRICT,
    head_teacher_id BIGINT      NOT NULL REFERENCES tenant_b.teachers(id) ON DELETE RESTRICT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_class_level_b ON tenant_b.class(level_id);
CREATE INDEX IF NOT EXISTS idx_class_teacher_b ON tenant_b.class(head_teacher_id);

-- Table 6: students
CREATE TABLE IF NOT EXISTS tenant_b.students (
    id         BIGINT      PRIMARY KEY REFERENCES tenant_b.users(id) ON DELETE CASCADE,
    name       TEXT        NOT NULL,
    class_id   BIGINT      NOT NULL REFERENCES tenant_b.class(id) ON DELETE RESTRICT,
    gender     TEXT        DEFAULT NULL,
    birth_data TEXT        DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_students_class_b ON tenant_b.students(class_id);

-- Table 7: student_parent_map
CREATE TABLE IF NOT EXISTS tenant_b.student_parent_map (
    student_id BIGINT NOT NULL REFERENCES tenant_b.students(id) ON DELETE CASCADE,
    parent_id  BIGINT NOT NULL REFERENCES tenant_b.parenets(id) ON DELETE CASCADE,
    PRIMARY KEY (student_id, parent_id)
);

-- Enum Type event_status
DO $$ BEGIN
    CREATE TYPE tenant_b.event_status AS ENUM ('draft', 'resource_planning', 'proposed', 'finance_approval', 'final_review', 'published');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Table 8: event
CREATE TABLE IF NOT EXISTS tenant_b.event (
    id             BIGSERIAL                PRIMARY KEY,
    title          TEXT                     NOT NULL,
    description    TEXT                     NOT NULL DEFAULT '',
    address        TEXT                     DEFAULT NULL,
    event_map_id   BIGINT                   DEFAULT NULL,
    school_subsidy NUMERIC(10, 2)           NOT NULL DEFAULT 0.00,
    date           TIMESTAMPTZ              NOT NULL,
    created_by     BIGINT                   NOT NULL REFERENCES tenant_b.users(id) ON DELETE CASCADE,
    created_at     TIMESTAMPTZ              NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status         tenant_b.event_status    NOT NULL DEFAULT 'draft',
    predicted_attendance INTEGER            NULL,
    manager_reviewer_id BIGINT              NULL REFERENCES tenant_b.users(id),
    finance_reviewer_id BIGINT              NULL REFERENCES tenant_b.users(id),
    total_cost     NUMERIC(12,2)            NULL,
    submitted_at   TIMESTAMPTZ              NULL,
    manager_approved_at TIMESTAMPTZ         NULL,
    finance_priced_at TIMESTAMPTZ           NULL,
    published_at   TIMESTAMPTZ              NULL
);

CREATE INDEX IF NOT EXISTS idx_event_creator_b ON tenant_b.event(created_by);
CREATE INDEX IF NOT EXISTS idx_event_date_b ON tenant_b.event(date);
CREATE INDEX IF NOT EXISTS ix_events_status_b ON tenant_b.event(status);

-- Table 9: event_class_map
CREATE TABLE IF NOT EXISTS tenant_b.event_class_map (
    id            BIGSERIAL      PRIMARY KEY,
    event_id      BIGINT         NOT NULL REFERENCES tenant_b.event(id) ON DELETE CASCADE,
    class_id      BIGINT         NOT NULL REFERENCES tenant_b.class(id) ON DELETE CASCADE,
    ticket_price  NUMERIC(10, 2) NOT NULL DEFAULT 0.00
);

CREATE INDEX IF NOT EXISTS idx_ecm_event_b ON tenant_b.event_class_map(event_id);
CREATE INDEX IF NOT EXISTS idx_ecm_class_b ON tenant_b.event_class_map(class_id);

-- Table 10: enrollment
CREATE TABLE IF NOT EXISTS tenant_b.enrollment (
    id                 BIGSERIAL   PRIMARY KEY,
    student_id         BIGINT      NOT NULL REFERENCES tenant_b.students(id) ON DELETE CASCADE,
    event_class_map_id BIGINT      NOT NULL REFERENCES tenant_b.event_class_map(id) ON DELETE CASCADE,
    state              TEXT        NOT NULL CHECK (state IN ('requested_by_student', 'approved_by_parent', 'approved_by_teacher', 'rejected_by_parent', 'rejected_by_teacher')),
    teacher_id         BIGINT      DEFAULT NULL REFERENCES tenant_b.teachers(id) ON DELETE SET NULL,
    parent_id          BIGINT      DEFAULT NULL REFERENCES tenant_b.parenets(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (student_id, event_class_map_id)
);

CREATE INDEX IF NOT EXISTS idx_enrollment_student_b ON tenant_b.enrollment(student_id);
CREATE INDEX IF NOT EXISTS idx_enrollment_ecm_b ON tenant_b.enrollment(event_class_map_id);

-- Table 11: payments
CREATE TABLE IF NOT EXISTS tenant_b.payments (
    id            BIGSERIAL      PRIMARY KEY,
    enrollment_id BIGINT         NOT NULL REFERENCES tenant_b.enrollment(id) ON DELETE CASCADE,
    amount        NUMERIC(10, 2) NOT NULL,
    status        TEXT           NOT NULL CHECK (status IN ('pending', 'paid', 'refunded')),
    created_at    TIMESTAMPTZ    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Table 12: event_feedback
CREATE TABLE IF NOT EXISTS tenant_b.event_feedback (
    id         BIGSERIAL   PRIMARY KEY,
    event_id   BIGINT      NOT NULL REFERENCES tenant_b.event(id) ON DELETE CASCADE,
    user_id    BIGINT      NOT NULL REFERENCES tenant_b.users(id) ON DELETE CASCADE,
    rating     INT         NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comments   TEXT        DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Table 13: student_health_and_records
CREATE TABLE IF NOT EXISTS tenant_b.student_health_and_records (
    id                            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id                    BIGINT      UNIQUE NOT NULL REFERENCES tenant_b.students(id) ON DELETE CASCADE,
    national_id_encrypted         TEXT        NOT NULL,
    medical_conditions_encrypted  TEXT        NOT NULL,
    emergency_contact_encrypted   TEXT        NOT NULL
);

-- Table 14: notifications
CREATE TABLE IF NOT EXISTS tenant_b.notifications (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id          BIGINT       NOT NULL REFERENCES tenant_b.event(id) ON DELETE CASCADE,
    recipient_user_id BIGINT       NOT NULL REFERENCES tenant_b.users(id) ON DELETE CASCADE,
    delivered_at      TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at           TIMESTAMPTZ  DEFAULT NULL,
    title_override    VARCHAR(255) DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_recipient_b ON tenant_b.notifications(recipient_user_id);

-- Table 15: resource_types
CREATE TABLE IF NOT EXISTS tenant_b.resource_types (
    id                 SERIAL       PRIMARY KEY,
    name               VARCHAR(120) NOT NULL,
    category           VARCHAR(30)  NOT NULL DEFAULT 'other',
    is_custom          BOOLEAN      NOT NULL DEFAULT false,
    created_by_user_id BIGINT       NULL REFERENCES tenant_b.users(id) ON DELETE SET NULL,
    is_active          BOOLEAN      NOT NULL DEFAULT true,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Table 16: resources
CREATE TABLE IF NOT EXISTS tenant_b.resources (
    id                 SERIAL      PRIMARY KEY,
    event_id           BIGINT      NOT NULL REFERENCES tenant_b.event(id) ON DELETE CASCADE,
    resource_type_id   INTEGER     NOT NULL REFERENCES tenant_b.resource_types(id),
    description        TEXT        NULL,
    quantity           INTEGER     NOT NULL CHECK (quantity > 0),
    added_by_user_id   BIGINT      NOT NULL REFERENCES tenant_b.users(id),
    updated_by_user_id BIGINT      NULL REFERENCES tenant_b.users(id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_resources_event_b ON tenant_b.resources(event_id);

-- Table 17: resource_cost
CREATE TABLE IF NOT EXISTS tenant_b.resource_cost (
    id             SERIAL        PRIMARY KEY,
    event_id       BIGINT        NOT NULL REFERENCES tenant_b.event(id) ON DELETE CASCADE,
    resource_id    INTEGER       NOT NULL UNIQUE REFERENCES tenant_b.resources(id) ON DELETE CASCADE,
    unit_price     NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    total_cost     NUMERIC(12,2) NOT NULL,
    currency       VARCHAR(3)    NOT NULL DEFAULT 'JOD',
    set_by_user_id BIGINT        NOT NULL REFERENCES tenant_b.users(id),
    updated_at     TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- Seed System Resource Types for tenant_b
INSERT INTO tenant_b.resource_types (name, category, is_custom, created_by_user_id, is_active)
VALUES
    ('20-Seat Bus',       'transport', false, NULL, true),
    ('40-Seat Bus',       'transport', false, NULL, true),
    ('Male Supervisor',   'staffing',  false, NULL, true),
    ('Female Supervisor', 'staffing',  false, NULL, true),
    ('Kids Meal',         'meals',     false, NULL, true),
    ('Adult Meal',        'meals',     false, NULL, true)
ON CONFLICT DO NOTHING;

