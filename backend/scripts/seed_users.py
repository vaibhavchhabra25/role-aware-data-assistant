"""Create or update the three demo users. Passwords come from .env, never from this file.

Usage: backend/.venv/bin/python backend/scripts/seed_users.py
"""

import os
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import database_url  # noqa: E402
from app.db.users import upsert_user  # noqa: E402
from app.security.auth import hash_password  # noqa: E402

DEMO_USERS = [
    ("inventory", "Inventory Manager", "inventory_manager", "DEMO_INVENTORY_PASSWORD"),
    ("procurement", "Procurement Manager", "procurement_manager", "DEMO_PROCUREMENT_PASSWORD"),
    ("owner", "Owner", "owner", "DEMO_OWNER_PASSWORD"),
]


def main() -> None:
    with psycopg.connect(database_url(), autocommit=True) as admin:
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname = 'agent_state'").fetchone():
            admin.execute("CREATE DATABASE agent_state")
    for username, display_name, role, password_var in DEMO_USERS:
        password = os.environ.get(password_var)
        if not password:
            sys.exit(f"Set {password_var} in .env first.")
        upsert_user(username, display_name, role, hash_password(password))
        print(f"user '{username}' -> {role}")


if __name__ == "__main__":
    main()
