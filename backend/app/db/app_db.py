"""The app's own database (agent_state): users and the audit log. Never the company data."""

from functools import cache

from psycopg_pool import ConnectionPool

from app.config import agent_state_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_users (
    username      text PRIMARY KEY,
    display_name  text NOT NULL,
    role          text NOT NULL CHECK (role IN ('inventory_manager', 'procurement_manager', 'owner')),
    password_hash text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app_audit_log (
    id        bigserial PRIMARY KEY,
    at        timestamptz NOT NULL DEFAULT now(),
    username  text NOT NULL,
    role      text NOT NULL,
    action    text NOT NULL,
    status    text NOT NULL CHECK (status IN ('executed', 'failed', 'cancelled', 'expired')),
    payload   jsonb NOT NULL,
    result    jsonb,
    error     text
);
"""


@cache
def app_pool() -> ConnectionPool:
    pool = ConnectionPool(agent_state_url(), min_size=0, max_size=4, open=True, name="app_db")
    with pool.connection() as conn:
        conn.execute(SCHEMA)
    return pool
