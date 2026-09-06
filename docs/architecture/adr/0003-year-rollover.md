# ADR 0003 — Year rollover, adapted to the year-scoped `class` model

**Status:** Accepted (implemented this session — backend only, see "Not built" below)
**Ratification:** Decided by an AI assistant under "make your best call" direction; not yet
human-reviewed. Ratify or amend before building the UI (module roadmap Wave C1).
**Date:** 2026-08-27
**Deciders:** made by the assistant per explicit "make your best call" direction; flagged
for review rather than silently assumed permanent

## Context

A fully-specified year-rollover design was proposed: `year_rollover`/`year_rollover_line`
tables with an idempotency-key + draft→previewed→committing→committed state machine, a
re-entrant 11-step commit transaction, a Postgres trigger syncing `students.class_id`,
`curriculum_subject`/`teaching_assignment` copy steps, and campus-scoped section cloning.

That spec assumed a data model this repo does not have: date-ranged "placements"
(`effective_to`, `exit_reason`) separate from `class`. ADR 0002 (this session, already
shipped as `tenant_0002`) deliberately chose the opposite shape — `class.academic_year_id`,
one row per class per year, immutable once its year closes — specifically to avoid needing
placement date ranges. The two designs solve overlapping problems differently; layering the
incoming spec on top of ADR 0002 without reconciling them would have produced a rollover
that wrote to tables nothing else read.

Four things in the incoming spec were not decided anywhere in this project:
`curriculum_subject`/`teaching_assignment` (curriculum content, forecloses on `CLAUDE.md`'s
explicit "not an LMS" boundary), a database trigger (this codebase has zero triggers; all
logic lives in Python service/repository code — a load-bearing convention, not an oversight),
campus-scoped cloning (`class.campus_id` does not exist; multi-campus is unbuilt), and a
client-supplied idempotency key (a pattern with no precedent anywhere else in this API).

## Decision

Adapt the incoming spec to ADR 0002's already-shipped model rather than introduce a second,
competing data model:

- **No separate placement table.** "Closing" a placement is repointing `students.class_id`
  at the new year's (already-immutable) class row. `year_rollover_line` carries
  `from_class_id`/`to_class_id` (a real `class.id` once resolved) plus `to_level_id`/
  `to_section_label` (the *intended* target before commit creates the row it will
  eventually point at — see Schema below for why these are separate).
- **No curriculum tables, no trigger, no campus dimension.** Omitted outright, not stubbed.
  See "Refused/deferred" below for the reasoning on each.
- **No client-supplied idempotency key.** Idempotency comes from `UNIQUE (from_year_id,
  to_year_id)` on `year_rollover` — a rollover between two specific years is inherently a
  singleton operation; retrying the same request finds the existing row. Re-entrancy on
  `commit` comes from `SELECT ... FOR UPDATE` plus `applied_at IS NULL` gating on each line,
  matching the original spec's mechanism.

### Schema (`tenant_0003`, mirrored in `database.py`)

- `academic_years.is_active` (boolean, from ADR 0002) → `status` (`planned | active |
  closed`), plus `rolled_from_id`. A boolean cannot represent a future year that's been
  *defined* (via "Define next year") but not yet rolled into — it's neither active nor
  closed. This is the third state ADR 0002 would have needed had rollover been designed
  first; adding it now is a small migration, not a redesign.
- `class.section_label`: a rollover-matching key independent of the free-text `name`
  display field, best-effort parsed from the existing "`<Grade> - <Section>`" naming
  convention (`AGENTS.md` §7's locked grade prefix) and kept in sync going forward by
  `TenantRepository.create_class`/`update_class` (see `_derive_section_label`), not just
  backfilled once. A class whose name doesn't parse (or that's `is_active = false`) has no
  `section_label` and cannot be a clone source — its students `hold` rather than being
  silently misassigned.
- `students.status` (`enrolled | graduated | withdrawn`) / `exited_on`: replaces the
  previous overload of `class_id = NULL` meaning both "not yet placed" and "graduated".
- `year_rollover` / `year_rollover_line`: as specified, minus the columns/constraints tied
  to the rejected placement-table and trigger design.

### Plan generation

A class clones forward to the next level (`ordinal + 1`) only if it is `is_active` and has
a parseable `section_label`. Per enrolled student in the source year: no next level →
`graduate` (`no_next_level`); next level exists but the student's class doesn't clone
forward → `hold` (`no_placement`); otherwise → `promote`. `over_capacity` is flagged
(non-blocking) by comparing the projected headcount per `(to_level_id, to_section_label)`
group against the source class's capacity, which is what the new class inherits at commit.
Students with `status <> 'enrolled'` are excluded from plan generation entirely, not held.

`/preview` recomputes `proposed_action`/`exception_code` from scratch on every call, but an
admin's `override_action` **and any target they set while resolving it**
(`to_level_id`/`to_section_label`) are preserved — the upsert only lets the engine overwrite
a line's target when that line has no override yet. This was a real bug caught by testing:
an earlier version of the upsert preserved `override_action` but still let the recompute
silently wipe the admin-chosen target back to `NULL`, which then crashed commit. Preview
refuses to advance to `previewed` while any line is `hold` without a resolved target.

### Commit

One transaction, re-entrant, on the tenant's `class`-scoped model:

1. `SELECT ... FOR UPDATE` the rollover row; a `state = 'committed'` row returns its existing
   summary immediately (idempotent replay); anything other than `previewed` is rejected.
2. Stamp `state = 'committing'` — inside the same transaction, so a crash before `COMMIT`
   rolls this back too, leaving a clean `previewed` state for retry.
3. For each distinct `(to_level_id, to_section_label)` a `promote` line targets, create the
   `to_year` class if it doesn't exist yet (name = `"<target level name> - <section_label>"`,
   capacity/head_teacher cloned from the source class), `ON CONFLICT (name, academic_year_id)
   DO NOTHING` so a retry after a partial failure finds the row instead of erroring.
4. Apply each line's effective action (`override_action` if set, else `proposed_action`):
   `promote` repoints `students.class_id`; `graduate`/`withdraw` sets `class_id = NULL`,
   `status`, `exited_on`; `hold` is left unresolved for a future attempt. Every transition is
   logged to `student_class_history` via the existing `_record_class_change` helper.
5. Mark every applied line's `applied_at`.
6. Flip academic-year `status`: `from_year` → `closed` **before** `to_year` → `active` — the
   `one_active_academic_year` partial unique index rejects the reverse order (both years
   `active` at once, even momentarily, inside the same transaction).
7. Stamp the rollover `committed` with a computed summary.

### Refused / deferred, with reasoning

- **Curriculum content (`curriculum_subject`/`teaching_assignment`):** not built. This is
  curriculum *content*, which `CLAUDE.md` explicitly forecloses ("not an LMS... no
  curriculum content"). The incoming spec marked both copy-steps `[opt]`; the "Define next
  year" screen simply doesn't offer these checkboxes rather than offering a no-op.
- **Database trigger:** not built. `students.class_id` is repointed explicitly by
  `commit_year_rollover` in Python, inside the same transaction. Every other rule in this
  codebase lives in service/repository code specifically so it's auditable in one place and
  testable without a running Postgres trigger harness (which this repo's pytest-integration
  style has no precedent for). If a real need for enforcement-regardless-of-write-path
  emerges later, that's a decision to make explicitly then, not inherit from a spec that
  didn't originate from this codebase's constraints.
- **Campus-scoped cloning:** not built. `class.campus_id` doesn't exist; multi-campus is
  unbuilt scope (an unrelated, previously-identified gap). The clone rule matches on
  `(level, section_label)` only.
- **`unpaid_balance` exception:** not built. This product has trip-ticket payments per
  enrollment, not a per-student running tuition/fee balance — there is nothing in this
  schema an "unpaid balance" could mean without inventing a billing concept the product
  doesn't have. Fabricating a proxy for it (e.g. "any pending trip payment") would silently
  misrepresent what the flag means; omitted rather than guessed at.

## Consequences

**Accepted:** rollover builds directly on the already-shipped, already-tested year-scoping
work instead of a second competing schema. Section identity survives free-text renames via
`section_label`. Admin overrides — including manually-resolved targets — genuinely survive
a preview recompute (verified by a bug this session's own testing caught and fixed).

**Costs:** `year_rollover_line.to_class_id` is not populated until commit (the target class
often doesn't exist until then); code and any future UI must read `to_level_id`/
`to_section_label` as "the intended target" and `to_class_id` as "the resolved target,
once committed" — not interchangeable.

**Risks:** `over_capacity` is computed against the *engine's* base grouping, not
re-validated against admin overrides that move a student into a different, possibly
already-full target — "revalidates capacity against the overrides" from the original spec
is not fully implemented. A future pass should regroup by each line's *effective* target
(after overrides) before flagging capacity, not the engine's raw proposal.

## Invariants this must not break

- No query crosses a tenant boundary — all new tables are tenant-schema-scoped.
- A closed academic year's rosters do not change after closing (enforced by construction:
  nothing writes `class_id` to point at a non-active year's class).
- Every student is in exactly one of: placed in an active-year class, `graduated`,
  `withdrawn`, or unplaced (`class_id = NULL`, `status = 'enrolled'`) — never ambiguous.
- Tenant activation / curriculum-lock irreversibility is unaffected.

## Not built this session

Frontend (the three-screen admin flow), the `GET /setup-state`-style `blocking[]` contract
reuse on a dedicated UI, and the capacity-revalidates-overrides refinement above. Backend
(migration, repository, service, router) is implemented and tested against a live scratch
tenant schema, including the full generate → override → preview → commit → re-entrant-commit
path, plus the full existing pytest suite (179 tests) with no regressions.
