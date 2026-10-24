"""App users, stored in the app's own database (agent_state), not in the company data."""

from dataclasses import dataclass

from app.db.app_db import app_pool


@dataclass(frozen=True)
class UserRecord:
    username: str
    display_name: str
    role: str
    password_hash: str


def get_user(username: str) -> UserRecord | None:
    with app_pool().connection() as conn:
        row = conn.execute(
            "SELECT username, display_name, role, password_hash FROM app_users WHERE username = %s",
            (username.strip().lower(),),
        ).fetchone()
    return UserRecord(*row) if row else None


def upsert_user(username: str, display_name: str, role: str, password_hash: str) -> None:
    with app_pool().connection() as conn:
        conn.execute(
            """INSERT INTO app_users (username, display_name, role, password_hash) VALUES (%s, %s, %s, %s)
               ON CONFLICT (username) DO UPDATE
               SET display_name = EXCLUDED.display_name, role = EXCLUDED.role, password_hash = EXCLUDED.password_hash""",
            (username, display_name, role, password_hash),
        )
