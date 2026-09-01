"""academic year dimension

Introduces `academic_years` as a real, queryable table and makes `class` rows
year-scoped, per ADR 0002 (back/docs/adr/0002-academic-year-as-first-class-dimension.md).
Today `academic_settings.academic_year` is a bare TEXT column on a
singleton-by-convention settings row, referenced by nothing — a class, once
created, is reused forever with no way to say which year its current roster
belongs to. This is Option A from the ADR: a class is now identified by
(name, level_id, academic_year_id), not just (name, level_id).

Run once per tenant schema — see alembic/apply_all_tenants.py, or directly:
    alembic -x target=tenant -x schema=<tenant_id> upgrade tenant@head

Data migration (upgrade path): backfills exactly one `academic_years` row from
whatever `academic_settings.academic_year` currently holds (same
`ORDER BY id DESC LIMIT 1` convention TenantRepository already uses to resolve
"the current settings row" — see PROJECT_UNDERSTANDING.md §14.24 — with the
same "2026-2027" fallback TenantRepository.save_academic_structure uses when no
settings row exists at all), marks it `is_active = TRUE`, and points every
existing `class` row at it. This is safe only because the current schema has
no way to represent more than one logical year yet, so there is nothing today
that could disagree with "every existing class belongs to one single year."

A partial unique index enforces at most one active academic year at a time —
not explicitly spelled out in the implementation plan this migration follows,
added here because "which year is active" being ambiguous would silently break
every default-active-year query this dimension exists to support.

Revision ID: tenant_0002
Revises: tenant_0001
Create Date: 2026-08-27
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "tenant_0002"
down_revision = "tenant_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Table: academic_years
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS academic_years (
            id         BIGSERIAL   PRIMARY KEY,
            name       TEXT        NOT NULL,
            is_active  BOOLEAN     NOT NULL DEFAULT FALSE,
            start_date DATE        DEFAULT NULL,
            end_date   DATE        DEFAULT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_academic_year
            ON academic_years (is_active) WHERE is_active = TRUE;
        """
    )

    # Backfill: exactly one active year, derived from the existing
    # academic_settings row if one exists.
    op.execute(
        """
        INSERT INTO academic_years (name, is_active)
        SELECT
            COALESCE(
                (SELECT academic_year FROM academic_settings ORDER BY id DESC LIMIT 1),
                '2026-2027'
            ),
            TRUE
        WHERE NOT EXISTS (SELECT 1 FROM academic_years);
        """
    )

    # class.academic_year_id — added nullable, backfilled, then locked NOT NULL,
    # since existing `class` rows predate this column and need a value before
    # the constraint can apply.
    op.execute(
        """
        ALTER TABLE class
            ADD COLUMN IF NOT EXISTS academic_year_id BIGINT
                REFERENCES academic_years(id) ON DELETE RESTRICT;
        """
    )
    op.execute(
        """
        UPDATE class
        SET academic_year_id = (SELECT id FROM academic_years WHERE is_active = TRUE LIMIT 1)
        WHERE academic_year_id IS NULL;
        """
    )
    op.execute("ALTER TABLE class ALTER COLUMN academic_year_id SET NOT NULL;")
    op.execute("CREATE INDEX IF NOT EXISTS idx_class_academic_year ON class(academic_year_id);")
    # Alembic runs each op.execute() as its own prepared statement (unlike the
    # raw asyncpg simple-query execute() database.py's mirror of this uses),
    # so a DROP+ADD CONSTRAINT pair must be two calls, not one multi-statement string.
    op.execute("ALTER TABLE class DROP CONSTRAINT IF EXISTS class_name_academic_year_unique;")
    op.execute(
        "ALTER TABLE class ADD CONSTRAINT class_name_academic_year_unique UNIQUE (name, academic_year_id);"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE class DROP CONSTRAINT IF EXISTS class_name_academic_year_unique;")
    op.execute("DROP INDEX IF EXISTS idx_class_academic_year;")
    op.execute("ALTER TABLE class DROP COLUMN IF EXISTS academic_year_id;")
    op.execute("DROP TABLE IF EXISTS academic_years;")
