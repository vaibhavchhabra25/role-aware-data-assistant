"""Invariants of the prepared local data (after clean_data.sql + seed_extra_data.py).

These must hold after every rebuild; prepare_local_db.sh runs them as its last step.
"""

import os
import psycopg
import pytest

from app.config import AS_OF_DATE, SYNTHETIC_USER, database_url
from app.security.roles import ALL_TABLES

ORIGINAL_AUTHORS = (
    "f6570cdb-2153-4b92-aedb-77514c880d7d",
    "a12e74bb-c602-49d3-803e-1ff8e1cdef96",
    "3f6b5856-336b-4643-8f6a-241cc69351d0",
)
# Users of this app: rows they create through confirmed write actions are expected.
APP_USERS = ["inventory", "procurement", "owner"]
# Wrote the ledger reconciliation that clean_data.sql keeps as opening balances.
RECONCILIATION_AUTHOR = "c9bbbbc8-c5c6-452d-8fc8-c8ff9b6e72c9"


@pytest.fixture(scope="module")
def conn():
    try:
        with psycopg.connect(database_url(), connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "3"))) as c:
            yield c
    except psycopg.OperationalError as e:
        pytest.skip(f"local DB not reachable: {e}")


def scalar(conn, query, params=None):
    return conn.execute(query, params).fetchone()[0]


def test_stock_equals_ledger_for_every_item_and_location(conn):
    mismatches = scalar(conn, """
        WITH l AS (SELECT item_id, location_id, sum(quantity) AS qty FROM inv_transactions GROUP BY 1, 2)
        SELECT count(*) FROM l FULL JOIN inv_current_stock s USING (item_id, location_id)
        WHERE abs(coalesce(s.quantity, 0) - coalesce(l.qty, 0)) > 0.001""")
    assert mismatches == 0


def test_no_running_balance_goes_negative(conn):
    negative = scalar(conn, """
        WITH r AS (SELECT item_id, location_id,
                          sum(quantity) OVER (PARTITION BY item_id, location_id ORDER BY created_at, id) AS balance
                   FROM inv_transactions)
        SELECT count(DISTINCT (item_id, location_id)) FROM r WHERE balance < -0.001""")
    assert negative == 0


def test_nothing_is_dated_after_the_as_of_date(conn):
    columns = conn.execute(
        """SELECT table_name, column_name FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = ANY(%s)
             AND data_type IN ('date', 'timestamp without time zone')""",
        (list(ALL_TABLES),),
    ).fetchall()
    late = {
        f"{t}.{c}": n
        for t, c in columns
        if (n := scalar(conn, f'SELECT count(*) FROM "{t}" WHERE "{c}"::date > %s', (AS_OF_DATE,)))
    }
    assert late == {}


def test_only_original_or_synthetic_authors_remain(conn):
    allowed = list(ORIGINAL_AUTHORS) + [SYNTHETIC_USER] + APP_USERS
    assert scalar(conn, """SELECT count(*) FROM inv_transactions WHERE created_by <> ALL(%s)
                           AND NOT (created_by = %s AND reference_type IN ('opening_balance', 'stock_count'))""",
                  (allowed, RECONCILIATION_AUTHOR)) == 0
    assert scalar(conn, "SELECT count(*) FROM proc_purchase_orders WHERE created_by <> ALL(%s)", (allowed,)) == 0


def test_physical_count_is_typed_as_adjustment(conn):
    assert scalar(conn, """SELECT count(*) FROM inv_transactions
                           WHERE notes LIKE 'Physical count%%' AND transaction_type <> 'adjustment'""") == 0


def test_po_totals_match_lines(conn):
    assert scalar(conn, """
        SELECT count(*) FROM proc_purchase_orders po
        JOIN (SELECT po_id, sum(line_total) AS total FROM proc_po_lines GROUP BY 1) l ON l.po_id = po.id
        WHERE abs(po.total_amount - l.total) > 0.01""") == 0
    assert scalar(conn, "SELECT count(*) FROM proc_po_lines WHERE abs(line_subtotal + line_tax - line_total) > 0.01") == 0


def test_synthetic_lines_received_equals_receipts(conn):
    assert scalar(conn, """
        SELECT count(*) FROM proc_po_lines l
        JOIN proc_purchase_orders po ON po.id = l.po_id AND po.created_by = %s
        LEFT JOIN (SELECT po_line_id, sum(quantity) AS qty FROM proc_po_receipts GROUP BY 1) r ON r.po_line_id = l.id
        WHERE abs(l.quantity_received - coalesce(r.qty, 0)) > 0.001""", (SYNTHETIC_USER,)) == 0


def test_every_receipt_has_a_matching_inbound_movement(conn):
    assert scalar(conn, """
        SELECT count(*) FROM proc_po_receipts r
        LEFT JOIN inv_transactions t ON t.id = r.inv_transaction_id
        WHERE t.id IS NULL OR t.transaction_type <> 'inbound' OR abs(t.quantity - r.quantity) > 0.001""") == 0


def test_po_status_matches_receipts(conn):
    assert scalar(conn, """
        SELECT count(*) FROM proc_purchase_orders po
        JOIN (SELECT po_id, bool_and(quantity_received >= quantity_ordered) AS done,
                     bool_or(quantity_received > 0) AS started
              FROM proc_po_lines GROUP BY 1) l ON l.po_id = po.id
        WHERE po.created_by = %s
          AND po.status <> CASE WHEN l.done THEN 'received' WHEN l.started THEN 'partial' ELSE 'placed' END""",
        (SYNTHETIC_USER,)) == 0


def test_payment_tranches_add_up_to_100(conn):
    assert scalar(conn, """SELECT count(*) FROM (SELECT po_id FROM proc_po_payment_tranches
                           GROUP BY 1 HAVING abs(sum(percentage) - 100) > 0.01) t""") == 0


def test_reorder_levels_are_consistent(conn):
    assert scalar(conn, "SELECT count(*) FROM inv_current_stock WHERE max_stock_level < min_stock_level") == 0
    # Template defaults are gone from synthetic rows.
    assert scalar(conn, """SELECT count(*) FROM inv_current_stock
                           WHERE is_synthetic AND (min_stock_level, max_stock_level) IN ((25, 250), (250, 2500))""") == 0


def test_po_line_units_use_one_label_per_unit(conn):
    units = {r[0] for r in conn.execute("SELECT DISTINCT unit FROM proc_po_lines WHERE unit IS NOT NULL").fetchall()}
    assert units <= {"pcs", "kg", "set", "mtr", "ltr", "roll", "pair"}, units
