"""
Alembic environment — schema-per-tenant aware.

This project has no SQLAlchemy ORM layer (all app code talks to Postgres via
raw asyncpg), so migrations here are plain SQL wrapped in op.execute() — there
is no `target_metadata` to autogenerate against. Alembic is used purely as a
version-tracked DDL runner.

Two independent migration branches share this one versions/ directory, because
this app is schema-per-tenant inside ONE physical database (see
back/docs/adr/0001-use-clean-architecture.md), not database-per-tenant:

- "control_plane" — the shared tables in the `public` schema (tenants, parents,
  super_admins, invitations, ...). Tracked by a single `alembic_version` table
  in `public`. Run with:
      alembic upgrade control_plane@head

- "tenant" — the per-school tables (users, students, events, ...), replayed
  once per tenant schema. Each tenant schema gets its OWN `alembic_version`
  table (via version_table_schema below), so tenants can be migrated
  independently and a brand-new tenant starts from an empty history. Run with:
      alembic -x target=tenant -x schema=<tenant_id> upgrade tenant@head
  or, to apply to every existing tenant in one go, use
  alembic/apply_all_tenants.py instead of calling this per schema by hand.

target/schema are resolved from `-x` CLI arguments for manual/CLI use, or from
config.attributes for programmatic use (see apply_all_tenants.py), so both
invocation styles work without duplicating this logic.
"""

import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# back/ on sys.path so `from app.core.config import ...` resolves regardless of
# the working directory Alembic is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import CONTROL_PLANE_DB_NAME, DB_HOST, DB_PASSWORD, DB_PORT, DB_USER  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Raw-SQL migrations only — no ORM models to autogenerate against.
target_metadata = None


def _resolve_target_and_schema() -> tuple[str, str]:
    """Figure out which branch to run and which schema to point it at.

    Programmatic callers (apply_all_tenants.py) set config.attributes before
    invoking alembic.command.upgrade(); the plain CLI sets `-x` args instead.
    Checking attributes first lets both paths share this one function.
    """
    target = config.attributes.get("target")
    schema = config.attributes.get("schema")

    if target is None:
        x_args = context.get_x_argument(as_dictionary=True)
        target = x_args.get("target", "control_plane")
        schema = x_args.get("schema")

    if target not in ("control_plane", "tenant"):
        raise ValueError(f"Unknown migration target '{target}' — expected 'control_plane' or 'tenant'")

    if target == "control_plane":
        schema = "public"
    elif not schema:
        raise ValueError(
            "Tenant-branch migrations need a schema — pass -x schema=<tenant_id> "
            "(or use apply_all_tenants.py, which does this for every tenant)."
        )

    return target, schema


def _build_url() -> str:
    # Schema-per-tenant means every target — control-plane and every tenant
    # alike — lives in this one physical database; only search_path differs.
    return f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{CONTROL_PLANE_DB_NAME}"


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (`alembic upgrade --sql`)."""
    target, schema = _resolve_target_and_schema()
    context.configure(
        url=_build_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        version_table_schema=schema,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection, schema: str) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=schema,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    target, schema = _resolve_target_and_schema()

    connectable = async_engine_from_config(
        {"sqlalchemy.url": _build_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        if target == "tenant":
            # Mirrors _ensure_schema_exists() in app/core/database.py — a
            # brand-new tenant has no schema yet, so the baseline migration
            # needs somewhere to create its tables.
            await connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        await connection.execute(text(f'SET search_path TO "{schema}", public'))
        # SQLAlchemy 2.0 async connections auto-begin a transaction on that
        # first execute() and do NOT auto-commit on a clean exit — commit it
        # explicitly here so Alembic's own begin_transaction() below starts
        # from a clean slate instead of silently inheriting (and potentially
        # never committing) this one.
        await connection.commit()
        await connection.run_sync(_do_run_migrations, schema)
        # Belt-and-suspenders: commit whatever the migration did too, in case
        # anything upstream still leaves the transaction open on exit.
        await connection.commit()

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
