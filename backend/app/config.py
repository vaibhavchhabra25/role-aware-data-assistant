"""Paths and environment. Secrets are read from the repo-root .env, never hard-coded."""

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from psycopg.conninfo import make_conninfo

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
CONFIG_DIR = BACKEND_DIR / "config"

load_dotenv(REPO_ROOT / ".env")

# "Today" for the assistant. The company data ends on 21 Aug 2026, so relative dates
# ("last month", "overdue") are resolved against this date, not the wall clock.
AS_OF_DATE = date.fromisoformat(os.getenv("DATA_AS_OF", "2026-08-21"))

# Models. The guard decides what each role may get, so it uses the stronger model:
# on the eval cases gpt-4.1 was 112/112 and stable vs 109/112 for gpt-4.1-mini, for
# ~1k tokens a request. Formatting stays on the cheap model.
GUARD_MODEL = os.getenv("GUARD_MODEL", "gpt-4.1")
FAST_MODEL = os.getenv("FAST_MODEL", "gpt-4.1-mini")
SQL_MODEL = os.getenv("SQL_MODEL", "gpt-4.1")
# Optional: any OpenAI-compatible provider (e.g. Groq). Unset = OpenAI with OPENAI_API_KEY.
LLM_BASE_URL = os.getenv("LLM_BASE_URL") or None
LLM_API_KEY = os.getenv("LLM_API_KEY") or None

# Cache identical LLM calls on disk during development (keyed by the full prompt).
LLM_CACHE_PATH = BACKEND_DIR / ".cache" / "llm.sqlite" if os.getenv("LLM_CACHE", "1") == "1" else None

# Browser origins allowed to call the API (the Next.js app).
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]

# created_by / received_by on every row added during data preparation.
SYNTHETIC_USER = "00000000-0000-4000-8000-000000000001"


def database_url() -> str:
    """Admin connection to the local working copy. Used by scripts and tests, never by the agent."""
    return os.environ["DATABASE_URL"]


def role_password_var(db_role: str) -> str:
    """Name of the env var holding a Postgres role's password, e.g. ROLE_INVENTORY_PASSWORD."""
    return f"{db_role.upper()}_PASSWORD"


def agent_state_url() -> str:
    """Separate local database for LangGraph checkpoints (conversation state)."""
    return make_conninfo(database_url(), dbname="agent_state")


def role_conninfo(db_role: str) -> str:
    """Connection string for a per-role read-only login on the local DB."""
    return make_conninfo(database_url(), user=db_role, password=os.environ[role_password_var(db_role)])
