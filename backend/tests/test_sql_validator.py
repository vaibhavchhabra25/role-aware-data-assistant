import pytest

from app.security.roles import get_role
from app.security.sql_validator import MAX_ROWS, SQLValidationError, referenced_tables, validate_sql

INVENTORY = get_role("inventory_manager")
PROCUREMENT = get_role("procurement_manager")
OWNER = get_role("owner")


# --- Allowed queries --------------------------------------------------------


def test_simple_select_passes_and_gets_limit():
    result = validate_sql("SELECT name, unit FROM inv_items", INVENTORY)
    assert result.tables == {"inv_items"}
    assert result.sql.endswith(f"LIMIT {MAX_ROWS}")


def test_join_cte_and_subquery_tables_are_collected():
    sql = """
        WITH low AS (
            SELECT item_id, location_id FROM inv_current_stock WHERE quantity < min_stock_level
        )
        SELECT i.item_code, l.name
        FROM low
        JOIN inv_items i ON i.id = low.item_id
        JOIN inv_locations l ON l.id = low.location_id
        WHERE i.category_id IN (SELECT id FROM inv_categories WHERE short_code = 'RM')
    """
    assert validate_sql(sql, INVENTORY).tables == {"inv_current_stock", "inv_items", "inv_locations", "inv_categories"}


def test_count_star_is_allowed():
    validate_sql("SELECT count(*) FROM proc_purchase_orders", PROCUREMENT)


def test_small_limit_is_kept_and_large_limit_is_capped():
    assert validate_sql("SELECT name FROM inv_items LIMIT 10", INVENTORY).sql.endswith("LIMIT 10")
    assert validate_sql("SELECT name FROM inv_items LIMIT 100000", INVENTORY).sql.endswith(f"LIMIT {MAX_ROWS}")


def test_union_gets_limit():
    sql = "SELECT name FROM inv_items UNION SELECT name FROM inv_locations"
    assert validate_sql(sql, INVENTORY).sql.endswith(f"LIMIT {MAX_ROWS}")


def test_generate_series_and_date_functions_are_allowed():
    sql = """
        SELECT m.month, count(t.id)
        FROM generate_series(date_trunc('month', DATE '2026-08-21') - interval '5 months',
                             date_trunc('month', DATE '2026-08-21'), interval '1 month') AS m(month)
        LEFT JOIN inv_transactions t ON date_trunc('month', t.created_at) = m.month
        GROUP BY m.month
    """
    assert validate_sql(sql, INVENTORY).tables == {"inv_transactions"}


def test_owner_can_join_across_domains():
    sql = """
        SELECT i.item_code, v.name
        FROM inv_current_stock s
        JOIN inv_items i ON i.id = s.item_id
        JOIN proc_po_lines l ON l.inv_item_id = i.id
        JOIN proc_purchase_orders po ON po.id = l.po_id
        JOIN proc_vendors v ON v.id = po.vendor_id
    """
    assert len(validate_sql(sql, OWNER).tables) == 5


def test_schema_qualified_public_is_allowed():
    validate_sql("SELECT name FROM public.inv_items", INVENTORY)


# --- Role allowlist -----------------------------------------------------------


@pytest.mark.parametrize(
    "role, sql",
    [
        (INVENTORY, "SELECT name, payment_terms FROM proc_vendors"),
        (INVENTORY, "SELECT i.name FROM inv_items i JOIN proc_po_lines l ON l.inv_item_id = i.id"),
        (INVENTORY, "SELECT name FROM inv_items WHERE id IN (SELECT inv_item_id FROM proc_po_lines)"),
        (INVENTORY, "WITH x AS (SELECT name FROM proc_vendors) SELECT name FROM x"),
        (INVENTORY, "SELECT name FROM inv_items UNION SELECT name FROM proc_vendors"),
        (PROCUREMENT, "SELECT quantity FROM inv_current_stock"),
        (PROCUREMENT, "SELECT quantity FROM inv_transactions"),
        (OWNER, "SELECT id FROM users"),
        (OWNER, "SELECT checkpoint FROM checkpoints"),
    ],
)
def test_tables_outside_role_are_rejected(role, sql):
    with pytest.raises(SQLValidationError, match="not available"):
        validate_sql(sql, role)


def test_cte_named_like_forbidden_table_does_not_hide_it():
    # The CTE is named proc_vendors but reads a real forbidden table inside.
    sql = "WITH proc_vendors AS (SELECT name FROM proc_vendors) SELECT name FROM proc_vendors"
    with pytest.raises(SQLValidationError):
        validate_sql(sql, INVENTORY)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT rolname, rolpassword FROM pg_catalog.pg_authid",
        "SELECT usename, passwd FROM pg_shadow",
        "SELECT table_name FROM information_schema.tables",
        "SELECT name FROM other_schema.inv_items",
    ],
)
def test_system_catalogs_and_other_schemas_are_rejected(sql):
    with pytest.raises(SQLValidationError):
        validate_sql(sql, OWNER)


# --- Restricted columns -------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT item_id, unit_cost FROM inv_transactions",
        "SELECT t.item_id FROM inv_transactions t WHERE t.unit_cost > 10",
        "SELECT sum(quantity * unit_cost) FROM inv_transactions",
        "SELECT item_id FROM inv_transactions ORDER BY currency",
    ],
)
def test_excluded_columns_are_rejected_for_inventory(sql):
    with pytest.raises(SQLValidationError, match="not available"):
        validate_sql(sql, INVENTORY)


def test_owner_can_read_unit_cost():
    validate_sql("SELECT item_id, unit_cost FROM inv_transactions", OWNER)


@pytest.mark.parametrize("sql", ["SELECT * FROM inv_items", "SELECT i.* FROM inv_items i"])
def test_star_is_rejected(sql):
    with pytest.raises(SQLValidationError, match=r"\*"):
        validate_sql(sql, INVENTORY)


def test_star_on_transactions_cannot_leak_unit_cost():
    with pytest.raises(SQLValidationError):
        validate_sql("SELECT * FROM inv_transactions", INVENTORY)


# --- Writes, DDL and multi-statement injection --------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE inv_current_stock",
        "DELETE FROM proc_purchase_orders",
        "UPDATE proc_purchase_orders SET status = 'received'",
        "INSERT INTO inv_locations (id, name) VALUES ('x', 'y')",
        "TRUNCATE inv_transactions",
        "ALTER TABLE inv_items ADD COLUMN x int",
        "CREATE TABLE x (id int)",
        "GRANT SELECT ON proc_vendors TO role_inventory",
        "COPY inv_items TO '/tmp/items.csv'",
        "SET ROLE role_owner",
        "RESET ROLE",
        "SHOW ALL",
        "VACUUM",
        "SELECT name INTO stolen FROM inv_items",
        "SELECT name FROM inv_items FOR UPDATE",
        "WITH d AS (DELETE FROM inv_items RETURNING id) SELECT id FROM d",
    ],
)
def test_non_select_statements_are_rejected(sql):
    with pytest.raises(SQLValidationError):
        validate_sql(sql, OWNER)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM inv_items; DROP TABLE inv_items",
        "SELECT name FROM inv_items; SELECT name FROM proc_vendors",
        "SELECT 1; SET ROLE role_owner",
    ],
)
def test_multiple_statements_are_rejected(sql):
    with pytest.raises(SQLValidationError, match="Exactly one"):
        validate_sql(sql, OWNER)


def test_comment_cannot_smuggle_a_second_statement():
    result = validate_sql("SELECT name FROM inv_items -- ; DROP TABLE inv_items", INVENTORY)
    assert "DROP" not in result.sql.upper()


def test_unparseable_sql_is_rejected():
    with pytest.raises(SQLValidationError):
        validate_sql("SELEC name FROM", INVENTORY)


# --- Dangerous functions -------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_sleep(10)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT name FROM inv_items WHERE pg_sleep(5) IS NOT NULL",
        "SELECT pg_ls_dir('.')",
        "SELECT lo_import('/etc/passwd')",
        "SELECT dblink('host=evil', 'select 1')",
        "SELECT set_config('role', 'role_owner', false)",
        "SELECT current_setting('data_directory')",
        "SELECT query_to_xml('select name from proc_vendors', true, false, '')",
        "SELECT version()",
        "SELECT pg_terminate_backend(123)",
    ],
)
def test_dangerous_functions_are_rejected(sql):
    with pytest.raises(SQLValidationError, match="not allowed"):
        validate_sql(sql, OWNER)


# --- "Today" is the as-of date ------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM proc_purchase_orders WHERE placed_on > current_date - 30",
        "SELECT count(*) FROM proc_purchase_orders WHERE placed_on > now() - interval '30 days'",
        "SELECT count(*) FROM inv_transactions WHERE created_at > CURRENT_TIMESTAMP - interval '1 day'",
        "SELECT clock_timestamp()",
    ],
)
def test_wall_clock_is_rejected(sql):
    with pytest.raises(SQLValidationError, match="2026-08-21"):
        validate_sql(sql, OWNER)


def test_as_of_date_literal_is_allowed():
    validate_sql("SELECT count(*) FROM proc_purchase_orders WHERE placed_on > DATE '2026-08-21' - 30", OWNER)


# --- Helper used by the eval runner --------------------------------------------


def test_referenced_tables_ignores_ctes():
    sql = "WITH x AS (SELECT item_id FROM inv_current_stock) SELECT i.name FROM x JOIN inv_items i ON i.id = x.item_id"
    assert referenced_tables(sql) == {"inv_current_stock", "inv_items"}
