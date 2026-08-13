"""
Unit tests for the dynamic Keycloak RBAC group-to-role mappings.

Tests the mapping of granular permissions to high-level system capabilities
on both CurrentUser and TenantService authorization modules in isolation.
"""

from app.core.dependencies import CurrentUser
from app.domains.tenant.service import TenantService


class TestRoleMapping:
    def test_current_user_direct_role(self):
        user = CurrentUser(
            user_id="user_123",
            tenant_id="tenant_a",
            role="teacher",
            roles=["teacher"]
        )
        assert user.has_role("teacher") is True
        assert user.has_role("school_admin") is False
        assert user.has_any_role("teacher", "school_admin") is True
        
    def test_current_user_super_admin_bypass(self):
        user1 = CurrentUser(
            user_id="user_123",
            tenant_id="tenant_a",
            role="super_admin",
            roles=["super_admin"]
        )
        assert user1.has_role("teacher") is True
        assert user1.has_role("school_admin") is True

    def test_current_user_multiple_roles(self):
        user = CurrentUser(
            user_id="user_123",
            tenant_id="tenant_a",
            role="teacher",
            roles=["teacher", "event_teacher"]
        )
        assert user.has_role("teacher") is True
        assert user.has_role("event_teacher") is True
        assert user.has_role("parent") is False
        assert user.has_role("school_admin") is False

    def test_has_intersection_capabilities(self):
        assert TenantService._has_intersection(
            user_role=["school_admin"],
            allowed_roles={"school_admin", "super_admin"}
        ) is True

        assert TenantService._has_intersection(
            user_role=["teacher"],
            allowed_roles={"school_admin"}
        ) is False

        assert TenantService._has_intersection(
            user_role=["super_admin"],
            allowed_roles={"school_admin"}
        ) is True

    def test_school_admin_event_delete_permission(self):
        admin_user = CurrentUser(
            user_id="admin_123",
            tenant_id="tenant_a",
            role="school_admin",
            roles=["school_admin"]
        )
        assert admin_user.has_role("school_admin") is True
        assert admin_user.has_any_role("school_admin", "super_admin", "event_teacher", "manager", "teacher") is True
        assert TenantService._has_intersection(
            user_role=["school_admin"],
            allowed_roles={"school_admin", "super_admin"}
        ) is True

    def test_admin_role_alias_mapping(self):
        admin_alias_user = CurrentUser(
            user_id="admin_456",
            tenant_id="tenant_a",
            role="admin",
            roles=["admin"]
        )
        assert admin_alias_user.has_role("school_admin") is True
        assert admin_alias_user.has_role("admin") is True
        assert admin_alias_user.has_role("user:view") is True
        assert TenantService._has_intersection(
            user_role=["admin"],
            allowed_roles={"school_admin", "super_admin", "admin"}
        ) is True

