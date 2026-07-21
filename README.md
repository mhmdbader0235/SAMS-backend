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
- **Postgres** on port `5432`
- **Backend API** on port `8001`
- **API Gateway** on port `8000` (this is what the frontend talks to)

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
| GET | `/api/v1/notes` | Bearer | List all events |
| POST | `/api/v1/notes` | Bearer (teacher) | Create event |
| PUT | `/api/v1/notes/{id}` | Bearer (teacher) | Update event |
| DELETE | `/api/v1/notes/{id}` | Bearer (teacher) | Delete event |
| POST | `/api/v1/notes/{id}/comments` | Bearer | Add comment |
| POST | `/api/v1/notes/{id}/enroll` | Bearer (parent) | Enroll student |
| DELETE | `/api/v1/notes/{id}/enroll` | Bearer (parent) | Cancel enrollment |

## Linting

```bash
ruff check app/ tests/
black --check app/ tests/

# Auto-fix
ruff check --fix app/ tests/
black app/ tests/
```
