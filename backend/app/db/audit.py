"""Audit log of every write action: who asked, what was proposed, and what happened."""

from typing import Any

from psycopg.types.json import Jsonb

from app.db.app_db import app_pool


def record(username: str, role: str, action: str, status: str, payload: dict[str, Any],
           result: dict[str, Any] | None = None, error: str | None = None) -> None:
    with app_pool().connection() as conn:
        conn.execute(
            "INSERT INTO app_audit_log (username, role, action, status, payload, result, error) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (username, role, action, status, Jsonb(payload), Jsonb(result) if result is not None else None, error),
        )
