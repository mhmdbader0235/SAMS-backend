# ADR 0002 — Introduce `academic_year` as a first-class, queryable dimension

**Status:** Proposed
**Date:** 2026-08-27
**Deciders:** architecture team (Option A vs. B below is an open decision, not settled by this ADR)

## Context

`academic_settings.academic_year` is a bare `TEXT NOT NULL` on a singleton-by-convention
settings row (`alembic/versions/tenant_0001_tenant_schema_baseline.py:85-94`,
`app/core/database.py:254-262`). No other table references it. `class`, `students`,
`blackout_dates`, `enrollment`, `event`, and `event_class_map` carry no year column in either
schema-creation path. Consequences, verified against the running code:

- **Live-drift headcount bug.** `TenantService.get_predicted_attendance` →
  `get_student_count_for_classes` (`tenant_repository.py:1802-1810`) runs
  `SELECT COUNT(*) FROM students WHERE class_id = ANY($1)` at call time. A **published** event's
  reported audience silently changes if any mapped class's roster changes afterward — there is no
  point at which the number is frozen.
- **No roster-as-of-a-date query exists.** `student_class_history` (added per §14.23 of
  `PROJECT_UNDERSTANDING.md`) does log every `class_id` transition with `changed_at`, so the raw
  data to reconstruct a past roster exists — but no endpoint does this reconstruction, and there
  is no `academic_year` value on either the class or the history row to scope the answer to "the
  2024-2025 school year" as opposed to an arbitrary date.
- **No promotion/rollover flow.** Confirmed by exhaustive grep: zero hits for `school_year`,
  `term`, `session`, `rollover`, `graduate`/`graduat`, `alumni` anywhere in `back/app/` or
  `front/src/` (the only `GraduationCap` hits are a Lucide icon name, semantically inert).
  `bulk_reassign_students` is a generic reassignment tool, not a year-turnover operation.
- **Overloaded `NULL`.** `students.class_id = NULL` means "not yet placed" (new student, JIT
  provisioning) with no distinct state for "graduated" — Grade 12 (max ordinal) has no exit that
  isn't indistinguishable from a brand-new unplaced student.

**Correction to the originating report:** `bulk_reassign_students` is not an unlogged destructive
`UPDATE`. It already runs inside a transaction with `SELECT ... FOR UPDATE` and writes one
`student_class_history` row per actually-changed student (`tenant_repository.py:1017-1049`) — this
was a real defect, already fixed and documented in `PROJECT_UNDERSTANDING.md` §14.23. What remains
true, and is the actual subject of this ADR, is that **no year axis exists at all** — history
without a year dimension answers "when did this change" but not "what did the roster look like
*for the 2024-2025 year*", and does nothing about the live-drift headcount bug or the missing
graduation state.

**New drift found during verification, tracked separately in `PROJECT_UNDERSTANDING.md` §14.28:**
`student_class_history` itself is created in `app/core/database.py` / `init.sql` but is **absent
from the Alembic baseline** (`tenant_0001_tenant_schema_baseline.py`), even though that file's own
docstring claims to mirror `database.py`. Any tenant provisioned through the Alembic path alone
would silently lack the §14.23 history feature. This should be fixed as a prerequisite, independent
of the decision below.

**Binding constraint:** schema-per-tenant (ADR 0001, amended). Any DDL change must be expressed as
an Alembic revision applied to *every* existing tenant schema via `apply_all_tenants.py`, **and**
mirrored in `app/core/database.py::_initialize_tenant_tables()` so freshly-provisioned tenants get
the same shape — the two paths already disagree once (see above); this change must not add a third
disagreement.

## Decision

Make `academic_year` a real table, and make `class` rows **year-scoped**: a class is now
`(name, level_id, academic_year_id)`, not just `(name, level_id)`. "Grade 7A for 2025-2026" and
"Grade 7A for 2026-2027" become two distinct `class` rows, linked only by name and level, not by
identity.

```sql
CREATE TABLE academic_year (
    id         BIGSERIAL PRIMARY KEY,
    label      TEXT NOT NULL UNIQUE,             -- "2025-2026"
    start_date DATE NOT NULL,
    end_date   DATE NOT NULL,
    status     TEXT NOT NULL DEFAULT 'planned'
               CHECK (status IN ('planned', 'active', 'closed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- exactly one 'active' row at a time:
CREATE UNIQUE INDEX one_active_academic_year ON academic_year (status) WHERE status = 'active';

ALTER TABLE class ADD COLUMN academic_year_id BIGINT NOT NULL REFERENCES academic_year(id);

ALTER TABLE students ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'graduated', 'withdrawn'));
```

`class_id = NULL` on `students` keeps meaning "not currently placed"; `status = 'graduated'`
becomes the explicit exit state instead of overloading `NULL`.

**Why year-scoped classes instead of a date-ranged enrollment join table** (the direction
`PROJECT_UNDERSTANDING.md` §14.23 flagged as the likely next step for the *separate* concurrent-
placement problem) — this is the disagreement this ADR is naming rather than silently resolving:

- `students.class_id` is read as a plain column, not through a join, in every hot path that
  matters here: `get_student_count_for_classes`, notification fan-out on publish, dashboard
  queries, the roster modal. A join-table redesign (`student_class_enrollment` with
  `valid_from`/`valid_to`) would need to rewrite every one of those call sites to resolve
  "current enrollment as of a date" before it could even be used for the year problem — a much
  larger blast radius across the five-places rule than this ADR's scope.
  Year-scoping `class` itself leaves `students.class_id` exactly as it is read today; only the
  class row it points to now carries a year.
- Freezing past rosters falls out for free: once `academic_year.status = 'closed'`, nobody
  reassigns a student into that year's class rows anymore (enforced in the service layer, see
  Invariants), so `event_class_map` → `class_id` → `students.class_id` for a past, closed year is
  naturally immutable. This directly fixes the live-drift headcount bug without a separate
  snapshot/freeze mechanism.
- Cost: `head_teacher_id`, `capacity`, and any other per-class setting must be copied forward (or
  re-entered) at rollover time, since last year's "7A" and this year's "7A" are different rows.
  This is judged cheaper than rewriting every `class_id` read site — see Alternatives.

## Consequences

**Accepted:**
- "Roster of 7A for 2025-2026" becomes a plain `WHERE class_id = X`, no date arithmetic.
- Past published events stop drifting — closing a year makes its classes' rosters immutable by
  construction.
- Graduation becomes a real, queryable state instead of an overloaded `NULL`.

**Costs:**
- Every place that currently treats a class as a perennial identity (head teacher, capacity,
  any saved reference to "the 7A class") needs a rollover-time copy-forward step. The Curriculum
  Ladder Wizard's staged-commit UI is the closest existing precedent for how to surface this.
- `front/src/store.js` and every view that lists classes must filter to the active academic year
  by default (a `class` picker with no year filter would show every historical section forever).
  This is a five-places change, not a backend-only one.
- Reports that want to compare *across* years ("attendance trend by grade over 3 years") now join
  across `academic_year_id` explicitly — this is the intended trade-off (year-scoping makes
  within-year queries trivial at the cost of across-year queries needing an explicit join), not a
  side effect to work around.

**Risks:**
- Backfill correctness on existing tenants: the single current `academic_settings.academic_year`
  string becomes the first `academic_year` row (`status = 'active'`), and every existing `class`
  row gets `academic_year_id` set to it — this is safe only because there is currently no way for
  a tenant to have accumulated multiple "logical years" of data under one `class` row. **Not
  verified against real tenant data** — see below.
- Rollover is an irreversible-adjacent operation (moving every student, closing a year) in the
  same family as tenant activation and curriculum locking. It needs the same "are we sure" posture
  CLAUDE.md §7 asks for, not a bare bulk-UPDATE endpoint.

## Alternatives considered

**B. Perennial `class` + `student_class_enrollment(student_id, class_id, academic_year_id, valid_from, valid_to)` join table.**
This is what §14.23 pointed toward for the *concurrent-placement* problem (a student in a homeroom
and an elective simultaneously), which this ADR does not attempt to solve. Pro: doesn't force
duplicating class rows every year. Con: `students.class_id` is read as a denormalized column
everywhere today (see Decision) — adopting B for the year problem means either keeping `class_id`
as a "current enrollment cache" in sync with the join table (two sources of truth) or rewriting
every read site to join through "enrollment as of today / as of the event's year" now, not later.
Rejected for this ADR's scope; may still be the right answer if/when concurrent placement is
tackled, at which point it would likely subsume this ADR's `academic_year_id` onto the join table
instead of onto `class`.

**C. Add only `academic_year` on `student_class_history`, leave `class` perennial.**
Lets past-roster reconstruction work (replay history up to a cutoff date, filtered by year) without
touching `class` or `students` at all. Rejected as the primary fix: it does nothing for the
live-drift headcount bug (`event_class_map` still points at a perennial class whose *current*
roster is what gets counted), and does nothing for promotion/graduation. Worth doing as a small
addition regardless, since it makes the history log itself queryable by year rather than only by
date.

## Invariants this must not break

- No query, join, or repository method spans tenant schemas (ADR 0001) — `academic_year` and its
  FK are tenant-scoped, consistent with every existing table.
- A closed academic year's class rosters do not change after closing — enforced at the service
  layer by rejecting any reassignment that targets a `class` row whose `academic_year_id` is not
  the current active year.
- Every student is in exactly one of: placed in an active-year class, `status = 'graduated'`, or
  unplaced (`class_id = NULL`, `status = 'active'`) — never ambiguous between the last two.
- Tenant activation and curriculum-lock irreversibility (CLAUDE.md §"School onboarding") are
  unaffected — academic-year rollover is a distinct, later-life operation with its own guard.

## Open questions for the deciders

- Confirm Option A over Option B (this ADR assumes A; B remains available if concurrent placement
  is tackled first and subsumes this problem).
- Should rollover auto-copy `head_teacher_id`/`capacity` from the prior year's matching class, or
  require the admin to re-enter them per new class row?
- Should rollover be a guided, staged flow (mirroring the Curriculum Ladder Wizard's live-preview
  pattern) or a single confirm-and-run endpoint?
- Should the existing `academic_settings.academic_year` string be trusted verbatim as the backfilled
  active year's label, or should every tenant admin be asked to confirm start/end dates during the
  migration window?

## Not verified

- Whether `front/src/store.js`'s capability map or any Vue view assumes `class_id`/class identity
  is perennial beyond what was checked in the backend survey — the frontend was not read for this
  ADR.
- Whether `school_policy.rego` references class or student rows in a way this change would need to
  update.
- Real tenant data shape — whether backfilling every current `class` row into a single "active"
  academic year is actually safe for every existing tenant, as opposed to only being safe in
  principle given the current schema's inability to represent more than one year already.
