-- =============================================================================
-- SchoolDesk — PostgreSQL Target Schema Initialization Script
-- Runs automatically when a new Postgres container starts (Docker init.d)
-- Also used as the reference DDL for manual DB setup.
-- =============================================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS citext;     -- Case-insensitive text (email)
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

-- =============================================================================
-- 1. CONTROL-PLANE DATABASE SCHEMA (Global Metadata)
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

-- Parent-Child Cross-DB Link Table
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


-- =============================================================================
-- 2. TARGET TENANT SCHEMA (14 Tables)
-- =============================================================================

-- Table 1: users
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL   PRIMARY KEY,
    email         CITEXT      UNIQUE NOT NULL,
    role          TEXT        NOT NULL CHECK (role IN ('school_admin', 'teacher', 'parent', 'student', 'manager', 'finance')),
    password_hash TEXT        NOT NULL,
    phone         VARCHAR(50) DEFAULT NULL,
    address       TEXT        DEFAULT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- Table 2: levels
CREATE TABLE IF NOT EXISTS levels (
    level_id   BIGSERIAL   PRIMARY KEY,
    name       TEXT        NOT NULL
);

-- Table 3: teachers
CREATE TABLE IF NOT EXISTS teachers (
    id   BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    name TEXT   NOT NULL
);

-- Table 4: parenets
CREATE TABLE IF NOT EXISTS parenets (
    id    BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    name  TEXT   NOT NULL,
    phone TEXT   DEFAULT NULL
);

-- Table 5: class
CREATE TABLE IF NOT EXISTS class (
    id              BIGSERIAL   PRIMARY KEY,
    name            TEXT        NOT NULL,
    level_id        BIGINT      NOT NULL REFERENCES levels(level_id) ON DELETE RESTRICT,
    head_teacher_id BIGINT      NOT NULL REFERENCES teachers(id) ON DELETE RESTRICT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_class_level ON class(level_id);
CREATE INDEX IF NOT EXISTS idx_class_teacher ON class(head_teacher_id);

-- Table 6: students
CREATE TABLE IF NOT EXISTS students (
    id         BIGINT      PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT        NOT NULL,
    class_id   BIGINT      NOT NULL REFERENCES class(id) ON DELETE RESTRICT,
    gender     TEXT        DEFAULT NULL,
    birth_data TEXT        DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_students_class ON students(class_id);

-- Table 7: student_parent_map
CREATE TABLE IF NOT EXISTS student_parent_map (
    student_id BIGINT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    parent_id  BIGINT NOT NULL REFERENCES parenets(id) ON DELETE CASCADE,
    PRIMARY KEY (student_id, parent_id)
);

-- Table 8: cost_budget
CREATE TABLE IF NOT EXISTS cost_budget (
    id          BIGSERIAL      PRIMARY KEY,
    budget_id   BIGINT         DEFAULT NULL,
    description TEXT           NOT NULL,
    price       NUMERIC(10, 2) NOT NULL DEFAULT 0.00
);

-- Enum Type event_status
DO $$ BEGIN
    CREATE TYPE event_status AS ENUM ('draft', 'proposed', 'finance_approval', 'final_review', 'published');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Table 9: event
CREATE TABLE IF NOT EXISTS event (
    id             BIGSERIAL      PRIMARY KEY,
    title          TEXT           NOT NULL,
    description    TEXT           NOT NULL DEFAULT '',
    address        TEXT           DEFAULT NULL,
    event_map_id   BIGINT         DEFAULT NULL,
    school_subsidy NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
    date           TIMESTAMPTZ    NOT NULL,
    created_by     BIGINT         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at     TIMESTAMPTZ    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status         event_status   NOT NULL DEFAULT 'draft',
    predicted_attendance INTEGER  NULL,
    manager_reviewer_id BIGINT    NULL REFERENCES users(id),
    finance_reviewer_id BIGINT    NULL REFERENCES users(id),
    total_cost     NUMERIC(12,2)  NULL,
    submitted_at   TIMESTAMPTZ    NULL,
    manager_approved_at TIMESTAMPTZ NULL,
    finance_priced_at TIMESTAMPTZ NULL,
    published_at   TIMESTAMPTZ    NULL
);

CREATE INDEX IF NOT EXISTS idx_event_creator ON event(created_by);
CREATE INDEX IF NOT EXISTS idx_event_date ON event(date);
CREATE INDEX IF NOT EXISTS ix_events_status ON event(status);

-- Table 10: event_class_map
CREATE TABLE IF NOT EXISTS event_class_map (
    id            BIGSERIAL      PRIMARY KEY,
    event_id      BIGINT         NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    class_id      BIGINT         NOT NULL REFERENCES class(id) ON DELETE CASCADE,
    ticket_price  NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
    costbudget_id BIGINT         DEFAULT NULL REFERENCES cost_budget(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_ecm_event ON event_class_map(event_id);
CREATE INDEX IF NOT EXISTS idx_ecm_class ON event_class_map(class_id);

-- Table 11: enrollment
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

CREATE INDEX IF NOT EXISTS idx_enrollment_student ON enrollment(student_id);
CREATE INDEX IF NOT EXISTS idx_enrollment_ecm ON enrollment(event_class_map_id);

-- Table 12: payments
CREATE TABLE IF NOT EXISTS payments (
    id            BIGSERIAL      PRIMARY KEY,
    enrollment_id BIGINT         NOT NULL REFERENCES enrollment(id) ON DELETE CASCADE,
    amount        NUMERIC(10, 2) NOT NULL,
    status        TEXT           NOT NULL CHECK (status IN ('pending', 'paid', 'refunded')),
    created_at    TIMESTAMPTZ    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Table 13: event_feedback
CREATE TABLE IF NOT EXISTS event_feedback (
    id         BIGSERIAL   PRIMARY KEY,
    event_id   BIGINT      NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    user_id    BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating     INT         NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comments   TEXT        DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Table 14: student_health_and_records (PII table)
CREATE TABLE IF NOT EXISTS student_health_and_records (
    id                            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id                    BIGINT      UNIQUE NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    national_id_encrypted         TEXT        NOT NULL,
    medical_conditions_encrypted  TEXT        NOT NULL,
    emergency_contact_encrypted   TEXT        NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_student_health_student ON student_health_and_records(student_id);

-- Table 15: notifications
CREATE TABLE IF NOT EXISTS notifications (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id          BIGINT      NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    recipient_user_id BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delivered_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at           TIMESTAMPTZ DEFAULT NULL,
    title_override    VARCHAR(255) DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications(recipient_user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read_at);


-- Table 16: resource_types
CREATE TABLE IF NOT EXISTS resource_types (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(120) NOT NULL,
    category        VARCHAR(30)  NOT NULL DEFAULT 'other',
    is_custom       BOOLEAN      NOT NULL DEFAULT false,
    created_by_user_id BIGINT    NULL REFERENCES users(id) ON DELETE SET NULL,
    is_active       BOOLEAN      NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Table 17: resources
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

-- Table 18: resource_cost
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

-- Seed System Resource Types
INSERT INTO resource_types (name, category, is_custom, created_by_user_id, is_active)
VALUES
('20-Seat Bus', 'transport', false, NULL, true),
('40-Seat Bus', 'transport', false, NULL, true),
('Male Supervisor', 'staffing', false, NULL, true),
('Female Supervisor', 'staffing', false, NULL, true),
('Kids Meal', 'meals', false, NULL, true),
('Adult Meal', 'meals', false, NULL, true)
ON CONFLICT DO NOTHING;


