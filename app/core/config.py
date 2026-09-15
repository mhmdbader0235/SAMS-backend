"""
Application configuration.

All values are read from environment variables (or a .env file loaded by
python-dotenv). Never hard-code secrets here — use .env.example as a template.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# ─── PostgreSQL (base connection used for sys-level DB creation) ──────────────
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql://admin:secure_local_password@127.0.0.1:5433/user_service_db",
)

DB_HOST: str = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT: int = int(os.getenv("DB_PORT", "5433"))
DB_USER: str = os.getenv("DB_USER", "admin")
DB_PASSWORD: str = os.getenv("DB_PASSWORD", "secure_local_password")

# ─── JWT ─────────────────────────────────────────────────────────────────────
JWT_SECRET: str = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
# Was 1440 (24h) with no refresh mechanism, so an exfiltrated token stayed
# valid for a full day regardless of anything a school did in the meantime.
# Refresh tokens with rotation (see REFRESH_TOKEN_EXPIRATION_DAYS below,
# AuthService.issue_refresh_token/rotate_refresh_token) now cover the
# "session stays alive" job, so the access token itself can drop to
# something short without forcing a password prompt every time it expires.
# Revocation/demotion no longer waits on this at all -- dependencies.py reads
# status/roles live from user_tenant_map on every request -- but this still
# bounds how long a *stolen* token keeps working.
JWT_EXPIRATION_MINUTES: int = int(os.getenv("JWT_EXPIRATION_MINUTES", "30"))
REFRESH_TOKEN_EXPIRATION_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRATION_DAYS", "30"))

# RS256 key file paths (optional — only needed when JWT_ALGORITHM=RS256)
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JWT_PRIVATE_KEY_PATH: str = os.getenv(
    "JWT_PRIVATE_KEY_PATH", os.path.join(_base, "deploy", "certs", "key.pem")
)
JWT_PUBLIC_KEY_PATH: str = os.getenv(
    "JWT_PUBLIC_KEY_PATH", os.path.join(_base, "deploy", "certs", "cert.pem")
)

# ─── App secrets ─────────────────────────────────────────────────────────────
TEACHER_INVITE_CODE: str = os.getenv("TEACHER_INVITE_CODE", "regester123")

# Dedicated code for the FIRST super_admin bootstrap. Kept separate from the
# staff self-registration passphrases below on purpose: those are meant to be
# shared with any new teacher/manager, so if super_admin accepted them too,
# knowing any one of them would let a caller mint themselves full
# cross-tenant platform access instead of just staff access in one school.
SUPER_ADMIN_BOOTSTRAP_CODE: str = os.getenv(
    "SUPER_ADMIN_BOOTSTRAP_CODE", "sd-platform-bootstrap-2026"
)

# The platform has exactly one super_admin identity. Knowing the bootstrap code
# (or holding an existing super_admin session) is deliberately NOT sufficient on
# its own to mint a new one — every super_admin-creation path must also check
# the target email against this allowlist. Without this, the bootstrap code
# alone lets anyone who has it (a QA suite, a leaked .env, a former operator)
# create themselves a permanent, un-expiring, cross-tenant account.
SUPER_ADMIN_ALLOWED_EMAIL: str = os.getenv("SUPER_ADMIN_ALLOWED_EMAIL", "sa@desk.com")
CONTROL_PLANE_DB_NAME: str = os.getenv("CONTROL_PLANE_DB_NAME", "user_service_db")
ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "7_L_y2C9W-g63_FmH2o9fXkPvxnK74yC5k9zRzR0yM4=")

# ─── Connection pools ──────────────────────────────────────────────────────
# Split so tuning the control-plane pool for its growing per-request load
# (dependencies.py now probes user_tenant_map on every authenticated
# request) doesn't inflate every tenant pool by the same factor -- one
# Database class with one hardcoded size used to govern both. Neither pool
# previously set an acquire timeout, so contention queued indefinitely
# instead of failing fast into a 503 someone would notice.
CP_POOL_MIN: int = int(os.getenv("CP_POOL_MIN", "10"))
CP_POOL_MAX: int = int(os.getenv("CP_POOL_MAX", "30"))
CP_POOL_ACQUIRE_TIMEOUT: float = float(os.getenv("CP_POOL_ACQUIRE_TIMEOUT", "2.0"))
TENANT_POOL_MIN: int = int(os.getenv("TENANT_POOL_MIN", "1"))
TENANT_POOL_MAX: int = int(os.getenv("TENANT_POOL_MAX", "5"))
TENANT_POOL_ACQUIRE_TIMEOUT: float = float(os.getenv("TENANT_POOL_ACQUIRE_TIMEOUT", "5.0"))

# ─── OPA AuthZ ───────────────────────────────────────────────────────────────
# This default (host-reachable localhost) is for running the backend bare on
# the host via `python run.py`, per the "Working rhythm" section of
# CLAUDE.md — still a supported dev path. When the backend runs as the
# `backend` service in docker-compose.yml instead, that file sets OPA_URL to
# the in-Docker "opa" hostname explicitly, overriding this default.
OPA_URL: str = os.getenv("OPA_URL", "http://localhost:8181/v1/data/school/authz/allow")

# ─── CORS ────────────────────────────────────────────────────────────────────
# Extra browser origins allowed on top of the fixed localhost/127.0.0.1 dev
# ports app/main.py's CORSMiddleware always allows. Comma-separated, e.g.
# "https://sams.example.com,http://203.0.113.10:9080". Empty by default —
# set this once the real production frontend URL is known, otherwise every
# browser request from that origin is CORS-blocked before it reaches a route.
CORS_EXTRA_ORIGINS: list[str] = [
    origin.strip() for origin in os.getenv("CORS_EXTRA_ORIGINS", "").split(",") if origin.strip()
]
