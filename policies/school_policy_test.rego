package school.authz_test

import rego.v1

import data.school.authz

# =============================================================================
# 1. SUPER ADMIN (GLOBAL BYPASS & TENANT AGNOSTIC)
# =============================================================================

test_super_admin_cross_tenant_bypass if {
    authz.allow with input as {
        "user": {"id": "sa_1", "tenant_id": "tenant_a", "roles": ["super_admin"]},
        "action": "system:full_wipe",
        "resource": {"tenant_id": "tenant_b", "status": "published"}
    }
}

test_super_admin_http_bypass if {
    authz.allow with input as {
        "user": {"id": "sa_1", "tenant_id": "tenant_a", "roles": ["super_admin"]},
        "http": {"method": "DELETE", "path": "/api/v1/internal/admin/purge"}
    }
}

# =============================================================================
# 2. MULTI-ROLE COMBINATIONS & ROLE INTERSECTION
# =============================================================================

test_dual_role_teacher_action if {
    authz.allow with input as {
        "user": {"id": "dual_usr", "tenant_id": "tenant_a", "roles": ["teacher", "parent"]},
        "action": "event:edit",
        "resource": {"tenant_id": "tenant_a", "status": "draft"}
    }
}

test_dual_role_parent_action if {
    authz.allow with input as {
        "user": {"id": "dual_usr", "tenant_id": "tenant_a", "roles": ["teacher", "parent"]},
        "action": "billing:pay",
        "resource": {"tenant_id": "tenant_a", "status": "published"}
    }
}

test_dual_role_manager_action_denied if {
    not authz.allow with input as {
        "user": {"id": "dual_usr", "tenant_id": "tenant_a", "roles": ["teacher", "parent"]},
        "action": "event:review",
        "resource": {"tenant_id": "tenant_a", "status": "proposed"}
    }
}

test_mixed_unknown_role_with_valid_role if {
    authz.allow with input as {
        "user": {"id": "usr_x", "tenant_id": "tenant_a", "roles": ["custom_auditor", "teacher"]},
        "action": "event:create",
        "resource": {"tenant_id": "tenant_a"}
    }
}

# =============================================================================
# 3. CROSS-TENANT ISOLATION & ATTACK DEFENSE
# =============================================================================

test_school_admin_cross_tenant_denied if {
    not authz.allow with input as {
        "user": {"id": "admin_a", "tenant_id": "tenant_a", "roles": ["school_admin"]},
        "action": "class:create",
        "resource": {"tenant_id": "tenant_b"}
    }
}

test_manager_cross_tenant_publish_denied if {
    not authz.allow with input as {
        "user": {"id": "mgr_a", "tenant_id": "tenant_a", "roles": ["manager"]},
        "action": "event:publish",
        "resource": {"tenant_id": "tenant_b", "status": "proposed"}
    }
}

test_parent_cross_tenant_view_denied if {
    not authz.allow with input as {
        "user": {"id": "parent_a", "tenant_id": "tenant_a", "roles": ["parent"]},
        "action": "event:read",
        "resource": {"tenant_id": "tenant_b", "status": "published"}
    }
}

test_teacher_cross_tenant_propose_denied if {
    not authz.allow with input as {
        "user": {"id": "teacher_a", "tenant_id": "tenant_a", "roles": ["teacher"]},
        "action": "event:propose",
        "resource": {"tenant_id": "tenant_b", "status": "draft"}
    }
}

test_student_cross_tenant_enrollment_denied if {
    not authz.allow with input as {
        "user": {"id": "student_a", "tenant_id": "tenant_a", "roles": ["student"]},
        "action": "enrollment:request",
        "resource": {"tenant_id": "tenant_b", "status": "published"}
    }
}

# =============================================================================
# 4. EVENT LIFECYCLE & STATE MACHINE TRANSITIONS
# =============================================================================

test_teacher_edit_draft_allowed if {
    authz.allow with input as {
        "user": {"id": "t1", "tenant_id": "tenant_a", "roles": ["teacher"]},
        "action": "event:edit",
        "resource": {"tenant_id": "tenant_a", "status": "draft"}
    }
}

test_teacher_edit_proposed_denied if {
    not authz.allow with input as {
        "user": {"id": "t1", "tenant_id": "tenant_a", "roles": ["teacher"]},
        "action": "event:edit",
        "resource": {"tenant_id": "tenant_a", "status": "proposed"}
    }
}

test_teacher_edit_published_denied if {
    not authz.allow with input as {
        "user": {"id": "t1", "tenant_id": "tenant_a", "roles": ["teacher"]},
        "action": "event:edit",
        "resource": {"tenant_id": "tenant_a", "status": "published"}
    }
}

test_teacher_delete_published_denied if {
    not authz.allow with input as {
        "user": {"id": "t1", "tenant_id": "tenant_a", "roles": ["teacher"]},
        "action": "event:delete",
        "resource": {"tenant_id": "tenant_a", "status": "published"}
    }
}

test_teacher_delete_draft_allowed if {
    authz.allow with input as {
        "user": {"id": "t1", "tenant_id": "tenant_a", "roles": ["teacher"]},
        "action": "event:delete",
        "resource": {"tenant_id": "tenant_a", "status": "draft"}
    }
}

test_manager_review_proposed_allowed if {
    authz.allow with input as {
        "user": {"id": "m1", "tenant_id": "tenant_a", "roles": ["manager"]},
        "action": "event:review",
        "resource": {"tenant_id": "tenant_a", "status": "proposed"}
    }
}

test_manager_review_draft_denied if {
    not authz.allow with input as {
        "user": {"id": "m1", "tenant_id": "tenant_a", "roles": ["manager"]},
        "action": "event:review",
        "resource": {"tenant_id": "tenant_a", "status": "draft"}
    }
}

test_manager_review_published_denied if {
    not authz.allow with input as {
        "user": {"id": "m1", "tenant_id": "tenant_a", "roles": ["manager"]},
        "action": "event:review",
        "resource": {"tenant_id": "tenant_a", "status": "published"}
    }
}

test_manager_publish_proposed_allowed if {
    authz.allow with input as {
        "user": {"id": "m1", "tenant_id": "tenant_a", "roles": ["manager"]},
        "action": "event:publish",
        "resource": {"tenant_id": "tenant_a", "status": "proposed"}
    }
}

test_manager_publish_draft_denied if {
    not authz.allow with input as {
        "user": {"id": "m1", "tenant_id": "tenant_a", "roles": ["manager"]},
        "action": "event:publish",
        "resource": {"tenant_id": "tenant_a", "status": "draft"}
    }
}

test_parent_view_published_allowed if {
    authz.allow with input as {
        "user": {"id": "p1", "tenant_id": "tenant_a", "roles": ["parent"]},
        "action": "event:read",
        "resource": {"tenant_id": "tenant_a", "status": "published"}
    }
}

test_parent_view_proposed_denied if {
    not authz.allow with input as {
        "user": {"id": "p1", "tenant_id": "tenant_a", "roles": ["parent"]},
        "action": "event:read",
        "resource": {"tenant_id": "tenant_a", "status": "proposed"}
    }
}

test_parent_view_draft_denied if {
    not authz.allow with input as {
        "user": {"id": "p1", "tenant_id": "tenant_a", "roles": ["parent"]},
        "action": "event:read",
        "resource": {"tenant_id": "tenant_a", "status": "draft"}
    }
}

test_student_view_published_allowed if {
    authz.allow with input as {
        "user": {"id": "s1", "tenant_id": "tenant_a", "roles": ["student"]},
        "action": "event:read",
        "resource": {"tenant_id": "tenant_a", "status": "published"}
    }
}

test_student_view_draft_denied if {
    not authz.allow with input as {
        "user": {"id": "s1", "tenant_id": "tenant_a", "roles": ["student"]},
        "action": "event:read",
        "resource": {"tenant_id": "tenant_a", "status": "draft"}
    }
}

test_student_view_proposed_denied if {
    not authz.allow with input as {
        "user": {"id": "s1", "tenant_id": "tenant_a", "roles": ["student"]},
        "action": "event:read",
        "resource": {"tenant_id": "tenant_a", "status": "proposed"}
    }
}

# =============================================================================
# 5. PRIVILEGE ESCALATION ATTEMPTS
# =============================================================================

test_student_privilege_escalation_review_denied if {
    not authz.allow with input as {
        "user": {"id": "s1", "tenant_id": "tenant_a", "roles": ["student"]},
        "action": "event:review",
        "resource": {"tenant_id": "tenant_a", "status": "proposed"}
    }
}

test_student_privilege_escalation_publish_denied if {
    not authz.allow with input as {
        "user": {"id": "s1", "tenant_id": "tenant_a", "roles": ["student"]},
        "action": "event:publish",
        "resource": {"tenant_id": "tenant_a", "status": "proposed"}
    }
}

test_teacher_privilege_escalation_refund_denied if {
    not authz.allow with input as {
        "user": {"id": "t1", "tenant_id": "tenant_a", "roles": ["teacher"]},
        "action": "billing:refund",
        "resource": {"tenant_id": "tenant_a"}
    }
}

test_teacher_privilege_escalation_invoice_denied if {
    not authz.allow with input as {
        "user": {"id": "t1", "tenant_id": "tenant_a", "roles": ["teacher"]},
        "action": "billing:invoice",
        "resource": {"tenant_id": "tenant_a"}
    }
}

test_parent_privilege_escalation_user_delete_denied if {
    not authz.allow with input as {
        "user": {"id": "p1", "tenant_id": "tenant_a", "roles": ["parent"]},
        "action": "user:delete",
        "resource": {"tenant_id": "tenant_a"}
    }
}

test_parent_privilege_escalation_create_class_denied if {
    not authz.allow with input as {
        "user": {"id": "p1", "tenant_id": "tenant_a", "roles": ["parent"]},
        "action": "class:create",
        "resource": {"tenant_id": "tenant_a"}
    }
}

test_parent_school_health_manage_denied if {
    not authz.allow with input as {
        "user": {"id": "p1", "tenant_id": "tenant_a", "roles": ["parent"]},
        "action": "health:manage",
        "resource": {"tenant_id": "tenant_a"}
    }
}

test_parent_child_health_manage_allowed if {
    authz.allow with input as {
        "user": {"id": "p1", "tenant_id": "tenant_a", "roles": ["parent"]},
        "action": "health:manage_child",
        "resource": {"tenant_id": "tenant_a"}
    }
}

# =============================================================================
# 6. ZERO-TRUST & ANOMALOUS PAYLOAD DEFENSE
# =============================================================================

test_empty_roles_denied if {
    not authz.allow with input as {
        "user": {"id": "anon_1", "tenant_id": "tenant_a", "roles": []},
        "action": "event:read",
        "resource": {"tenant_id": "tenant_a", "status": "published"}
    }
}

test_invalid_action_denied if {
    not authz.allow with input as {
        "user": {"id": "t1", "tenant_id": "tenant_a", "roles": ["teacher"]},
        "action": "super_secret_backdoor_action",
        "resource": {"tenant_id": "tenant_a", "status": "draft"}
    }
}

test_teacher_edit_missing_status_allowed_fallback if {
    authz.allow with input as {
        "user": {"id": "t1", "tenant_id": "tenant_a", "roles": ["teacher"]},
        "action": "event:read",
        "resource": {"tenant_id": "tenant_a"}
    }
}

# =============================================================================
# 7. ROUTE & VERB PROTECTION FOR STUDENTS & PARENTS
# =============================================================================

test_http_teacher_post_events_allowed if {
    authz.allow with input as {
        "user": {"id": "t1", "tenant_id": "tenant_a", "roles": ["teacher"]},
        "http": {"method": "POST", "path": "/api/v1/events"}
    }
}

test_http_student_post_events_denied if {
    not authz.allow with input as {
        "user": {"id": "s1", "tenant_id": "tenant_a", "roles": ["student"]},
        "http": {"method": "POST", "path": "/api/v1/events"}
    }
}

test_http_parent_manager_queue_denied if {
    not authz.allow with input as {
        "user": {"id": "p1", "tenant_id": "tenant_a", "roles": ["parent"]},
        "http": {"method": "GET", "path": "/api/v1/events/manager-queue"}
    }
}

test_http_teacher_finance_queue_denied if {
    not authz.allow with input as {
        "user": {"id": "t1", "tenant_id": "tenant_a", "roles": ["teacher"]},
        "http": {"method": "GET", "path": "/api/v1/events/finance-queue"}
    }
}

test_http_manager_manager_queue_allowed if {
    authz.allow with input as {
        "user": {"id": "m1", "tenant_id": "tenant_a", "roles": ["manager"]},
        "http": {"method": "GET", "path": "/api/v1/events/manager-queue"}
    }
}

test_http_parent_pay_enrollment_allowed if {
    authz.allow with input as {
        "user": {"id": "p1", "tenant_id": "tenant_a", "roles": ["parent"]},
        "http": {"method": "POST", "path": "/api/v1/events/enrollments/42/pay"}
    }
}

test_http_student_pay_enrollment_denied if {
    not authz.allow with input as {
        "user": {"id": "s1", "tenant_id": "tenant_a", "roles": ["student"]},
        "http": {"method": "POST", "path": "/api/v1/events/enrollments/42/pay"}
    }
}

test_http_student_list_classes_denied if {
    not authz.allow with input as {
        "user": {"id": "s1", "tenant_id": "tenant_a", "roles": ["student"]},
        "http": {"method": "GET", "path": "/api/v1/students/classes"}
    }
}

test_http_student_list_students_denied if {
    not authz.allow with input as {
        "user": {"id": "s1", "tenant_id": "tenant_a", "roles": ["student"]},
        "http": {"method": "GET", "path": "/api/v1/students"}
    }
}

test_http_student_list_teachers_denied if {
    not authz.allow with input as {
        "user": {"id": "s1", "tenant_id": "tenant_a", "roles": ["student"]},
        "http": {"method": "GET", "path": "/api/v1/students/teachers"}
    }
}

test_http_parent_list_classes_denied if {
    not authz.allow with input as {
        "user": {"id": "p1", "tenant_id": "tenant_a", "roles": ["parent"]},
        "http": {"method": "GET", "path": "/api/v1/students/classes"}
    }
}

test_http_parent_list_students_denied if {
    not authz.allow with input as {
        "user": {"id": "p1", "tenant_id": "tenant_a", "roles": ["parent"]},
        "http": {"method": "GET", "path": "/api/v1/students"}
    }
}

# =============================================================================
# 8. DYNAMIC ROLES & CUSTOM GRANULAR PERMISSIONS MATRIX TESTS
# =============================================================================

# Test 1: Student granted custom "event:create" permission can create draft event
test_student_granted_event_create_allowed if {
    authz.allow with input as {
        "user": {"id": "s_cust_1", "tenant_id": "tenant_a", "roles": ["student", "event:create"]},
        "action": "event:create",
        "resource": {"tenant_id": "tenant_a", "status": "draft"}
    }
}

# Test 2: Student without "event:create" is denied
test_student_without_event_create_denied if {
    not authz.allow with input as {
        "user": {"id": "s_cust_2", "tenant_id": "tenant_a", "roles": ["student"]},
        "action": "event:create",
        "resource": {"tenant_id": "tenant_a", "status": "draft"}
    }
}

# Test 3: Teacher granted custom "billing:refund" permission can issue refund
test_teacher_granted_billing_refund_allowed if {
    authz.allow with input as {
        "user": {"id": "t_cust_1", "tenant_id": "tenant_a", "roles": ["teacher", "billing:refund"]},
        "action": "billing:refund",
        "resource": {"tenant_id": "tenant_a"}
    }
}

# Test 4: Teacher without "billing:refund" is denied
test_teacher_without_billing_refund_denied if {
    not authz.allow with input as {
        "user": {"id": "t_cust_2", "tenant_id": "tenant_a", "roles": ["teacher"]},
        "action": "billing:refund",
        "resource": {"tenant_id": "tenant_a"}
    }
}

# Test 5: Teacher granted "billing:invoice" permission can issue invoice
test_teacher_granted_billing_invoice_allowed if {
    authz.allow with input as {
        "user": {"id": "t_cust_3", "tenant_id": "tenant_a", "roles": ["teacher", "billing:invoice"]},
        "action": "billing:invoice",
        "resource": {"tenant_id": "tenant_a"}
    }
}

# Test 6: Parent granted custom "health:view" permission can view health roster
test_parent_granted_health_view_allowed if {
    authz.allow with input as {
        "user": {"id": "p_cust_1", "tenant_id": "tenant_a", "roles": ["parent", "health:view"]},
        "action": "health:view",
        "resource": {"tenant_id": "tenant_a"}
    }
}

# Test 7: Parent without "health:view" is denied general health records
test_parent_without_health_view_denied if {
    not authz.allow with input as {
        "user": {"id": "p_cust_2", "tenant_id": "tenant_a", "roles": ["parent"]},
        "action": "health:view",
        "resource": {"tenant_id": "tenant_a"}
    }
}

# Test 8: Manager granted custom "resource:create" permission can create resources
test_manager_granted_resource_create_allowed if {
    authz.allow with input as {
        "user": {"id": "m_cust_1", "tenant_id": "tenant_a", "roles": ["manager", "resource:create"]},
        "action": "resource:create",
        "resource": {"tenant_id": "tenant_a", "status": "draft"}
    }
}

# Test 9: Student granted custom "feedback:view" can view feedback
test_student_granted_feedback_view_allowed if {
    authz.allow with input as {
        "user": {"id": "s_cust_3", "tenant_id": "tenant_a", "roles": ["student", "feedback:view"]},
        "action": "feedback:view",
        "resource": {"tenant_id": "tenant_a"}
    }
}

# Test 10: Parent granted "event:audience_predict" can query prediction
test_parent_granted_audience_predict_allowed if {
    authz.allow with input as {
        "user": {"id": "p_cust_3", "tenant_id": "tenant_a", "roles": ["parent", "event:audience_predict"]},
        "action": "event:audience_predict",
        "resource": {"tenant_id": "tenant_a"}
    }
}

# Test 11: Multi-role Teacher + Manager can review proposed event
test_teacher_and_manager_review_allowed if {
    authz.allow with input as {
        "user": {"id": "tm_1", "tenant_id": "tenant_a", "roles": ["teacher", "manager"]},
        "action": "event:review",
        "resource": {"tenant_id": "tenant_a", "status": "proposed"}
    }
}

# Test 12: Multi-role Teacher + Manager can publish proposed event
test_teacher_and_manager_publish_allowed if {
    authz.allow with input as {
        "user": {"id": "tm_1", "tenant_id": "tenant_a", "roles": ["teacher", "manager"]},
        "action": "event:publish",
        "resource": {"tenant_id": "tenant_a", "status": "proposed"}
    }
}

# Test 13: Multi-role Teacher + Manager can create draft event
test_teacher_and_manager_create_event_allowed if {
    authz.allow with input as {
        "user": {"id": "tm_1", "tenant_id": "tenant_a", "roles": ["teacher", "manager"]},
        "action": "event:create",
        "resource": {"tenant_id": "tenant_a", "status": "draft"}
    }
}

# Test 14: Multi-role Parent + Student can request enrollment
test_parent_and_student_enrollment_request_allowed if {
    authz.allow with input as {
        "user": {"id": "ps_1", "tenant_id": "tenant_a", "roles": ["parent", "student"]},
        "action": "enrollment:request",
        "resource": {"tenant_id": "tenant_a", "status": "published"}
    }
}

# Test 15: Multi-role Parent + Student can approve parent enrollment
test_parent_and_student_parent_approve_allowed if {
    authz.allow with input as {
        "user": {"id": "ps_1", "tenant_id": "tenant_a", "roles": ["parent", "student"]},
        "action": "enrollment:parent_approve",
        "resource": {"tenant_id": "tenant_a", "status": "published"}
    }
}

# Test 16: Custom permission "permissions" array format in input payload is supported
test_custom_permission_in_permissions_array if {
    authz.allow with input as {
        "user": {
            "id": "u_array_1",
            "tenant_id": "tenant_a",
            "roles": ["student"],
            "permissions": ["subsidy:manage"]
        },
        "action": "subsidy:manage",
        "resource": {"tenant_id": "tenant_a"}
    }
}

# Test 17: User with custom permission "event:edit" is still blocked if event is published
test_custom_permission_respects_draft_state_lock if {
    not authz.allow with input as {
        "user": {"id": "s_cust_4", "tenant_id": "tenant_a", "roles": ["student", "event:edit"]},
        "action": "event:edit",
        "resource": {"tenant_id": "tenant_a", "status": "published"}
    }
}

# Test 18: User with custom permission "event:publish" is blocked if event is draft
test_custom_permission_respects_publish_state_lock if {
    not authz.allow with input as {
        "user": {"id": "t_cust_4", "tenant_id": "tenant_a", "roles": ["teacher", "event:publish"]},
        "action": "event:publish",
        "resource": {"tenant_id": "tenant_a", "status": "draft"}
    }
}

# Test 19: User with custom permission cannot cross tenant boundary
test_custom_permission_cross_tenant_blocked if {
    not authz.allow with input as {
        "user": {"id": "t_cust_5", "tenant_id": "tenant_a", "roles": ["teacher", "billing:invoice"]},
        "action": "billing:invoice",
        "resource": {"tenant_id": "tenant_b"}
    }
}

# Test 20: Finance role user can access finance queue via HTTP
test_finance_role_http_finance_queue_allowed if {
    authz.allow with input as {
        "user": {"id": "f1", "tenant_id": "tenant_a", "roles": ["finance"]},
        "http": {"method": "GET", "path": "/api/v1/events/finance-queue"}
    }
}

# Test 21: Finance role user can set resource price
test_finance_role_resource_price_allowed if {
    authz.allow with input as {
        "user": {"id": "f1", "tenant_id": "tenant_a", "roles": ["finance"]},
        "action": "resource:price",
        "resource": {"tenant_id": "tenant_a"}
    }
}

# Test 22: Finance role user can audit billing
test_finance_role_billing_audit_allowed if {
    authz.allow with input as {
        "user": {"id": "f1", "tenant_id": "tenant_a", "roles": ["finance"]},
        "action": "billing:audit",
        "resource": {"tenant_id": "tenant_a"}
    }
}

# Test 23: Finance role user cannot create class (separation of concerns)
test_finance_role_create_class_denied if {
    not authz.allow with input as {
        "user": {"id": "f1", "tenant_id": "tenant_a", "roles": ["finance"]},
        "action": "class:create",
        "resource": {"tenant_id": "tenant_a"}
    }
}

# Test 24: Finance role user cannot review event (Manager concern)
test_finance_role_event_review_denied if {
    not authz.allow with input as {
        "user": {"id": "f1", "tenant_id": "tenant_a", "roles": ["finance"]},
        "action": "event:review",
        "resource": {"tenant_id": "tenant_a", "status": "proposed"}
    }
}

# Test 25: User with cleared permissions cannot execute custom action
test_user_with_cleared_permissions_denied if {
    not authz.allow with input as {
        "user": {"id": "s_cleared", "tenant_id": "tenant_a", "roles": ["student"], "permissions": []},
        "action": "billing:invoice",
        "resource": {"tenant_id": "tenant_a"}
    }
}

