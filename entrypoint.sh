#!/bin/sh
set -e

# This repo's Alembic history is NOT one linear chain -- see alembic/env.py's
# module docstring. There are two independent branches, "control_plane" and
# "tenant", each with its own baseline revision and no common ancestor. A bare
# `alembic upgrade head` fails with "Multiple head revisions are present"
# instead of migrating anything, so each branch is run the way the repo's own
# docs say to run it.
alembic upgrade control_plane@head

# Tenant-branch migrations replay once per EXISTING tenant schema -- there is
# no single "upgrade every tenant" target for plain `alembic upgrade` to hit
# (see alembic/apply_all_tenants.py's docstring). Zero rows in the
# control-plane `tenants` table (a brand-new platform) makes this a no-op.
#
# Run by PATH, not `python -m alembic.apply_all_tenants` -- verified against
# a real venv that the -m form raises "No module named 'alembic.apply_all_tenants'"
# because the installed `alembic` pip package always wins name resolution
# over this same-named local directory. See apply_all_tenants.py's docstring,
# fixed alongside this entrypoint.
python alembic/apply_all_tenants.py

# NOTE: this assumes exactly one backend container. Running migrations from
# every container's entrypoint is only safe as long as that holds -- if this
# service is ever scaled to multiple replicas, move both commands above into
# a separate one-off "migrate" step that runs once before the replicas start,
# so concurrent `alembic upgrade` calls don't race on the same
# alembic_version row(s).
exec uvicorn app.main:app --host 0.0.0.0 --port 8001
