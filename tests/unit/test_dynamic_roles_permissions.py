"""
Comprehensive Unit Test Suite for SchoolDesk Dynamic Roles, Granular Permissions, and Action Verification.

Tests cover:
1. Single composite roles and their permission sets
2. Multi-role memberships (e.g., Teacher + Parent, Teacher + Manager)
3. Granular custom permission grants per user
4. Role/permission revocation
5. Super admin universal bypass
6. Admin alias mappings
7. Role intersection checks in TenantService
8. OPA verification fallback mechanics in CurrentUser.can()
9. Role catalog validation and edge-case payload safety
"""

import pytest
from unittest.mock import AsyncMock, patch

from app.core.dependencies import CurrentUser, COMPOSITE_ROLE_PERMISSIONS
from app.domains.tenant.service import TenantService


class TestDynamicRolesAndPermissions:
    # 1. Single role baseline test
    def test_single_role_teacher_permissions(self):
        user = CurrentUser(
            user_id="usr_teacher_1",
            tenant_id="tenant_a",
            role="teacher",
            roles=["teacher"]
        )
        assert user.has_role("teacher") is True
        assert user.has_role("event:create") is True
        assert user.has_role("event:edit") is True
        assert user.has_role("resource:create") is True
        assert user.has_role("enrollment:teacher_approve") is True
        # Actions outside teacher role
        assert user.has_role("billing:refund") is False
        assert user.has_role("event:review") is False
        assert user.has_role("class:assign_teacher") is False

    # 2. Multi-role assignment (Teacher + Parent)
    def test_multi_role_teacher_and_parent_composite_union(self):
        user = CurrentUser(
            user_id="usr_dual_1",
            tenant_id="tenant_a",
            role="teacher",
            roles=["teacher", "parent"]
        )
        assert user.has_role("teacher") is True
        assert user.has_role("parent") is True
        # Inherits teacher capabilities
        assert user.has_role("event:create") is True
        assert user.has_role("enrollment:teacher_approve") is True
        # Inherits parent capabilities
        assert user.has_role("enrollment:parent_approve") is True
        assert user.has_role("billing:pay") is True
        assert user.has_role("health:manage_child") is True
        # Does NOT inherit manager capabilities
        assert user.has_role("event:review") is False
        assert user.has_role("billing:refund") is False

    # 3. Multi-role assignment (Teacher + Manager)
    def test_multi_role_teacher_and_manager(self):
        user = CurrentUser(
            user_id="usr_tm_1",
            tenant_id="tenant_a",
            role="teacher",
            roles=["teacher", "manager"]
        )
        assert user.has_role("event:create") is True
        assert user.has_role("event:review") is True
        assert user.has_role("event:publish") is True
        assert user.has_role("billing:refund") is True
        assert user.has_role("billing:invoice") is True

    # 4. Student granted custom "event:create" granular permission
    def test_student_with_custom_event_create_permission(self):
        user = CurrentUser(
            user_id="usr_student_cust",
            tenant_id="tenant_a",
            role="student",
            roles=["student", "event:create"]
        )
        assert user.has_role("student") is True
        assert user.has_role("event:create") is True
        assert user.has_role("enrollment:request") is True
        assert user.has_role("event:delete") is False
        assert user.has_role("event:review") is False

    # 5. Teacher granted custom "billing:refund" granular permission
    def test_teacher_with_custom_billing_refund_permission(self):
        user = CurrentUser(
            user_id="usr_teacher_cust",
            tenant_id="tenant_a",
            role="teacher",
            roles=["teacher", "billing:refund"]
        )
        assert user.has_role("teacher") is True
        assert user.has_role("event:create") is True
        assert user.has_role("billing:refund") is True
        assert user.has_role("class:assign_teacher") is False

    # 6. Parent granted custom "health:view" granular permission
    def test_parent_with_custom_health_view_permission(self):
        user = CurrentUser(
            user_id="usr_parent_cust",
            tenant_id="tenant_a",
            role="parent",
            roles=["parent", "health:view"]
        )
        assert user.has_role("parent") is True
        assert user.has_role("health:view") is True
        assert user.has_role("health:manage_child") is True
        assert user.has_role("health:manage") is False

    # 7. Manager granted custom "resource:create" granular permission
    def test_manager_with_custom_resource_create_permission(self):
        user = CurrentUser(
            user_id="usr_manager_cust",
            tenant_id="tenant_a",
            role="manager",
            roles=["manager", "resource:create"]
        )
        assert user.has_role("manager") is True
        assert user.has_role("event:review") is True
        assert user.has_role("resource:create") is True

    # 8. Finance role capabilities
    def test_finance_role_permissions_set(self):
        user = CurrentUser(
            user_id="usr_finance_1",
            tenant_id="tenant_a",
            role="finance",
            roles=["finance", "resource:price", "resource:set_cost", "billing:invoice", "billing:audit", "billing:refund"]
        )
        assert user.has_role("finance") is True
        assert user.has_role("resource:price") is True
        assert user.has_role("billing:audit") is True
        assert user.has_role("billing:refund") is True
        assert user.has_role("class:create") is False
        assert user.has_role("event:review") is False

    # 9. Super Admin universal bypass on all permission checks
    def test_super_admin_bypasses_all_permissions(self):
        user = CurrentUser(
            user_id="usr_super_admin",
            tenant_id="tenant_a",
            role="super_admin",
            roles=["super_admin"]
        )
        assert user.has_role("any_arbitrary_permission") is True
        assert user.has_role("system:full_wipe") is True
        assert user.has_role("event:create") is True
        assert user.has_role("billing:refund") is True

    # 10. Admin role aliases map cleanly to school_admin
    def test_admin_role_alias_maps_to_school_admin(self):
        user = CurrentUser(
            user_id="usr_admin_alias",
            tenant_id="tenant_a",
            role="admin",
            roles=["admin"]
        )
        assert user.has_role("school_admin") is True
        assert user.has_role("admin") is True
        assert user.has_role("school:write") is True
        assert user.has_role("class:assign_teacher") is True
        assert user.has_role("safety:manage") is True

    # 11. has_any_role helper check
    def test_has_any_role_matches_subset(self):
        user = CurrentUser(
            user_id="usr_1",
            tenant_id="tenant_a",
            role="teacher",
            roles=["teacher"]
        )
        assert user.has_any_role("school_admin", "manager", "teacher") is True
        assert user.has_any_role("super_admin", "manager") is False

    # 12. TenantService._has_intersection capability check with custom permissions
    def test_tenant_service_has_intersection_capabilities(self):
        assert TenantService._has_intersection(
            user_role=["teacher", "billing:refund"],
            allowed_roles={"billing:refund"}
        ) is True

        assert TenantService._has_intersection(
            user_role=["student"],
            allowed_roles={"teacher", "manager"}
        ) is False

        assert TenantService._has_intersection(
            user_role=["super_admin"],
            allowed_roles={"teacher"}
        ) is True

    # 13. TenantService._has_intersection admin guard
    def test_tenant_service_admin_guard_intersection(self):
        assert TenantService._has_intersection(
            user_role=["school_admin"],
            allowed_roles={"school_admin", "super_admin", "admin"}
        ) is True

        assert TenantService._has_intersection(
            user_role=["teacher"],
            allowed_roles={"school_admin", "super_admin", "admin"}
        ) is False

    # 14. OPA verify_opa_authorization success in CurrentUser.can()
    @pytest.mark.asyncio
    async def test_current_user_can_opa_allowed(self):
        user = CurrentUser(
            user_id="usr_1",
            tenant_id="tenant_a",
            role="teacher",
            roles=["teacher"]
        )
        with patch("app.core.opa.verify_opa_authorization", new_callable=AsyncMock) as mock_opa:
            mock_opa.return_value = True
            allowed = await user.can("event:edit", {"status": "draft", "tenant_id": "tenant_a"})
            assert allowed is True
            mock_opa.assert_called_once()

    # 15. CurrentUser.can() fallback when OPA returns False but local check fails
    @pytest.mark.asyncio
    async def test_current_user_can_denied_when_no_role(self):
        user = CurrentUser(
            user_id="usr_student_1",
            tenant_id="tenant_a",
            role="student",
            roles=["student"]
        )
        with patch("app.core.opa.verify_opa_authorization", new_callable=AsyncMock) as mock_opa:
            mock_opa.return_value = False
            allowed = await user.can("billing:refund", {"tenant_id": "tenant_a"})
            assert allowed is False

    # 16. CurrentUser.can() fallback to local role evaluation on OPA connection exception
    @pytest.mark.asyncio
    async def test_current_user_can_fallback_on_opa_exception(self):
        user = CurrentUser(
            user_id="usr_teacher_2",
            tenant_id="tenant_a",
            role="teacher",
            roles=["teacher"]
        )
        with patch("app.core.opa.verify_opa_authorization", new_callable=AsyncMock) as mock_opa:
            mock_opa.side_effect = Exception("OPA service unreachable")
            # Local fallback evaluates teacher permissions for "event:create" -> True
            allowed = await user.can("event:create")
            assert allowed is True

            # Local fallback evaluates teacher permissions for "billing:refund" -> False
            allowed_denied = await user.can("billing:refund")
            assert allowed_denied is False

    # 17. Permission revocation immediately removes access
    def test_permission_revocation(self):
        user = CurrentUser(
            user_id="usr_revoked",
            tenant_id="tenant_a",
            role="student",
            roles=["student", "event:create"]
        )
        assert user.has_role("event:create") is True

        # Simulate permission revocation
        user.roles = ["student"]
        assert user.has_role("event:create") is False

    # 18. Empty or unauthenticated roles return False safely
    def test_empty_roles_safety(self):
        user = CurrentUser(
            user_id="usr_anon",
            tenant_id=None,
            role="",
            roles=[]
        )
        assert user.has_role("event:create") is False
        assert user.has_role("school:read") is False
        assert user.has_role("user:view") is False

    # 19. None/empty input parameter checks in has_role
    def test_has_role_edge_cases(self):
        user = CurrentUser(
            user_id="usr_edge",
            tenant_id="tenant_a",
            role="teacher",
            roles=["teacher"]
        )
        assert user.has_role("") is False
        assert user.has_role("non_existent_role_xyz") is False

    # 20. Composite role catalog dictionary integrity
    def test_composite_role_permissions_catalog_integrity(self):
        required_roles = {"super_admin", "school_admin", "manager", "teacher", "parent", "student"}
        assert required_roles.issubset(set(COMPOSITE_ROLE_PERMISSIONS.keys()))
        for role, perms in COMPOSITE_ROLE_PERMISSIONS.items():
            assert isinstance(perms, set)
            assert len(perms) > 0
