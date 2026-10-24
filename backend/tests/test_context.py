"""Schema docs, few-shot examples and the role-scoped context built from them."""

import os
import re

import psycopg
import pytest

from app.agent.context import build_schema_context, examples, schema_docs, select_examples
from app.config import role_conninfo
from app.db.models import Base
from app.security.roles import ALL_TABLES, load_roles
from app.security.sql_validator import validate_sql

ROLES = load_roles()
INVENTORY, PROCUREMENT, OWNER = ROLES["inventory_manager"], ROLES["procurement_manager"], ROLES["owner"]


# --- schema_docs.yaml matches the real schema -------------------------------------


def test_every_table_is_documented():
    assert set(schema_docs()["tables"]) == set(ALL_TABLES)


def test_documented_columns_exist():
    for table, doc in schema_docs()["tables"].items():
        real = {c.name for c in Base.metadata.tables[table].columns}
        assert set(doc.get("columns", {})) <= real, table


def test_joins_reference_real_columns():
    for join in schema_docs()["joins"]:
        for table, column in re.findall(r"(\w+)\.(\w+)", join):
            assert column in Base.metadata.tables[table].columns, join


# --- context never reveals what a role can't access -------------------------------


@pytest.mark.parametrize("role", ROLES.values(), ids=lambda r: r.name)
def test_context_mentions_only_allowed_tables(role):
    context = build_schema_context(role)
    for table in set(ALL_TABLES) - role.allowed_tables:
        assert not re.search(rf"\b{table}\b", context), table
    # No reference columns pointing at hidden tables either.
    if role.name == "procurement_manager":
        assert "inv_transaction_id" not in context


def test_inventory_context_hides_unit_cost():
    context = build_schema_context(INVENTORY)
    assert "unit_cost" not in context
    assert "proc_" not in context


def test_procurement_context_has_no_stock_tables():
    context = build_schema_context(PROCUREMENT)
    assert "inv_current_stock" not in context and "inv_transactions" not in context


def test_definitions_follow_role_access():
    assert "Stock value" in build_schema_context(OWNER)
    assert "Stock value" not in build_schema_context(PROCUREMENT)
    assert "Stock value" not in build_schema_context(INVENTORY)
    assert "Below reorder level" in build_schema_context(INVENTORY)


def test_owner_context_has_everything():
    context = build_schema_context(OWNER)
    assert all(f"### {t}" in context for t in ALL_TABLES)


def test_context_uses_as_of_date_not_wall_clock():
    assert "DATE '2026-08-21'" in build_schema_context(OWNER)


@pytest.mark.parametrize("role", ROLES.values(), ids=lambda r: r.name)
def test_selected_examples_are_within_role(role):
    hidden = {c for cols in role.excluded_columns.values() for c in cols}
    for question in ["supplier prices", "stock below reorder level", "monthly spend trend", "issued items"]:
        for e in select_examples(role, question, k=10):
            assert e.tables <= role.allowed_tables
            assert not any(re.search(rf"\b{c}\b", e.sql) for c in hidden)


def test_examples_are_relevant():
    top = select_examples(PROCUREMENT, "Which suppliers deliver late?", k=1)[0]
    assert "reliable" in top.question or "late" in top.question


def test_each_role_has_examples():
    for role in ROLES.values():
        assert len([e for e in examples() if e.tables <= role.allowed_tables]) >= 5


# --- every example is valid SQL that runs for the narrowest role ---------------------


def narrowest_role(tables):
    fitting = [r for r in ROLES.values() if tables <= r.allowed_tables]
    return min(fitting, key=lambda r: len(r.allowed_tables))


@pytest.mark.parametrize("example", examples(), ids=lambda e: e.question[:40])
def test_example_validates_and_runs(example):
    role = narrowest_role(example.tables)
    validated = [validate_sql(example.sql, role)] + [validate_sql(c.sql, role) for c in example.caveats]
    try:
        conn = psycopg.connect(role_conninfo(role.db_role), connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "3")))
    except psycopg.OperationalError as e:
        pytest.skip(f"local DB not reachable: {e}")
    with conn:
        main, *caveats = validated
        assert conn.execute(main.sql).fetchall(), f"no rows for: {example.question}"
        for c in caveats:
            (n,) = conn.execute(c.sql).fetchone()
            assert isinstance(n, int)
