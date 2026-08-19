"""
Unit tests for the School Setup (Day-1 onboarding) domain — pure logic only.

Anything that needs real data (Stage-1 commit validation, curriculum-lock
rejection, has_structure no longer true on calendar-only, legacy grandfathering,
require_tenant_live gating) lives in tests/integration/test_school_routes.py
since it exercises real repository/DB behavior — see that file for those cases.
"""

import pytest

from app.domains.school.service import SchoolService


class TestSchoolServiceAdminGuard:
    def test_school_admin_role_is_allowed(self):
        # Should not raise
        SchoolService._require_admin("school_admin")

    def test_super_admin_role_is_allowed(self):
        SchoolService._require_admin(["super_admin"])

    def test_admin_alias_role_is_allowed(self):
        SchoolService._require_admin(["admin"])

    def test_composite_roles_with_admin_allowed(self):
        SchoolService._require_admin(["teacher", "school_admin"])

    def test_teacher_role_is_rejected(self):
        with pytest.raises(PermissionError):
            SchoolService._require_admin("teacher")

    def test_parent_role_is_rejected(self):
        with pytest.raises(PermissionError):
            SchoolService._require_admin(["parent"])

    def test_empty_roles_is_rejected(self):
        with pytest.raises(PermissionError):
            SchoolService._require_admin([])

    def test_none_role_is_rejected(self):
        with pytest.raises(PermissionError):
            SchoolService._require_admin(None)
