"""ddl parity — close the runtime/alembic divergence found by test_ddl_parity

Closes three gaps `tests/integration/test_ddl_parity.py` found between the
runtime table-creation path (`app/core/database.py::_initialize_tenant_tables`)
and the Alembic `tenant` branch. All three already exist in the runtime path
for every tenant that has ever gone through it (new tenant creation, or an
existing tenant re-hitting the drift-repair ALTERs); a tenant provisioned
through Alembic alone would silently lack them — the exact class of bug ADR
0002 already flagged once for `student_class_history` and warned against
reintroducing.

Schema changes (all idempotent, additive only):
- `class.is_active` (boolean, default TRUE) — retirement/archival flag.
- `student_class_history` — append-only placement-change log (who/when/from/to).
- `student_parent_map.relationship_type` / `.is_primary_contact` /
  `.can_approve` / `.created_at` — guardian-relationship metadata.
- `class.capacity` — SET NOT NULL, matching `database.py`'s ALTER (added after
  `tenant_0001`'s baseline was written; that baseline's own comment saying
  "nullable ... resolved in favor of database.py" is now itself stale, since
  database.py has since added the NOT NULL. Backfills any NULL to 25 first,
  same as the runtime path, before constraining.

Run once per tenant schema — see alembic/apply_all_tenants.py, or directly:
    alembic -x target=tenant -x schema=<tenant_id> upgrade tenant@head

Revision ID: tenant_0004
Revises: tenant_0003
Create Date: 2026-09-06
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "tenant_0004"
down_revision = "tenant_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE class ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;"
    )

    op.execute(
        "ALTER TABLE student_parent_map ADD COLUMN IF NOT EXISTS relationship_type TEXT DEFAULT NULL;"
    )
    op.execute(
        "ALTER TABLE student_parent_map ADD COLUMN IF NOT EXISTS is_primary_contact BOOLEAN NOT NULL DEFAULT FALSE;"
    )
    op.execute(
        "ALTER TABLE student_parent_map ADD COLUMN IF NOT EXISTS can_approve BOOLEAN NOT NULL DEFAULT TRUE;"
    )
    op.execute(
        "ALTER TABLE student_parent_map ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS student_class_history (
            id           BIGSERIAL   PRIMARY KEY,
            student_id   BIGINT      NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            old_class_id BIGINT      REFERENCES class(id) ON DELETE SET NULL,
            new_class_id BIGINT      REFERENCES class(id) ON DELETE SET NULL,
            changed_by   BIGINT      REFERENCES users(id) ON DELETE SET NULL,
            changed_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sch_student ON student_class_history(student_id, changed_at);"
    )

    op.execute("UPDATE class SET capacity = 25 WHERE capacity IS NULL OR capacity <= 0;")
    op.execute("ALTER TABLE class ALTER COLUMN capacity SET NOT NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE class ALTER COLUMN capacity DROP NOT NULL;")
    op.execute("DROP INDEX IF EXISTS idx_sch_student;")
    op.execute("DROP TABLE IF EXISTS student_class_history;")
    op.execute("ALTER TABLE student_parent_map DROP COLUMN IF EXISTS relationship_type;")
    op.execute("ALTER TABLE student_parent_map DROP COLUMN IF EXISTS is_primary_contact;")
    op.execute("ALTER TABLE student_parent_map DROP COLUMN IF EXISTS can_approve;")
    op.execute("ALTER TABLE student_parent_map DROP COLUMN IF EXISTS created_at;")
    op.execute("ALTER TABLE class DROP COLUMN IF EXISTS is_active;")
