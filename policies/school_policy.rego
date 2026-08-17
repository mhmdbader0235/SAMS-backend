package school.authz

import rego.v1

# =============================================================================
# SchoolDesk (Doumind) Master OPA Authorization Policy (Rego v1)
# Package: school.authz
# Default Decision: DENY
# Supports Dual-Mode Evaluation:
#   1. Granular Action & Resource State Mode (input.action, input.resource)
#   2. HTTP Route / Path & Method Mode (input.http.path, input.http.method)
# =============================================================================

default allow := false

# -----------------------------------------------------------------------------
# Master Allow Evaluators
# -----------------------------------------------------------------------------

# Super admin bypasses all authorization checks
allow if {
    "super_admin" in input.user.roles
}

# Evaluate Granular Action & Resource State
allow if {
    input.action
    action_allow
}

# Evaluate HTTP Route (Path + Method) for Gateway / Edge / Middleware
allow if {
    input.http
    http_allow
}

# -----------------------------------------------------------------------------
# Tenant Isolation Helpers
# -----------------------------------------------------------------------------
tenant_matches if {
    input.user.tenant_id == input.resource.tenant_id
}

valid_tenant if {
    not input.resource.tenant_id
}

valid_tenant if {
    tenant_matches
}

# =============================================================================
# SECTION A: GRANULAR ACTION & RESOURCE AUTHORIZATION
# =============================================================================

action_allow if {
    valid_tenant
    "school_admin" in input.user.roles
    school_admin_permission[input.action]
}

action_allow if {
    valid_tenant
    "manager" in input.user.roles
    manager_permission[input.action]
    valid_manager_resource_status
}

action_allow if {
    valid_tenant
    "teacher" in input.user.roles
    teacher_permission[input.action]
    valid_teacher_resource_status
}

action_allow if {
    valid_tenant
    "parent" in input.user.roles
    parent_permission[input.action]
    valid_parent_resource_status
}

action_allow if {
    valid_tenant
    "student" in input.user.roles
    student_permission[input.action]
    valid_student_resource_status
}

action_allow if {
    valid_tenant
    "finance" in input.user.roles
    finance_permission[input.action]
}

# Allow granular permission assigned directly to user roles or permissions list
action_allow if {
    valid_tenant
    direct_user_has_permission(input.action)
    valid_action_resource_status(input.action)
}

direct_user_has_permission(act) if {
    act in input.user.roles
}

direct_user_has_permission(act) if {
    act in input.user.permissions
}

valid_action_resource_status(act) if {
    not input.resource.status
}

valid_action_resource_status(act) if {
    act in {
        "event:edit", "event:patch", "event:delete", "event:propose",
        "event:submit", "event:audience_edit", "resource:create",
        "resource:edit", "resource:update", "resource:delete"
    }
    input.resource.status == "draft"
}

valid_action_resource_status(act) if {
    act in {"event:review", "event:publish"}
    input.resource.status == "proposed"
}

valid_action_resource_status(act) if {
    act in {"event:read", "event:view"}
    input.resource.status in {"draft", "proposed", "published"}
}

valid_action_resource_status(act) if {
    not act in {
        "event:edit", "event:patch", "event:delete", "event:propose",
        "event:submit", "event:audience_edit", "resource:create",
        "resource:edit", "resource:update", "resource:delete",
        "event:review", "event:publish"
    }
}

# --- School Admin Permissions Set ---
school_admin_permission := {
    "school:write", "school:read", "level:create", "level:manage", "level:read",
    "class:create", "class:edit", "class:update", "class:read", "class:assign_teacher",
    "user:create", "user:invite", "user:delete", "user:link", "user:view", "user:read",
    "user:profile_read", "user:profile_edit", "teacher:create", "teacher:read", "teacher:write",
    "parent:read", "student:create", "student:read", "event:create", "event:read", "event:view",
    "event:edit", "event:patch", "event:delete", "event:clone", "event:propose", "event:submit",
    "event:review", "event:publish", "event:view_draft", "event:archive", "event:audience_edit",
    "event:audience_predict", "resource:create", "resource:view", "resource:read", "resource:edit",
    "resource:update", "resource:delete", "resource:price", "resource:set_cost", "resource_type:create",
    "resource_type:read", "enrollment:teacher_approve", "enrollment:cancel", "enrollment:view_roster",
    "enrollment:read", "billing:audit", "billing:invoice", "billing:view_payment", "subsidy:manage",
    "audit:view", "health:view", "health:manage", "safety:manage", "announcement:manage",
    "notification:send", "notification:read", "notification:mark_read", "feedback:view"
}

# --- Manager Permissions Set ---
manager_permission := {
    "school:read", "level:read", "class:read", "teacher:read", "parent:read", "student:read",
    "user:view", "user:read", "user:profile_read", "user:profile_edit", "event:read", "event:view",
    "event:review", "event:publish", "event:view_draft", "event:audience_predict", "resource:view",
    "resource:read", "resource:price", "resource:set_cost", "resource_type:read", "billing:invoice",
    "billing:pay", "billing:refund", "billing:audit", "billing:view_payment", "subsidy:manage",
    "enrollment:view_roster", "enrollment:read", "announcement:manage", "notification:send",
    "notification:read", "notification:mark_read", "feedback:view"
}

valid_manager_resource_status if {
    not input.resource.status
}
valid_manager_resource_status if {
    input.action == "event:review"
    input.resource.status == "proposed"
}
valid_manager_resource_status if {
    input.action == "event:publish"
    input.resource.status == "proposed"
}
valid_manager_resource_status if {
    input.action != "event:review"
    input.action != "event:publish"
}

# --- Teacher Permissions Set ---
teacher_permission := {
    "school:read", "level:read", "class:read", "teacher:read", "student:read", "user:view",
    "user:read", "user:profile_read", "user:profile_edit", "event:create", "event:read", "event:view",
    "event:edit", "event:patch", "event:delete", "event:clone", "event:propose", "event:submit",
    "event:view_draft", "event:audience_edit", "event:audience_predict", "resource:create",
    "resource:view", "resource:read", "resource:edit", "resource:update", "resource:delete",
    "resource_type:create", "resource_type:read", "enrollment:teacher_approve", "enrollment:view_roster",
    "enrollment:read", "health:view", "notification:read", "notification:mark_read", "feedback:view",
    "feedback:create"
}

valid_teacher_resource_status if {
    not input.resource.status
}
valid_teacher_resource_status if {
    input.action in {
        "event:edit", "event:patch", "event:delete", "event:propose",
        "event:submit", "event:audience_edit", "resource:create",
        "resource:edit", "resource:update", "resource:delete"
    }
    input.resource.status == "draft"
}
valid_teacher_resource_status if {
    not input.action in {
        "event:edit", "event:patch", "event:delete", "event:propose",
        "event:submit", "event:audience_edit", "resource:create",
        "resource:edit", "resource:update", "resource:delete"
    }
}

# --- Parent Permissions Set ---
parent_permission := {
    "school:read", "user:profile_read", "user:profile_edit", "student:view_linked",
    "event:read", "event:view", "enrollment:parent_approve", "enrollment:cancel",
    "enrollment:read", "billing:pay", "billing:view_payment", "health:manage_child",
    "notification:read", "notification:mark_read", "feedback:create"
}

valid_parent_resource_status if {
    not input.resource.status
}
valid_parent_resource_status if {
    input.resource.status == "published"
}

# --- Student Permissions Set ---
student_permission := {
    "school:read", "user:profile_read", "user:profile_edit", "event:read", "event:view",
    "enrollment:request", "enrollment:read", "notification:read", "notification:mark_read",
    "feedback:create"
}

valid_student_resource_status if {
    not input.resource.status
}
valid_student_resource_status if {
    input.resource.status == "published"
}


# --- Finance Permissions Set ---
finance_permission := {
    "resource:price", "resource:set_cost", "resource:view", "resource:read",
    "billing:invoice", "billing:pay", "billing:refund", "billing:audit",
    "billing:view_payment", "subsidy:manage", "notification:read", "notification:mark_read"
}


# =============================================================================
# SECTION B: HTTP PATH & METHOD AUTHORIZATION (APISIX / GATEWAY / ROUTER)
# =============================================================================

raw_path := trim(input.http.path, "/")
path_segments := split(raw_path, "/")
req_method := upper(input.http.method)

http_allow if {
    "school_admin" in input.user.roles
    school_admin_http_route(req_method, path_segments)
}

http_allow if {
    "manager" in input.user.roles
    manager_http_route(req_method, path_segments)
}

http_allow if {
    "teacher" in input.user.roles
    teacher_http_route(req_method, path_segments)
}

http_allow if {
    "parent" in input.user.roles
    parent_http_route(req_method, path_segments)
}

http_allow if {
    "student" in input.user.roles
    student_http_route(req_method, path_segments)
}

http_allow if {
    "finance" in input.user.roles
    finance_http_route(req_method, path_segments)
}

# Common endpoints accessible to all authenticated roles
common_http_route("GET", ["api", "v1", "auth", "me"])
common_http_route("GET", ["api", "v1", "auth", "profile"])
common_http_route("POST", ["api", "v1", "auth", "profile"])
common_http_route("GET", ["api", "v1", "notifications"])
common_http_route("POST", ["api", "v1", "notifications", _, "read"])

# --- School Admin HTTP Routes ---
school_admin_http_route(m, p) if common_http_route(m, p)
school_admin_http_route(_, p) if {
    p[0] == "api"
    p[1] == "v1"
    p[2] in {"students", "events", "notifications", "invitations", "auth"}
}

# --- Manager HTTP Routes ---
manager_http_route(m, p) if common_http_route(m, p)
manager_http_route("GET", ["api", "v1", "events", "manager-queue"])
manager_http_route("GET", ["api", "v1", "events", "finance-queue"])
manager_http_route("GET", ["api", "v1", "events", "published"])
manager_http_route("GET", ["api", "v1", "events", _])
manager_http_route("GET", ["api", "v1", "events", _, "resources"])
manager_http_route("GET", ["api", "v1", "events", _, "audience", "prediction"])
manager_http_route("POST", ["api", "v1", "events", _, "manager-decision"])
manager_http_route("POST", ["api", "v1", "events", _, "publish"])
manager_http_route("PATCH", ["api", "v1", "events", "resources", _])
manager_http_route("PUT", ["api", "v1", "events", "resources", _])
manager_http_route("PUT", ["api", "v1", "events", "resources", _, "cost"])
manager_http_route("GET", ["api", "v1", "events", "resource-types"])
manager_http_route("GET", ["api", "v1", "events", _, "feedbacks"])
manager_http_route("GET", ["api", "v1", "students", "levels"])
manager_http_route("GET", ["api", "v1", "students", "classes"])
manager_http_route("GET", ["api", "v1", "students", "teachers"])
manager_http_route("GET", ["api", "v1", "students", "parents"])
manager_http_route("GET", ["api", "v1", "students"])
manager_http_route("GET", ["api", "v1", "students", "enrollments"])
manager_http_route("POST", ["api", "v1", "notifications"])

# --- Teacher HTTP Routes ---
teacher_http_route(m, p) if common_http_route(m, p)
teacher_http_route("GET", ["api", "v1", "events"])
teacher_http_route("POST", ["api", "v1", "events"])
teacher_http_route("GET", ["api", "v1", "events", "published"])
teacher_http_route("GET", ["api", "v1", "events", event_id]) if {
    not event_id in {"manager-queue", "finance-queue", "resource-types"}
}
teacher_http_route("PUT", ["api", "v1", "events", _])
teacher_http_route("PATCH", ["api", "v1", "events", _])
teacher_http_route("DELETE", ["api", "v1", "events", _])

teacher_http_route("POST", ["api", "v1", "events", _, "clone"])
teacher_http_route("POST", ["api", "v1", "events", _, "audience"])
teacher_http_route("GET", ["api", "v1", "events", _, "audience", "prediction"])
teacher_http_route("GET", ["api", "v1", "events", "resource-types"])
teacher_http_route("POST", ["api", "v1", "events", "resource-types"])
teacher_http_route("GET", ["api", "v1", "events", _, "resources"])
teacher_http_route("POST", ["api", "v1", "events", _, "resources"])
teacher_http_route("POST", ["api", "v1", "events", _, "submit"])
teacher_http_route("POST", ["api", "v1", "events", _, "publish"])
teacher_http_route("GET", ["api", "v1", "events", _, "feedbacks"])
teacher_http_route("POST", ["api", "v1", "events", _, "feedbacks"])
teacher_http_route("GET", ["api", "v1", "students", "levels"])
teacher_http_route("GET", ["api", "v1", "students", "classes"])
teacher_http_route("GET", ["api", "v1", "students", "teachers"])
teacher_http_route("GET", ["api", "v1", "students"])
teacher_http_route("GET", ["api", "v1", "students", "enrollments"])
teacher_http_route("POST", ["api", "v1", "students", "enrollments", _, "approve"])
teacher_http_route("GET", ["api", "v1", "students", _, "health"])

# --- Parent HTTP Routes ---
parent_http_route(m, p) if common_http_route(m, p)
parent_http_route("GET", ["api", "v1", "events", "published"])
parent_http_route("GET", ["api", "v1", "events", event_id]) if {
    not event_id in {"manager-queue", "finance-queue", "resource-types"}
}
parent_http_route("GET", ["api", "v1", "events", _, "feedbacks"])
parent_http_route("POST", ["api", "v1", "events", _, "feedbacks"])
parent_http_route("GET", ["api", "v1", "events", "enrollments", _, "payment"])
parent_http_route("POST", ["api", "v1", "events", "enrollments", _, "pay"])
parent_http_route("GET", ["api", "v1", "students", "linked"])
parent_http_route("GET", ["api", "v1", "students", "enrollments"])
parent_http_route("POST", ["api", "v1", "students", "enrollments"])
parent_http_route("POST", ["api", "v1", "students", "enrollments", _, "approve"])
parent_http_route("DELETE", ["api", "v1", "students", "enrollments", _])
parent_http_route("GET", ["api", "v1", "students", _, "health"])
parent_http_route("POST", ["api", "v1", "students", _, "health"])

# --- Student HTTP Routes ---
student_http_route(m, p) if common_http_route(m, p)
student_http_route("GET", ["api", "v1", "events", "published"])
student_http_route("GET", ["api", "v1", "events", event_id]) if {
    not event_id in {"manager-queue", "finance-queue", "resource-types"}
}
student_http_route("GET", ["api", "v1", "events", _, "feedbacks"])
student_http_route("POST", ["api", "v1", "events", _, "feedbacks"])
student_http_route("GET", ["api", "v1", "students", "enrollments"])
student_http_route("POST", ["api", "v1", "students", "enrollments"])
student_http_route("DELETE", ["api", "v1", "students", "enrollments", _])

# --- Finance HTTP Routes ---
finance_http_route(m, p) if common_http_route(m, p)
finance_http_route("GET", ["api", "v1", "events", "finance-queue"])
finance_http_route("GET", ["api", "v1", "events", "published"])
finance_http_route("GET", ["api", "v1", "events", event_id]) if {
    not event_id in {"manager-queue"}
}
finance_http_route("GET", ["api", "v1", "events", _, "resources"])
finance_http_route("PUT", ["api", "v1", "events", "resources", _, "cost"])
finance_http_route("PATCH", ["api", "v1", "events", "resources", _])
finance_http_route("PUT", ["api", "v1", "events", "resources", _])
finance_http_route("GET", ["api", "v1", "events", "resource-types"])
finance_http_route("GET", ["api", "v1", "students", "levels"])
finance_http_route("GET", ["api", "v1", "students", "classes"])


