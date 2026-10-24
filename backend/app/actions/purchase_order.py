"""Create a purchase order: resolve and validate a draft, then insert it after confirmation."""

import json
import uuid
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import psycopg

from app.actions import lookups
from app.actions.models import ActionDraft, ActionError, ActionPlan
from app.agent.formatting import indian, rupees
from app.config import AS_OF_DATE
from app.db.execute import run_query, writer_transaction
from app.security.roles import RoleConfig

WRITER = "role_procurement_writer"
GRANTS = [
    "GRANT SELECT ON proc_purchase_orders, proc_po_lines, proc_vendors, inv_items TO {role}",
    "GRANT INSERT ON proc_purchase_orders, proc_po_lines TO {role}",
]
DEFAULT_LEAD_DAYS = 14
MAX_LINES = 20


def _money(x: Decimal | float) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _last_purchase(role: RoleConfig, item_id: str, vendor_id: str) -> tuple[Decimal | None, str | None]:
    """Last price paid (this supplier first, then any) and the unit it was bought in."""
    rows = run_query(
        role,
        """SELECT l.unit_price, l.unit, po.vendor_id = %(v)s AS same_supplier
           FROM proc_po_lines l JOIN proc_purchase_orders po ON po.id = l.po_id
           WHERE l.inv_item_id = %(i)s
           ORDER BY (po.vendor_id = %(v)s) DESC, po.placed_on DESC NULLS LAST, po.created_at DESC
           LIMIT 1""",
        {"i": item_id, "v": vendor_id},
    ).rows
    return (Decimal(str(rows[0][0])), rows[0][1]) if rows else (None, None)


def plan(draft: ActionDraft, role: RoleConfig) -> ActionPlan:
    if not draft.supplier:
        raise ActionError("Which supplier should the purchase order go to?")
    if not draft.lines:
        raise ActionError("Which items and quantities should the purchase order include?")
    if len(draft.lines) > MAX_LINES:
        raise ActionError(f"A purchase order here can have at most {MAX_LINES} items.")
    vendor = lookups.supplier(role, draft.supplier)
    expected = draft.expected_delivery_date or (AS_OF_DATE + timedelta(days=DEFAULT_LEAD_DAYS))
    if expected < AS_OF_DATE:
        raise ActionError(f"The expected delivery date {expected:%d %b %Y} is in the past.")

    lines, rows, total = [], [], Decimal(0)
    for line in draft.lines:
        item = lookups.item(role, line.item)
        if line.quantity <= 0:
            raise ActionError(f"The quantity for {item['name']} must be more than zero.")
        last_price, last_unit = _last_purchase(role, item["id"], vendor["id"])
        price = Decimal(str(line.unit_price)) if line.unit_price is not None else last_price
        if price is None:
            raise ActionError(f"We've never bought {item['name']} before. What unit price should I use?")
        if price <= 0:
            raise ActionError(f"The price for {item['name']} must be more than zero.")
        unit = last_unit or item["unit"]
        amount = _money(Decimal(str(line.quantity)) * price)
        total += amount
        lines.append({"item_id": item["id"], "quantity": line.quantity, "unit": unit, "unit_price": str(_money(price)),
                      "line_total": str(amount), "price_from_history": line.unit_price is None})
        rows.append([item["item_code"], item["name"], f"{indian(line.quantity)} {unit}", rupees(float(price)), rupees(float(amount))])

    priced_from_history = any(line["price_from_history"] for line in lines)
    summary = [
        ("Supplier", vendor["name"]),
        ("Items", str(len(lines))),
        ("Total (before GST)", rupees(float(total))),
        ("Expected delivery", f"{expected:%d %b %Y}" + ("" if draft.expected_delivery_date else " (default: 2 weeks)")),
        ("Payment terms", vendor["payment_terms"] or "Not recorded"),
    ]
    if priced_from_history:
        summary.append(("Prices", "Last price paid, unless you gave one"))
    return ActionPlan(
        action="create_purchase_order",
        title=f"New purchase order to {vendor['name']}",
        summary=summary,
        table={"columns": ["item_code", "item", "quantity", "unit_price", "amount"], "rows": rows},
        payload={"vendor_id": vendor["id"], "vendor_name": vendor["name"], "payment_terms": vendor["payment_terms"],
                 "expected_delivery_date": expected.isoformat(), "lines": lines, "total": str(total)},
    )


def execute(plan: ActionPlan, role: RoleConfig, username: str) -> dict:
    """Insert the PO and its lines in one transaction. Re-checks the supplier and items first."""
    p = plan.payload
    vendor_ok = run_query(role, "SELECT 1 FROM proc_vendors WHERE id = %s AND is_active", (p["vendor_id"],)).rows
    if not vendor_ok:
        raise ActionError("That supplier is no longer active, so I didn't create the purchase order.")
    for line in p["lines"]:
        if not run_query(role, "SELECT 1 FROM inv_items WHERE id = %s AND status = 'active'", (line["item_id"],)).rows:
            raise ActionError("One of the items is no longer active, so I didn't create the purchase order.")

    now = datetime.combine(AS_OF_DATE, datetime.now().time())  # the data's "today"
    expected = json.dumps([p["expected_delivery_date"]])
    for _ in range(3):  # retry if another PO took the same number
        try:
            with writer_transaction(WRITER) as conn:
                last = conn.execute(
                    "SELECT max(po_number) FROM proc_purchase_orders WHERE po_number ~ %s", (f"^PO-{AS_OF_DATE.year}-[0-9]{{4}}$",)
                ).fetchone()[0]
                po_number = f"PO-{AS_OF_DATE.year}-{(int(last.rsplit('-', 1)[1]) if last else 0) + 1:04d}"
                po_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO proc_purchase_orders
                       (id, po_number, vendor_id, status, placed_on, currency, subtotal, tax_amount, total_amount,
                        payment_terms, custom_fields, created_by, created_at, updated_at, expected_delivery_date)
                       VALUES (%s, %s, %s, 'placed', %s, 'INR', %s, 0, %s, %s, '{}', %s, %s, %s, %s)""",
                    (po_id, po_number, p["vendor_id"], AS_OF_DATE, p["total"], p["total"], p["payment_terms"],
                     username, now, now, expected),
                )
                for position, line in enumerate(p["lines"]):
                    conn.execute(
                        """INSERT INTO proc_po_lines
                           (id, po_id, inv_item_id, quantity_ordered, quantity_received, unit, unit_price, tax_percentage,
                            line_subtotal, line_tax, line_total, position, custom_fields, created_at, updated_at,
                            is_regularised, expected_delivery_date)
                           VALUES (%s, %s, %s, %s, 0, %s, %s, 0, %s, 0, %s, %s, '{}', %s, %s, false, %s)""",
                        (str(uuid.uuid4()), po_id, line["item_id"], line["quantity"], line["unit"], line["unit_price"],
                         line["line_total"], line["line_total"], position, now, now, expected),
                    )
            return {"po_number": po_number, "po_id": po_id, "total": p["total"]}
        except psycopg.errors.UniqueViolation:
            continue
    raise ActionError("I couldn't get a new PO number. Please try again.")


def done_message(result: dict, plan: ActionPlan) -> str:
    return f"Created {result['po_number']} to {plan.payload['vendor_name']} for {rupees(float(result['total']))} (before GST)."

