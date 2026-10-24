"""Adjust stock: record an adjustment movement and update stock on hand in one transaction,
so stock always equals the movement ledger."""

import uuid
from datetime import datetime

from app.actions import lookups
from app.actions.models import ActionDraft, ActionError, ActionPlan
from app.agent.formatting import indian
from app.config import AS_OF_DATE
from app.db.execute import run_query, writer_transaction
from app.security.roles import RoleConfig

WRITER = "role_inventory_writer"
GRANTS = [
    "GRANT SELECT ON inv_current_stock, inv_items, inv_locations TO {role}",
    "GRANT INSERT ON inv_transactions TO {role}",
    "GRANT UPDATE (quantity, last_transaction_at, updated_at) ON inv_current_stock TO {role}",
]


def plan(draft: ActionDraft, role: RoleConfig) -> ActionPlan:
    if not draft.item:
        raise ActionError("Which item's stock should I adjust?")
    if not draft.warehouse:
        raise ActionError("At which warehouse?", [w for (w,) in run_query(role, "SELECT name FROM inv_locations WHERE is_active ORDER BY name").rows][:4])
    if draft.quantity_change is None and draft.counted_quantity is None:
        raise ActionError("By how much should the stock change, or what is the counted quantity?")
    if not draft.reason:
        raise ActionError("What's the reason for this adjustment (e.g. physical count, damaged, found)?")
    item = lookups.item(role, draft.item)
    location = lookups.warehouse(role, draft.warehouse)
    rows = run_query(
        role, "SELECT id, quantity FROM inv_current_stock WHERE item_id = %s AND location_id = %s", (item["id"], location["id"])
    ).rows
    if not rows:
        raise ActionError(f"{item['name']} isn't stocked at {location['name']}, so there's nothing to adjust there.")
    stock_id, current = rows[0]
    new = draft.counted_quantity if draft.counted_quantity is not None else current + draft.quantity_change
    change = round(new - current, 3)
    if new < 0:
        raise ActionError(f"Stock can't go below zero: {item['name']} has {indian(current)} {item['unit']} at {location['name']}.")
    if change == 0:
        raise ActionError(f"{item['name']} already has {indian(current)} {item['unit']} at {location['name']}; nothing to change.")
    unit = item["unit"]
    return ActionPlan(
        action="adjust_stock",
        title=f"Stock adjustment: {item['name']}",
        summary=[
            ("Item", f"{item['item_code']} · {item['name']}"),
            ("Warehouse", location["name"]),
            ("Stock now", f"{indian(current)} {unit}"),
            ("Change", f"{'+' if change > 0 else ''}{indian(change)} {unit}"),
            ("Stock after", f"{indian(new)} {unit}"),
            ("Reason", draft.reason),
        ],
        payload={"stock_id": stock_id, "item_id": item["id"], "location_id": location["id"],
                 "expected_current": current, "change": change, "reason": draft.reason,
                 "item_name": item["name"], "unit": unit, "warehouse": location["name"]},
    )


def execute(plan: ActionPlan, role: RoleConfig, username: str) -> dict:
    """Insert the adjustment and update stock, only if stock hasn't changed since the card was shown."""
    p = plan.payload
    now = datetime.combine(AS_OF_DATE, datetime.now().time())  # the data's "today"
    with writer_transaction(WRITER) as conn:
        updated = conn.execute(
            """UPDATE inv_current_stock SET quantity = quantity + %s, last_transaction_at = %s, updated_at = %s
               WHERE id = %s AND abs(quantity - %s) < 0.0005 AND quantity + %s >= 0
               RETURNING quantity""",
            (p["change"], now, now, p["stock_id"], p["expected_current"], p["change"]),
        ).fetchone()
        if updated is None:
            raise ActionError("Stock changed since I showed you this adjustment, so I didn't apply it. Please ask again.")
        tx_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO inv_transactions
               (id, item_id, location_id, transaction_type, quantity, reference_type, notes, created_by, created_at,
                currency, is_synthetic)
               VALUES (%s, %s, %s, 'adjustment', %s, 'manual_adjustment', %s, %s, %s, 'INR', false)""",
            (tx_id, p["item_id"], p["location_id"], p["change"], p["reason"], username, now),
        )
    return {"transaction_id": tx_id, "new_quantity": updated[0]}


def done_message(result: dict, plan: ActionPlan) -> str:
    p = plan.payload
    change = f"{'+' if p['change'] > 0 else ''}{indian(p['change'])}"
    return f"Done. {p['item_name']} at {p['warehouse']} is now {indian(result['new_quantity'])} {p['unit']} ({change} {p['unit']})."
