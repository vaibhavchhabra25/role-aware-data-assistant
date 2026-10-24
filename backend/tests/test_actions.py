"""Write actions: validation rules, execution, writer-login permissions. No LLM calls.

Needs the local DB with roles applied. Rows created here are removed afterwards.
"""

import os

import psycopg
import pytest
from psycopg import errors

from app.actions import ACTIONS, purchase_order, stock
from app.actions.models import ActionDraft, ActionError, DraftLine
from app.config import database_url, role_conninfo
from app.security.roles import get_role

PROC, INV = get_role("procurement_manager"), get_role("inventory_manager")
TEST_USER = "pytest-writer"
ITEM = "SS Sheet 304 1500x3000x2mm"  # bought before from Sterling, stocked at 132-1


@pytest.fixture(scope="module", autouse=True)
def db_and_cleanup():
    try:
        psycopg.connect(database_url(), connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "3"))).close()
    except psycopg.OperationalError as e:
        pytest.skip(f"local DB not reachable: {e}")
    yield
    with psycopg.connect(database_url()) as c:
        c.execute("DELETE FROM proc_purchase_orders WHERE created_by = %s", (TEST_USER,))
        c.execute("""UPDATE inv_current_stock s SET quantity = s.quantity - t.quantity FROM inv_transactions t
                     WHERE t.created_by = %s AND s.item_id = t.item_id AND s.location_id = t.location_id""", (TEST_USER,))
        c.execute("DELETE FROM inv_transactions WHERE created_by = %s", (TEST_USER,))


def po_draft(**kw) -> ActionDraft:
    return ActionDraft(action="create_purchase_order", **({"supplier": "Sterling", "lines": [DraftLine(item=ITEM, quantity=10)]} | kw))


def adjust_draft(**kw) -> ActionDraft:
    return ActionDraft(action="adjust_stock", **({"item": ITEM, "warehouse": "132-1", "quantity_change": 3, "reason": "recount"} | kw))


# --- validation ------------------------------------------------------------------------------


def test_ambiguous_item_asks_which_one_with_options():
    with pytest.raises(ActionError) as e:
        purchase_order.plan(po_draft(lines=[DraftLine(item="SS Sheet 304", quantity=10)]), PROC)
    assert e.value.message.startswith("Which item") and 2 <= len(e.value.options) <= 4


@pytest.mark.parametrize(
    "draft, message",
    [
        (po_draft(supplier="Nonexistent Traders"), "couldn't find a supplier"),
        (po_draft(lines=[DraftLine(item=ITEM, quantity=0)]), "more than zero"),
        (po_draft(lines=[DraftLine(item=ITEM, quantity=5, unit_price=-1)]), "more than zero"),
        (po_draft(supplier=None), "Which supplier"),
    ],
)
def test_po_rules(draft, message):
    with pytest.raises(ActionError, match=message):
        purchase_order.plan(draft, PROC)


def test_po_defaults_to_last_price_and_two_week_delivery():
    plan = purchase_order.plan(po_draft(), PROC)
    assert plan.payload["lines"][0]["price_from_history"] and float(plan.payload["lines"][0]["unit_price"]) > 0
    assert "default: 2 weeks" in dict(plan.summary)["Expected delivery"]


@pytest.mark.parametrize(
    "draft, message",
    [
        (adjust_draft(quantity_change=-10**9), "can't go below zero"),
        (adjust_draft(quantity_change=None, counted_quantity=None), "By how much"),
        (adjust_draft(reason=None), "reason"),
        (adjust_draft(warehouse="Mars"), "couldn't find a warehouse"),
    ],
)
def test_adjustment_rules(draft, message):
    with pytest.raises(ActionError, match=message):
        stock.plan(draft, INV)


# --- execution -------------------------------------------------------------------------------


def test_create_po_writes_header_and_lines():
    plan = purchase_order.plan(po_draft(), PROC)
    result = purchase_order.execute(plan, PROC, TEST_USER)
    with psycopg.connect(database_url()) as c:
        status, total, lines = c.execute(
            """SELECT po.status, po.total_amount, count(l.id) FROM proc_purchase_orders po
               JOIN proc_po_lines l ON l.po_id = po.id WHERE po.po_number = %s GROUP BY 1, 2""", (result["po_number"],)
        ).fetchone()
    assert status == "placed" and lines == 1 and float(total) == float(plan.payload["total"])


def test_adjustment_keeps_stock_equal_to_ledger_and_rejects_stale_cards():
    plan = stock.plan(adjust_draft(), INV)
    stock.execute(plan, INV, TEST_USER)
    with pytest.raises(ActionError, match="changed"):
        stock.execute(plan, INV, TEST_USER)  # same card again: stock moved since it was shown
    with psycopg.connect(database_url()) as c:
        mismatches = c.execute("""WITH l AS (SELECT item_id, location_id, sum(quantity) q FROM inv_transactions GROUP BY 1, 2)
                                  SELECT count(*) FROM l FULL JOIN inv_current_stock s USING (item_id, location_id)
                                  WHERE abs(coalesce(s.quantity, 0) - coalesce(l.q, 0)) > 0.001""").fetchone()[0]
    assert mismatches == 0


# --- writer logins can only do their own action --------------------------------------------


@pytest.mark.parametrize(
    "writer, statement",
    [
        (purchase_order.WRITER, "UPDATE proc_purchase_orders SET status = 'received' WHERE false"),
        (purchase_order.WRITER, "DELETE FROM proc_po_lines WHERE false"),
        (purchase_order.WRITER, "INSERT INTO inv_transactions (id) VALUES ('x')"),
        (purchase_order.WRITER, "UPDATE inv_current_stock SET quantity = 0 WHERE false"),
        (purchase_order.WRITER, "SELECT id FROM proc_po_payment_tranches LIMIT 1"),
        (stock.WRITER, "INSERT INTO proc_purchase_orders (id) VALUES ('x')"),
        (stock.WRITER, "UPDATE inv_current_stock SET min_stock_level = 0 WHERE false"),
        (stock.WRITER, "DELETE FROM inv_transactions WHERE false"),
        (stock.WRITER, "UPDATE inv_transactions SET quantity = 0 WHERE false"),
        (stock.WRITER, "SELECT name FROM proc_vendors LIMIT 1"),
    ],
)
def test_writer_logins_are_limited_to_their_grants(writer, statement):
    with psycopg.connect(role_conninfo(writer), autocommit=True) as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(statement)


def test_read_only_role_logins_still_cannot_write():
    with psycopg.connect(role_conninfo(PROC.db_role), autocommit=True) as conn:
        with pytest.raises((errors.InsufficientPrivilege, errors.ReadOnlySqlTransaction)):
            conn.execute("INSERT INTO proc_purchase_orders (id) VALUES ('x')")


def test_every_action_is_granted_to_some_role():
    granted = {a for r in (PROC, INV, get_role("owner")) for a in r.actions}
    assert granted == set(ACTIONS)
