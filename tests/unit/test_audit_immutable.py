"""Regression test for the audit log's immutability guarantee.

invariant (see ADR 0006 / app/domains/audit/repository.py's module docstring):
no repository method anywhere in this codebase issues UPDATE or DELETE
against `audit_log`. This is the immutability guarantee at the application
layer today -- a real `REVOKE UPDATE, DELETE` needs a non-superuser app
database role, which this stack does not have yet. Until that lands, this
static check is what stands between "immutable by convention" and "immutable
by accident of nobody having written that code path yet".
"""

import re
from pathlib import Path

BACK_DIR = Path(__file__).resolve().parent.parent.parent

_MUTATION_PATTERN = re.compile(r"\b(UPDATE|DELETE\s+FROM)\s+audit_log\b", re.IGNORECASE)


def test_no_repository_method_mutates_audit_log():
    offenders = []
    for py_file in (BACK_DIR / "app").rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if _MUTATION_PATTERN.search(text):
            offenders.append(str(py_file.relative_to(BACK_DIR)))
    assert not offenders, f"found UPDATE/DELETE against audit_log in: {offenders}"


def test_audit_router_exposes_no_mutation_endpoint():
    router_source = (BACK_DIR / "app/domains/audit/router.py").read_text(encoding="utf-8")
    for verb in ("@router.put", "@router.patch", "@router.delete", "@router.post"):
        assert verb not in router_source, f"audit router unexpectedly defines a {verb} endpoint"
