"""Deterministic checks on LLM-generated SQL before it reaches the database.

This is the second security layer (after role-scoped context, before Postgres
grants). It never calls an LLM. A query passes only if it is a single read-only
SELECT over the role's tables, without restricted columns or dangerous functions.
The returned SQL always carries a LIMIT of at most MAX_ROWS.
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError
from sqlglot.optimizer.scope import traverse_scope

from app.config import AS_OF_DATE
from app.security.roles import RoleConfig

MAX_ROWS = 500

# Statement types that must never appear anywhere in the tree (including CTEs).
FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.Command, exp.Copy, exp.Into, exp.Lock, exp.Grant, exp.TruncateTable,
)

# Functions that read files, sleep, reach other servers, change settings or run arbitrary SQL.
FORBIDDEN_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink")
FORBIDDEN_FUNCTIONS = {
    "set_config", "current_setting", "version", "current_version", "inet_server_addr", "inet_client_addr",
    "query_to_xml", "query_to_xml_and_xmlschema", "query_to_xmlschema", "cursor_to_xml",
    "table_to_xml", "schema_to_xml", "database_to_xml", "txid_current", "nextval", "setval",
}


# "Today" is the configured as-of date, never the wall clock.
WALL_CLOCK_FUNCTIONS = {
    "current_date", "current_timestamp", "current_time", "localtimestamp", "localtime",
    "now", "clock_timestamp", "transaction_timestamp", "statement_timestamp", "timeofday",
}


class SQLValidationError(ValueError):
    """Raised with a reason that is safe to feed back to the SQL generator."""


@dataclass(frozen=True)
class ValidatedSQL:
    sql: str
    tables: frozenset[str]


def referenced_tables(sql: str) -> frozenset[str]:
    """Real tables a query reads (CTE names and table functions excluded). Used by evals too."""
    tree = sqlglot.parse_one(sql, dialect="postgres")
    return frozenset(_tables(tree))


def validate_sql(sql: str, role: RoleConfig) -> ValidatedSQL:
    tree = _parse_single_statement(sql)

    if not isinstance(tree, exp.Query):
        raise SQLValidationError("Only SELECT queries are allowed.")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLValidationError(f"{node.key.upper()} is not allowed; the assistant is read-only.")

    _check_functions(tree)
    tables = _tables(tree)
    _check_schemas(tree)

    forbidden = tables - role.allowed_tables
    if forbidden:
        raise SQLValidationError(
            f"Tables not available to the {role.label} role: {', '.join(sorted(forbidden))}."
        )

    _check_columns(tree, role, tables)
    return ValidatedSQL(sql=_enforce_limit(tree).sql(dialect="postgres", comments=False, pretty=True), tables=frozenset(tables))


def _parse_single_statement(sql: str) -> exp.Expression:
    try:
        statements = [s for s in sqlglot.parse(sql, dialect="postgres") if s is not None]
    except ParseError as e:
        raise SQLValidationError(f"SQL could not be parsed: {e.errors[0]['description'] if e.errors else e}") from None
    if len(statements) != 1:
        raise SQLValidationError("Exactly one SQL statement is allowed.")
    return statements[0]


def _tables(tree: exp.Expression) -> set[str]:
    # Scope analysis resolves each name to a CTE/subquery or a real table, so a CTE
    # named like a forbidden table cannot hide the real one it reads from.
    try:
        scopes = traverse_scope(tree)
    except SqlglotError as e:
        raise SQLValidationError(f"SQL could not be analysed: {e}") from None
    return {
        source.name
        for scope in scopes
        for source in scope.sources.values()
        if isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier)
    }


def _check_schemas(tree: exp.Expression) -> None:
    for t in tree.find_all(exp.Table):
        if t.catalog or (t.db and t.db != "public"):
            raise SQLValidationError(f"Schema '{t.catalog or t.db}' is not allowed; use the listed tables only.")


def _check_functions(tree: exp.Expression) -> None:
    for fn in tree.find_all(exp.Func):
        name = (fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()).lower()
        if name in FORBIDDEN_FUNCTIONS or name.startswith(FORBIDDEN_FUNCTION_PREFIXES):
            raise SQLValidationError(f"Function {name}() is not allowed.")
        if name in WALL_CLOCK_FUNCTIONS:
            raise SQLValidationError(f"Don't use {name.upper()}; use DATE '{AS_OF_DATE.isoformat()}' for today.")


def _check_columns(tree: exp.Expression, role: RoleConfig, tables: set[str]) -> None:
    for star in tree.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            raise SQLValidationError("SELECT * is not allowed; list the columns explicitly.")
    for col in tree.find_all(exp.Column):
        if isinstance(col.this, exp.Star):
            raise SQLValidationError("SELECT table.* is not allowed; list the columns explicitly.")

    # Aliases make column ownership ambiguous, so any column with a restricted name is
    # rejected when its table is in the query. Postgres column grants back this up.
    restricted = {c for t in tables for c in role.excluded_columns.get(t, ())}
    for col in tree.find_all(exp.Column):
        if col.name in restricted:
            raise SQLValidationError(f"Column {col.name} is not available to the {role.label} role.")


def _enforce_limit(tree: exp.Query) -> exp.Query:
    limit = tree.args.get("limit")
    value = limit.expression if limit else None
    if isinstance(value, exp.Literal) and value.is_int and int(value.this) <= MAX_ROWS:
        return tree
    return tree.limit(MAX_ROWS)
