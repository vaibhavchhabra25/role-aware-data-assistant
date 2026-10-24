"""Run validated, read-only SQL as the user's own Postgres role (third security layer)."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import cache
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

from app.config import role_conninfo
from app.security.roles import RoleConfig
from app.security.sql_validator import MAX_ROWS


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]


@cache
def _pool(db_role: str) -> ConnectionPool:
    return ConnectionPool(role_conninfo(db_role), min_size=0, max_size=4, open=True, name=db_role)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def run_query(role: RoleConfig, sql: str, params: dict | tuple | None = None) -> QueryResult:
    """Execute in a read-only transaction with a 5 s timeout, connected as the role's login."""
    with _pool(role.db_role).connection() as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '5s'")
        cur = conn.execute(sql, params)
        columns = [d.name for d in cur.description]
        rows = [[_jsonable(v) for v in row] for row in cur.fetchmany(MAX_ROWS)]
    return QueryResult(columns, rows)


@contextmanager
def writer_transaction(db_role: str) -> Iterator[psycopg.Connection]:
    """A read-write transaction as a writer login (only used by app/actions, with fixed SQL)."""
    with _pool(db_role).connection() as conn, conn.transaction():
        conn.execute("SET LOCAL statement_timeout = '5s'")
        yield conn
