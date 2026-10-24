"""Database layer: run SQL directly as each role's Postgres login, bypassing the validator.

Even if the prompt and the validator both failed, these grants are what stand
between a user and data outside their role. Needs the local DB with roles applied
(backend/scripts/apply_db_roles.sh).
"""

import os
import psycopg
import pytest
from psycopg import errors, sql

from app.config import role_conninfo
from app.security.roles import ALL_TABLES, load_roles

ROLES = list(load_roles().values())


def connect(role):
    try:
        return psycopg.connect(role_conninfo(role.db_role), connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "3")), autocommit=True)
    except psycopg.OperationalError as e:
        pytest.skip(f"local DB not reachable as {role.db_role}: {e}")


def run(conn, query, params=None):
    return conn.execute(query, params).fetchall()


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.name)
@pytest.mark.parametrize("table", ALL_TABLES)
def test_table_access_matches_allowlist(role, table):
    with connect(role) as conn:
        # Select one column the role may read, so column grants don't mask the table check.
        hidden = role.excluded_columns.get(table, frozenset())
        column = next(c for c in ("id", "item_id", "name") if c not in hidden)
        query = sql.SQL("SELECT {} FROM {} LIMIT 1").format(sql.Identifier(column), sql.Identifier(table))
        if table in role.allowed_tables:
            run(conn, query)
        else:
            with pytest.raises(errors.InsufficientPrivilege):
                run(conn, query)


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.name)
def test_excluded_columns_are_denied(role):
    with connect(role) as conn:
        for table, columns in role.excluded_columns.items():
            for column in columns:
                with pytest.raises(errors.InsufficientPrivilege):
                    run(conn, sql.SQL("SELECT {} FROM {} LIMIT 1").format(sql.Identifier(column), sql.Identifier(table)))
            with pytest.raises(errors.InsufficientPrivilege):
                run(conn, sql.SQL("SELECT * FROM {} LIMIT 1").format(sql.Identifier(table)))


def test_inventory_can_read_permitted_transaction_columns():
    role = load_roles()["inventory_manager"]
    with connect(role) as conn:
        run(conn, "SELECT item_id, quantity, transaction_type FROM inv_transactions LIMIT 1")


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.name)
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE inv_items SET name = name WHERE false",
        "DELETE FROM inv_locations WHERE false",
        "INSERT INTO inv_locations (id, name, is_active, created_at) VALUES ('x', 'x', true, now())",
        "CREATE TABLE stolen (id int)",
        "DROP TABLE inv_items",
        "TRUNCATE proc_vendors",
    ],
)
def test_writes_and_ddl_are_denied(role, statement):
    with connect(role) as conn:
        with pytest.raises((errors.InsufficientPrivilege, errors.ReadOnlySqlTransaction)):
            run(conn, statement)


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.name)
def test_session_is_read_only_with_timeout(role):
    with connect(role) as conn:
        assert run(conn, "SHOW default_transaction_read_only") == [("on",)]
        assert run(conn, "SHOW statement_timeout") == [("5s",)]


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.name)
def test_cannot_switch_to_another_role(role):
    others = [r.db_role for r in ROLES if r.db_role != role.db_role] + ["postgres"]
    with connect(role) as conn:
        for other in others:
            with pytest.raises(errors.InsufficientPrivilege):
                run(conn, sql.SQL("SET ROLE {}").format(sql.Identifier(other)))


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.name)
def test_server_file_functions_are_denied(role):
    with connect(role) as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            run(conn, "SELECT pg_read_file('/etc/passwd')")


def test_long_query_is_cancelled_by_statement_timeout():
    role = load_roles()["owner"]
    with connect(role) as conn:
        with pytest.raises(errors.QueryCanceled):
            run(conn, "SELECT pg_sleep(6)")
