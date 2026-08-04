import re

path = "init.sql"
with open(path, "r", encoding="utf-8") as f:
    sql = f.read()

# Make head_teacher_id optional
sql = sql.replace(
    "head_teacher_id BIGINT      NOT NULL REFERENCES tenant_a.teachers(id) ON DELETE RESTRICT",
    "head_teacher_id BIGINT      REFERENCES tenant_a.teachers(id) ON DELETE RESTRICT"
)

# 1. Remove SCHEMA creation and usage
sql = re.sub(r'CREATE SCHEMA IF NOT EXISTS tenant_a;\s*', '', sql)
sql = sql.replace('tenant_a.', '')

# 2. Add tenant_id to all CREATE TABLE statements (except control plane tables)
control_plane = ['tenants', 'parents', 'super_admins', 'parent_child_links', 'parent_tenant_links']

def inject_tenant_id(match):
    table_name = match.group(1)
    body = match.group(2)
    if table_name in control_plane:
        return match.group(0) 
    
    return f"CREATE TABLE IF NOT EXISTS {table_name} (\n    tenant_id     VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,{body});"

sql = re.sub(r'CREATE TABLE IF NOT EXISTS ([a-z_]+) \((.*?)\);', inject_tenant_id, sql, flags=re.DOTALL)

with open(path, "w", encoding="utf-8") as f:
    f.write(sql)
print("done")
