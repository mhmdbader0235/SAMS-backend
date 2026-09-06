"""Regression tests for the fixed reverse-permission-expansion bug.

invariant: a granular permission held by every role (e.g. "school:read") must
never expand back into an admin-only role (e.g. "school_admin"). This was the
exact mechanism by which a student token used to pass 12 academic write
endpoints -- see SAMS_Academic_Model_Next_Steps.md §0.1 (now banner-marked
fixed). app.core.authz.require is forward-expansion only; these tests fail if
that direction is ever reintroduced.

Was TenantService._has_intersection -- moved to app.core.authz (module
roadmap Wave A2) as a pure location change, logic unchanged. This file's
assertions did not need to change, only the import.
"""

from app.core import authz


def test_school_read_does_not_expand_to_school_admin():
    assert authz.require(["student"], {"school_admin"}) is False


def test_teacher_still_grants_class_read():
    assert authz.require(["teacher"], {"class:read"}) is True


def test_multiple_granular_perms_do_not_expand_to_admin():
    assert authz.require(["school:read", "event:read"], {"school_admin"}) is False
