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
JWT_EXPIRATION_MINUTES: int = int(os.getenv("JWT_EXPIRATION_MINUTES", "1440"))

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
CONTROL_PLANE_DB_NAME: str = os.getenv("CONTROL_PLANE_DB_NAME", "control_plane_db")
ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "7_L_y2C9W-g63_FmH2o9fXkPvxnK74yC5k9zRzR0yM4=")

