import pytest

from app.agent.context import examples
from app.agent.sql_checks import mixed_unit_sum


@pytest.mark.parametrize(
    "sql",
    [
        # monthly total across all items: kg + pcs added together
        "SELECT to_char(created_at, 'YYYY-MM') AS month, sum(-t.quantity) AS issued FROM inv_transactions t GROUP BY 1",
        "SELECT d.name, sum(abs(t.quantity)) FROM inv_transactions t JOIN inv_issued_to_targets d ON d.id = t.issued_to_id GROUP BY d.name",
        "SELECT sum(quantity) FROM inv_current_stock",
        "SELECT po.status, sum(l.quantity_ordered) FROM proc_po_lines l JOIN proc_purchase_orders po ON po.id = l.po_id GROUP BY po.status",
    ],
)
def test_sums_across_units_are_rejected(sql):
    assert mixed_unit_sum(sql) is not None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT i.item_code, i.name, i.unit, sum(-t.quantity) FROM inv_transactions t JOIN inv_items i ON i.id = t.item_id GROUP BY 1, 2, 3",
        "SELECT i.unit, sum(s.quantity) FROM inv_current_stock s JOIN inv_items i ON i.id = s.item_id GROUP BY i.unit",
        "SELECT sum(s.quantity) FROM inv_current_stock s JOIN inv_items i ON i.id = s.item_id WHERE i.item_code = 'RM-MT-FS-0064'",
        "SELECT sum(s.quantity) FROM inv_current_stock s JOIN inv_items i ON i.id = s.item_id WHERE i.name ILIKE '%ss sheet%1.2mm%'",
        "SELECT sum(s.quantity * l.unit_price) FROM inv_current_stock s JOIN proc_po_lines l ON l.inv_item_id = s.item_id",
        "SELECT to_char(created_at, 'YYYY-MM'), count(*) FROM inv_transactions GROUP BY 1",
        "SELECT sum(total_amount) FROM proc_purchase_orders",
    ],
)
def test_safe_sums_are_allowed(sql):
    assert mixed_unit_sum(sql) is None


@pytest.mark.parametrize("example", examples(), ids=lambda e: e.question[:40])
def test_curated_examples_pass(example):
    from app.agent.sql_checks import mixed_unit_ranking

    assert mixed_unit_sum(example.sql) is None
    assert mixed_unit_ranking(example.sql) is None


# --- ranking items against each other across units ------------------------------------------

from app.agent.sql_checks import mixed_unit_ranking  # noqa: E402


@pytest.mark.parametrize(
    "sql",
    [
        # the 'most bought item' query: LIMIT 1 over items in kg and pcs
        """SELECT i.item_code, i.name, i.unit, sum(l.quantity_received) AS total_quantity_bought
           FROM proc_po_lines l JOIN inv_items i ON i.id = l.inv_item_id
           GROUP BY i.item_code, i.name, i.unit ORDER BY total_quantity_bought DESC LIMIT 1""",
        # top 10 issued, ranked by an alias that doesn't say 'quantity'
        """SELECT i.item_code, i.unit, sum(-t.quantity) AS issued FROM inv_transactions t
           JOIN inv_items i ON i.id = t.item_id GROUP BY 1, 2 ORDER BY issued DESC LIMIT 10""",
        # ranking through a CTE column
        """WITH t AS (SELECT l.inv_item_id, l.unit, sum(l.quantity_ordered) AS total FROM proc_po_lines l GROUP BY 1, 2)
           SELECT inv_item_id, unit, total FROM t ORDER BY total DESC LIMIT 5""",
        # window ranking without a per-unit partition
        """SELECT item_code, q, row_number() OVER (ORDER BY q DESC) AS rn
           FROM (SELECT i.item_code, sum(s.quantity) AS q FROM inv_current_stock s JOIN inv_items i ON i.id = s.item_id GROUP BY 1) x""",
    ],
)
def test_ranking_across_units_is_rejected(sql):
    assert mixed_unit_ranking(sql) is not None


@pytest.mark.parametrize(
    "sql",
    [
        # top N per unit
        """WITH t AS (SELECT i.item_code, l.unit, sum(l.quantity_ordered) AS qty,
                  row_number() OVER (PARTITION BY l.unit ORDER BY sum(l.quantity_ordered) DESC) AS rn
                  FROM proc_po_lines l JOIN inv_items i ON i.id = l.inv_item_id GROUP BY i.item_code, l.unit)
           SELECT item_code, unit, qty FROM t WHERE rn <= 3 ORDER BY unit, qty DESC""",
        # filtered to one unit
        """SELECT i.item_code, sum(l.quantity_ordered) AS qty FROM proc_po_lines l JOIN inv_items i ON i.id = l.inv_item_id
           WHERE l.unit = 'kg' GROUP BY 1 ORDER BY qty DESC LIMIT 5""",
        # ranked by money
        """SELECT v.name, sum(l.line_total) AS spend FROM proc_po_lines l JOIN proc_purchase_orders po ON po.id = l.po_id
           JOIN proc_vendors v ON v.id = po.vendor_id GROUP BY 1 ORDER BY spend DESC LIMIT 10""",
        # a full list sorted by quantity (each row shows its unit; no cut)
        "SELECT i.item_code, i.unit, s.quantity FROM inv_current_stock s JOIN inv_items i ON i.id = s.item_id ORDER BY s.quantity DESC LIMIT 500",
        # one unit kept in a CTE, ranked in the outer query (regression: the reply 'In kg')
        """WITH received_kg AS (SELECT i.item_code, i.name, l.unit, sum(l.quantity_received) AS total_received_kg
                FROM proc_po_lines l JOIN inv_items i ON i.id = l.inv_item_id WHERE l.unit = 'kg' GROUP BY 1, 2, 3)
           SELECT item_code, name, total_received_kg AS quantity, unit
           FROM (SELECT item_code, name, total_received_kg, unit, row_number() OVER (ORDER BY total_received_kg DESC) AS rn
                 FROM received_kg) ranked WHERE rn = 1""",
        # counts are not quantities
        "SELECT d.name, count(*) AS issues FROM inv_transactions t JOIN inv_issued_to_targets d ON d.id = t.issued_to_id GROUP BY 1 ORDER BY issues DESC LIMIT 5",
    ],
)
def test_safe_rankings_are_allowed(sql):
    assert mixed_unit_ranking(sql) is None
