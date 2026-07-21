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
    created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
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


-- =============================================================================
-- 2. TARGET TENANT SCHEMA (12 Tables)
-- =============================================================================

-- Table 1: users
CREATE TABLE IF NOT EXISTS users (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email         CITEXT      UNIQUE NOT NULL,
    role          TEXT        NOT NULL CHECK (role IN ('school_admin', 'teacher', 'student')),
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- Table 2: grade_levels
CREATE TABLE IF NOT EXISTS grade_levels (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT        NOT NULL,
    academic_year TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (name, academic_year)
);

-- Table 3: students
CREATE TABLE IF NOT EXISTS students (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID        UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    grade_level_id UUID        NOT NULL REFERENCES grade_levels(id) ON DELETE RESTRICT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_students_user_id ON students(user_id);
CREATE INDEX IF NOT EXISTS idx_students_grade_level ON students(grade_level_id);

-- Table 4: classes
CREATE TABLE IF NOT EXISTS classes (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    subject        TEXT        NOT NULL,
    teacher_id     UUID        NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    grade_level_id UUID        NOT NULL REFERENCES grade_levels(id) ON DELETE RESTRICT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_classes_teacher ON classes(teacher_id);
CREATE INDEX IF NOT EXISTS idx_classes_grade ON classes(grade_level_id);

-- Table 5: enrollments
CREATE TABLE IF NOT EXISTS enrollments (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID        NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    class_id   UUID        NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (student_id, class_id)
);

CREATE INDEX IF NOT EXISTS idx_enrollments_student ON enrollments(student_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_class ON enrollments(class_id);

-- Table 6: attendance
CREATE TABLE IF NOT EXISTS attendance (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    enrollment_id UUID        NOT NULL REFERENCES enrollments(id) ON DELETE CASCADE,
    date          DATE        NOT NULL,
    status        TEXT        NOT NULL CHECK (status IN ('present', 'absent', 'tardy')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (enrollment_id, date)
);

CREATE INDEX IF NOT EXISTS idx_attendance_enrollment ON attendance(enrollment_id);
CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(date);

-- Table 7: events
CREATE TABLE IF NOT EXISTS events (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title       TEXT        NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    event_type  TEXT        NOT NULL,
    created_by  UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    start_at    TIMESTAMPTZ NOT NULL,
    end_at      TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_events_creator ON events(created_by);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_at);

-- Table 8: event_grade_level_targets
CREATE TABLE IF NOT EXISTS event_grade_level_targets (
    event_id       UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    grade_level_id UUID NOT NULL REFERENCES grade_levels(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, grade_level_id)
);

-- Table 9: event_class_targets
CREATE TABLE IF NOT EXISTS event_class_targets (
    event_id UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, class_id)
);

-- Table 10: event_student_targets
CREATE TABLE IF NOT EXISTS event_student_targets (
    event_id   UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, student_id)
);

-- Table 11: notifications
CREATE TABLE IF NOT EXISTS notifications (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id          UUID        NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    recipient_user_id UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delivered_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at           TIMESTAMPTZ DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications(recipient_user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read_at);

-- Table 12: student_health_and_records (PII table)
CREATE TABLE IF NOT EXISTS student_health_and_records (
    id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id                    UUID UNIQUE NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    national_id_encrypted         TEXT NOT NULL,
    medical_conditions_encrypted  TEXT NOT NULL,
    emergency_contact_encrypted   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_student_health_student ON student_health_and_records(student_id);
