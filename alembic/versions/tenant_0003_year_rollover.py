"""year rollover

Introduces the year-rollover workflow per ADR 0003 (back/docs/adr/0003-year-rollover.md),
which adapts the incoming rollover spec to the year-scoped `class` model ADR 0002
already shipped (tenant_0002) rather than introducing a separate date-ranged
placement table.

Schema changes:
- `academic_years.is_active` (boolean) -> `status` (planned|active|closed): a
  future year defined via "Define next year" but not yet rolled into is neither
  active nor closed, which a boolean cannot represent. `rolled_from_id` added.
- `class.section_label`: a rollover-matching key independent of the free-text
  display name (best-effort backfilled from the existing "<Grade> - <Section>"
  naming convention; NULL where it can't be parsed, no admin action forced).
- `students.status` / `exited_on`: replaces the previous overload of
  `class_id = NULL` meaning both "not yet placed" and "graduated".
- `year_rollover` / `year_rollover_line`: the rollover plan/commit tables.

Deliberately NOT included (see ADR 0003 for why): curriculum_subject /
teaching_assignment copy steps (curriculum content is out of this product's
scope), a database trigger to sync `students.class_id` (this codebase has no
triggers anywhere; the commit endpoint does this update explicitly), and
campus-scoped section cloning (multi-campus is unbuilt).

Run once per tenant schema — see alembic/apply_all_tenants.py, or directly:
    alembic -x target=tenant -x schema=<tenant_id> upgrade tenant@head

Revision ID: tenant_0003
Revises: tenant_0002
Create Date: 2026-08-27
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "tenant_0003"
down_revision = "tenant_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # academic_years: is_active -> status, plus rolled_from_id
    op.execute("ALTER TABLE academic_years ADD COLUMN IF NOT EXISTS status TEXT;")
    op.execute(
        "UPDATE academic_years SET status = CASE WHEN is_active THEN 'active' ELSE 'closed' END "
        "WHERE status IS NULL;"
    )
    op.execute("ALTER TABLE academic_years ALTER COLUMN status SET NOT NULL;")
    op.execute("ALTER TABLE academic_years DROP CONSTRAINT IF EXISTS academic_years_status_check;")
    op.execute(
        "ALTER TABLE academic_years ADD CONSTRAINT academic_years_status_check "
        "CHECK (status IN ('planned', 'active', 'closed'));"
    )
    op.execute("DROP INDEX IF EXISTS one_active_academic_year;")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS one_active_academic_year "
        "ON academic_years (status) WHERE status = 'active';"
    )
    op.execute("ALTER TABLE academic_years DROP COLUMN IF EXISTS is_active;")
    op.execute(
        "ALTER TABLE academic_years ADD COLUMN IF NOT EXISTS rolled_from_id BIGINT "
        "REFERENCES academic_years(id) ON DELETE SET NULL;"
    )

    # class: section_label, best-effort backfilled from either of this
    # codebase's two live naming conventions: "<Grade> - <Section>" (the
    # Curriculum Ladder Wizard / StructureClassesView) or "<Grade> Section
    # <Section>" (seed_data.py) -- confirmed as a real divergence by testing
    # against the actually-running app's seeded tenant_a data.
    op.execute("ALTER TABLE class ADD COLUMN IF NOT EXISTS section_label TEXT;")
    op.execute(
        r"""
        UPDATE class SET section_label = TRIM(SUBSTRING(name FROM '(?i)(?:-|Section)\s*(\S+)\s*$'))
        WHERE section_label IS NULL AND name ~ '(?i)(?:-|Section)\s*\S+\s*$';
        """
    )

    # students: lifecycle status
    op.execute(
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'enrolled';"
    )
    op.execute("ALTER TABLE students DROP CONSTRAINT IF EXISTS students_status_check;")
    op.execute(
        "ALTER TABLE students ADD CONSTRAINT students_status_check "
        "CHECK (status IN ('enrolled', 'graduated', 'withdrawn'));"
    )
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS exited_on DATE;")

    # year_rollover
    op.execute(
        """
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
        """
    )

    # year_rollover_line
    op.execute(
        """
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
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_yrl_rollover ON year_rollover_line(rollover_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS year_rollover_line;")
    op.execute("DROP TABLE IF EXISTS year_rollover;")
    op.execute("ALTER TABLE students DROP COLUMN IF EXISTS exited_on;")
    op.execute("ALTER TABLE students DROP CONSTRAINT IF EXISTS students_status_check;")
    op.execute("ALTER TABLE students DROP COLUMN IF EXISTS status;")
    op.execute("ALTER TABLE class DROP COLUMN IF EXISTS section_label;")
    op.execute("ALTER TABLE academic_years DROP COLUMN IF EXISTS rolled_from_id;")
    op.execute(
        "ALTER TABLE academic_years ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT FALSE;"
    )
    op.execute("UPDATE academic_years SET is_active = (status = 'active');")
    op.execute("DROP INDEX IF EXISTS one_active_academic_year;")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS one_active_academic_year "
        "ON academic_years (is_active) WHERE is_active = TRUE;"
    )
    op.execute("ALTER TABLE academic_years DROP CONSTRAINT IF EXISTS academic_years_status_check;")
    op.execute("ALTER TABLE academic_years DROP COLUMN IF EXISTS status;")
