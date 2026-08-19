# doumind-backend

> Production-ready FastAPI backend for the **SchoolDesk** school event management platform.  
> Multi-tenant architecture: one PostgreSQL database per school (tenant).

## Architecture

```
Router → Service → Repository
```

- **Routers** (`app/routers/`): HTTP request/response handling only. No business logic.
- **Services** (`app/services/`): Pure business logic. No HTTP, no DB.
- **Repositories** (`app/repositories/`): All database queries. No HTTP, no logic.

### Database Schema Notes
- **Classes**: `head_teacher_id` is optional (`NULL` allowed). Classes can be created before assigning a head teacher.

## Quick Start

### 1. Prerequisites
- Python 3.12+
- Docker & Docker Compose
- PostgreSQL 16 (or use Docker)

### 2. Install dependencies
```bash
pip install -r app/requirements.txt
```

### 3. Configure environment
```bash
cp .env.example .env
# Edit .env with your values
```

### 4. Start with Docker
```bash
docker compose up -d
```

This starts:
- **Postgres** on port `5433` (or `5432` local)
- **Keycloak** on port `8000`
- **Apache APISIX** internally as the API Gateway
- **Nginx DMZ** on port `9080` (this is what the frontend talks to, proxying to APISIX)

### 5. Run without Docker (development)
```bash
# Start Postgres separately, then:
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

## Running Tests

```bash
# All tests
pytest tests/ -v

# Unit tests only (no DB required)
pytest tests/unit/ -v

# Integration tests (requires Postgres)
pytest tests/integration/ -v

# With coverage
pytest tests/ --cov=app --cov-report=html
```

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/auth/tenants` | None | List available tenants |
| POST | `/api/v1/auth/register` | None | Register user |
| POST | `/api/v1/auth/login` | None | Login, get JWT |
| GET | `/api/v1/auth/me` | Bearer | Current user info |
| GET/POST | `/api/v1/events` | Bearer | List / create events |
| PUT/PATCH/DELETE | `/api/v1/events/{id}` | Bearer (teacher) | Update / patch / delete event |
| POST | `/api/v1/events/{id}/feedbacks` | Bearer | Add feedback |
| POST | `/api/v1/students/enrollments` | Bearer (parent/student) | Enroll student |
| DELETE | `/api/v1/students/enrollments/{id}` | Bearer (parent) | Cancel enrollment |

## Linting

```bash
ruff check app/ tests/
black --check app/ tests/

# Auto-fix
ruff check --fix app/ tests/
black app/ tests/
```

## Role & Permissions Workflow (Keycloak RBAC)

### ⚙️ Permissions-to-Role Mapping Matrix

| High-Level Role | Granular Role Permissions | Mapped Functions |
|----------------|---------------------------|-------------------|
| **`school_admin`** | `school:write`, `school:read`, `user:invite`, `user:delete`, `user:link`, `user:view`, `event:review`, `event:publish`, `teacher:read`, `enrollment:cancel`, `enrollment:view_roster`, `billing:audit`, `announcement:manage` | Manage school structure, register staff, manage announcements. |
| **`manager`** | `school:read`, `event:review`, `event:publish`, `event:view_draft`, `resource:view`, `resource:price`, `billing:invoice`, `billing:pay`, `billing:refund`, `billing:audit`, `enrollment:view_roster` | Approve event drafts, set final pricing, audit student logs. |
| **`teacher`** | `school:read`, `user:view`, `event:create`, `event:edit`, `event:delete`, `event:propose`, `event:clone`, `teacher:write`, `teacher:read`, `resource:create`, `resource:view`, `enrollment:teacher_approve`, `enrollment:view_roster` | Create events, plan resources, approve enrollments. |
| **`parent`** | `school:read`, `enrollment:parent_approve`, `enrollment:cancel`, `billing:pay` | Approve child requests, pay trip invoices. |
| **`student`** | `school:read`, `enrollment:request` | Browse published trips, request enrollment. |

*Note: The `super_admin` role automatically bypasses all access validations and grants full control.*
