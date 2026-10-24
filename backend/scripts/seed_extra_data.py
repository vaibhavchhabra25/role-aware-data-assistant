"""Add synthetic procurement history (Feb–Jul 2026) and derive realistic reorder levels.

Usage: python backend/scripts/seed_extra_data.py   (run by prepare_local_db.sh after clean_data.sql)

Why: the real data has three weeks of purchase orders (3–20 Aug), too little for
spend trends or supplier delivery performance. Everything here is built from the
real data: real suppliers, the items each supplier actually sold us, their real
prices and order sizes, and their real payment terms.

Rules:
- Deterministic (fixed seed), so every rebuild produces the same data.
- Every synthetic row is identifiable: created_by = SYNTHETIC_USER, is_synthetic = true
  where the column exists, custom_fields = {"synthetic": true} on POs and lines,
  PO numbers in their own series (PO-2026-Hnnn).
- Stock on hand is unchanged: each synthetic delivery is issued to the shop floor
  in full before the as-of date, so every (item, location) nets to zero.
- Some gaps are kept on purpose (missing expected dates, unpaid tranches) so the
  missing-data handling is still exercised.
"""

import json
import math
import random
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import AS_OF_DATE, SYNTHETIC_USER, database_url  # noqa: E402

SEED = 42
FIRST_MONTH = date(2026, 2, 1)
REAL_PO_START = date(2026, 8, 1)  # real POs start in August; synthetic history ends before it
LAST_MONTH = date(2026, 7, 1)
POS_PER_MONTH = (9, 14)
LINES_PER_PO = (1, 3)
MISSING_EXPECTED_DATE = 0.10  # share of POs saved without an expected delivery date
REVISED_DATE = 0.10  # share of POs whose expected date was pushed back once
SPLIT_DELIVERY = 0.15
SHORT_DELIVERY = 0.04
UNPAID_WHEN_DUE = 0.10
PRICE_DRIFT_PER_MONTH = 0.008  # older purchases were slightly cheaper
INTEGER_UNITS = {"pcs", "nos", "nos.", "set", "unit", "pair", "roll"}
SYNTHETIC_FIELDS = json.dumps({"synthetic": True})

rng = random.Random(SEED)
AS_OF_END = datetime.combine(AS_OF_DATE, time(18, 0))


def new_id() -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def money(x: float | Decimal) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def qty(x: float | Decimal, unit: str) -> Decimal:
    step = Decimal("1") if unit.lower() in INTEGER_UNITS else Decimal("0.001")
    return max(step, Decimal(str(x)).quantize(step, ROUND_HALF_UP))


def at_work_hours(d: date) -> datetime:
    return datetime.combine(d, time(rng.randint(9, 17), rng.randint(0, 59), rng.randint(0, 59)))


def weekday_in_month(month_start: date) -> date:
    while True:
        d = month_start + timedelta(days=rng.randint(0, 27))
        if d.weekday() < 6:  # Mon–Sat working days
            return d


@dataclass
class CatalogEntry:
    item_id: str
    unit: str
    price: Decimal
    order_qty: Decimal
    locations: list[str]


@dataclass
class Supplier:
    id: str
    name: str
    terms: str | None
    lead_days: int
    delay_mean: float
    catalog: list[CatalogEntry]


# --- Reading the real data ----------------------------------------------------


def load_suppliers(conn: psycopg.Connection) -> list[Supplier]:
    """Real suppliers with the items they actually sold us, at real prices and order sizes."""
    rows = conn.execute(
        """SELECT po.vendor_id, l.inv_item_id, coalesce(l.unit, i.unit), avg(l.unit_price), avg(l.quantity_ordered),
                  array_agg(DISTINCT s.location_id)
           FROM proc_po_lines l
           JOIN proc_purchase_orders po ON po.id = l.po_id
           JOIN inv_items i ON i.id = l.inv_item_id
           JOIN inv_current_stock s ON s.item_id = l.inv_item_id
           GROUP BY 1, 2, 3"""
    ).fetchall()
    catalogs: dict[str, list[CatalogEntry]] = defaultdict(list)
    for vendor_id, item_id, unit, price, order_qty, locations in rows:
        catalogs[vendor_id].append(CatalogEntry(item_id, unit, money(price), Decimal(order_qty), sorted(locations)))

    suppliers = []
    for vendor_id, name, terms in conn.execute("SELECT id, name, payment_terms FROM proc_vendors ORDER BY name"):
        if vendor_id not in catalogs:
            continue
        # Delivery behaviour per supplier: most are a few days late (as in the real
        # receipts), some are reliable, a few are chronically late.
        tier = rng.choices(["reliable", "usual", "late"], weights=[3, 5, 2])[0]
        if name.startswith("Kingfisher"):  # consistently late in the real data too
            tier = "late"
        delay_mean = {"reliable": rng.uniform(-1, 1), "usual": rng.uniform(2, 4), "late": rng.uniform(6, 10)}[tier]
        suppliers.append(Supplier(vendor_id, name, terms, rng.randint(7, 21), delay_mean, catalogs[vendor_id]))
    return suppliers


def load_issue_targets(conn: psycopg.Connection) -> tuple[list[str], list[int]]:
    """Where stock is issued to, weighted like the real outbound movements."""
    rows = conn.execute(
        """SELECT issued_to_id, count(*) FROM inv_transactions
           WHERE transaction_type = 'outbound' AND issued_to_id IS NOT NULL GROUP BY 1"""
    ).fetchall()
    return [r[0] for r in rows], [r[1] for r in rows]


def next_invoice_no() -> str:
    return f"invoice no {rng.randint(100, 4999)}"


# --- Payment terms ---------------------------------------------------------------


def tranches_for(terms: str | None) -> list[dict]:
    """Translate free-text payment terms into the same tranche shapes the real data uses.

    No terms recorded -> no schedule (as for most real POs); we don't invent terms.
    """
    if not terms:
        return []
    t = terms.lower()
    pct_adv = next((int(p) for p in ("100", "50", "30", "20") if f"{p}%" in t and "adv" in t), 0)
    days = next((int(w) for w in t.replace("%", " ").split() if w.isdigit() and int(w) in (7, 10, 15, 30, 45, 60)), 30)
    if pct_adv == 100:
        return [dict(stage="Advance", due_basis="advance", percentage=100, is_advance=True, blocks_dispatch=True, due_offset_days=None)]
    plan = []
    if pct_adv:
        plan.append(dict(stage="Advance", due_basis="advance", percentage=pct_adv, is_advance=True, blocks_dispatch=True, due_offset_days=None))
    balance = 100 - pct_adv
    on_credit = "credit" in t or ("days" in t and "dispatch" not in t)
    if on_credit:
        stage = f"{'Balance ' if pct_adv else ''}{days} Days Credit"
        plan.append(dict(stage=stage, due_basis="credit", percentage=balance, is_advance=False, blocks_dispatch=False, due_offset_days=days))
    else:
        offset = days if "pdc" in t else 0
        stage = f"{'Balance ' if pct_adv else '100% '}Against Dispatch" if not offset else f"{offset} Days PDC Against Dispatch"
        plan.append(dict(stage=stage, due_basis="dispatch", percentage=balance, is_advance=False, blocks_dispatch=True, due_offset_days=offset))
    return plan


# --- Generating history ----------------------------------------------------------------


class History:
    def __init__(self) -> None:
        self.pos: list[dict] = []
        self.lines: list[dict] = []
        self.receipts: list[dict] = []
        self.transactions: list[dict] = []
        self.tranches: list[dict] = []
        self.po_seq = 0

    def add_po(self, supplier: Supplier, placed_on: date, targets: tuple[list[str], list[int]]) -> None:
        self.po_seq += 1
        po_id = new_id()
        created_at = at_work_hours(placed_on)

        promised = placed_on + timedelta(days=supplier.lead_days + rng.randint(-2, 3))
        expected = None if rng.random() < MISSING_EXPECTED_DATE else [promised]
        if expected and rng.random() < REVISED_DATE:
            expected.append(promised + timedelta(days=rng.randint(5, 12)))
        dispatch = [d - timedelta(days=rng.randint(2, 3)) for d in expected] if expected else None

        # Goods arrive relative to the original promise, with the supplier's typical delay.
        arrival = promised + timedelta(days=max(-3, round(rng.gauss(supplier.delay_mean, 2))))

        entries = rng.sample(supplier.catalog, min(len(supplier.catalog), rng.randint(*LINES_PER_PO)))
        months_back = (REAL_PO_START - placed_on).days / 30
        po_total = Decimal(0)
        received_share = []
        for position, entry in enumerate(entries):
            ordered = qty(entry.order_qty * Decimal(str(rng.uniform(0.6, 1.4))), entry.unit)
            price = money(entry.price * Decimal(str(1 - PRICE_DRIFT_PER_MONTH * months_back * rng.uniform(0.5, 1.5))))
            subtotal = money(ordered * price)
            line_id = new_id()
            received, last_receipt = self.add_receipts(po_id, line_id, entry, ordered, price, arrival, targets)
            received_share.append(received / ordered)
            self.lines.append(dict(
                id=line_id, po_id=po_id, inv_item_id=entry.item_id, quantity_ordered=ordered,
                quantity_received=received, unit=entry.unit, unit_price=price, tax_percentage=Decimal(0),
                line_subtotal=subtotal, line_tax=Decimal(0), line_total=subtotal, position=position,
                custom_fields=SYNTHETIC_FIELDS, created_at=created_at,
                updated_at=last_receipt or created_at,
                expected_delivery_date=json.dumps([d.isoformat() for d in expected]) if expected else None,
                expected_dispatch_date=json.dumps([d.isoformat() for d in dispatch]) if dispatch else None,
            ))
            po_total += subtotal

        status = "received" if all(r >= 1 for r in received_share) else ("partial" if any(received_share) else "placed")
        self.pos.append(dict(
            id=po_id, po_number=f"PO-2026-H{self.po_seq:03d}", vendor_id=supplier.id, status=status,
            placed_on=placed_on, currency="INR", subtotal=po_total, tax_amount=Decimal(0), total_amount=po_total,
            payment_terms=supplier.terms, custom_fields=SYNTHETIC_FIELDS, created_by=SYNTHETIC_USER,
            created_at=created_at, updated_at=created_at,
            expected_delivery_date=json.dumps([d.isoformat() for d in expected]) if expected else None,
            expected_dispatch_date=json.dumps([d.isoformat() for d in dispatch]) if dispatch else None,
        ))
        self.add_tranches(po_id, supplier, placed_on, arrival, po_total, created_at)

    def add_receipts(self, po_id, line_id, entry, ordered, price, arrival, targets) -> tuple[Decimal, datetime | None]:
        """Deliveries for one PO line; returns (quantity received, time of last delivery)."""
        if arrival > AS_OF_DATE:
            return Decimal(0), None
        to_receive = ordered
        if rng.random() < SHORT_DELIVERY:
            to_receive = qty(ordered * Decimal(str(rng.uniform(0.85, 0.95))), entry.unit)
        parts = [to_receive]
        if rng.random() < SPLIT_DELIVERY and to_receive > 1:
            first = qty(to_receive * Decimal("0.6"), entry.unit)
            parts = [first, to_receive - first]

        location = rng.choice(entry.locations)
        received, received_at = Decimal(0), None
        for i, part in enumerate(parts):
            day = arrival + timedelta(days=i * rng.randint(4, 10))
            if day > AS_OF_DATE:
                break
            received_at = at_work_hours(day)
            tx_id = new_id()
            invoice = next_invoice_no()
            self.receipts.append(dict(
                id=new_id(), po_line_id=line_id, quantity=part, location_id=location, notes=invoice,
                inv_transaction_id=tx_id, received_by=SYNTHETIC_USER, received_at=received_at,
            ))
            self.transactions.append(dict(
                id=tx_id, item_id=entry.item_id, location_id=location, transaction_type="inbound",
                quantity=float(part), reference_type="purchase_order", reference_id=po_id, unit_cost=float(price),
                notes=invoice, created_at=received_at, issued_to_id=None,
            ))
            self.add_issues(entry, location, part, price, received_at, targets)
            received += part
        return received, received_at

    def add_issues(self, entry, location, amount, price, after: datetime, targets) -> None:
        """Issue the delivered quantity to the shop floor over the following weeks (nets to zero)."""
        ids, weights = targets
        pieces = rng.randint(1, 4)
        remaining = amount
        for i in range(pieces):
            part = remaining if i == pieces - 1 else qty(amount / pieces * Decimal(str(rng.uniform(0.7, 1.3))), entry.unit)
            part = min(part, remaining)
            if part <= 0:
                break
            when = min(after + timedelta(days=rng.randint(2, 35), hours=rng.randint(0, 6)), AS_OF_END)
            when = max(when, after + timedelta(minutes=5))
            self.transactions.append(dict(
                id=new_id(), item_id=entry.item_id, location_id=location, transaction_type="outbound",
                quantity=-float(part), reference_type="warehouse_issue", reference_id=None, unit_cost=float(price),
                notes=None, created_at=when, issued_to_id=rng.choices(ids, weights)[0],
            ))
            remaining -= part

    def add_tranches(self, po_id, supplier, placed_on, arrival, total, created_at) -> None:
        for seq, t in enumerate(tranches_for(supplier.terms)):
            if t["due_basis"] == "advance":
                due = placed_on + timedelta(days=rng.randint(1, 3))
            elif t["due_basis"] == "dispatch":
                due = arrival - timedelta(days=2) + timedelta(days=t["due_offset_days"] or 0)
            else:
                due = arrival + timedelta(days=t["due_offset_days"] or 30)
            paid = due <= AS_OF_DATE and rng.random() > UNPAID_WHEN_DUE
            self.tranches.append(dict(
                id=new_id(), po_id=po_id, sequence=seq, stage=t["stage"], percentage=Decimal(t["percentage"]),
                amount=money(total * Decimal(t["percentage"]) / 100), is_advance=t["is_advance"],
                blocks_dispatch=t["blocks_dispatch"], due_basis=t["due_basis"], due_offset_days=t["due_offset_days"],
                source="synthetic", cleared_at=at_work_hours(due) if paid else None,
                cleared_by=SYNTHETIC_USER if paid else None, created_at=created_at, updated_at=created_at,
            ))


def generate(suppliers: list[Supplier], targets) -> History:
    history = History()
    weights = [len(s.catalog) for s in suppliers]
    month = FIRST_MONTH
    while month <= LAST_MONTH:
        for placed_on in sorted(weekday_in_month(month) for _ in range(rng.randint(*POS_PER_MONTH))):
            history.add_po(rng.choices(suppliers, weights)[0], placed_on, targets)
        month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    return history


# --- Writing ---------------------------------------------------------------------------------


def insert(conn: psycopg.Connection, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols = list(rows[0])
    placeholders = ", ".join(f"%({c})s" for c in cols)
    with conn.cursor() as cur:
        cur.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})", rows)


def derive_reorder_levels(conn: psycopg.Connection) -> int:
    """Replace the template min/max levels (25/250, 250/2500, ...) on synthetic stock rows.

    min = 3 weeks of average consumption over the last 13 weeks (≈ supplier lead time
    plus a safety week); max = 8 weeks. Items with no consumption get 0/0, which the
    assistant treats as 'no reorder level set'. Hand-set levels on real rows are kept.
    """
    cur = conn.execute(
        """WITH use AS (
               SELECT item_id, location_id, -sum(quantity) / 13.0 AS weekly
               FROM inv_transactions
               WHERE transaction_type = 'outbound' AND created_at > %(as_of)s::date - interval '13 weeks'
               GROUP BY 1, 2)
           UPDATE inv_current_stock s
           SET min_stock_level = ceil(3 * coalesce(u.weekly, 0)),
               max_stock_level = ceil(8 * coalesce(u.weekly, 0))
           FROM inv_current_stock s2
           LEFT JOIN use u ON u.item_id = s2.item_id AND u.location_id = s2.location_id
           WHERE s2.id = s.id AND s.is_synthetic""",
        {"as_of": AS_OF_DATE},
    )
    return cur.rowcount


def main() -> None:
    with psycopg.connect(database_url()) as conn:
        if conn.execute("SELECT 1 FROM proc_purchase_orders WHERE created_by = %s LIMIT 1", (SYNTHETIC_USER,)).fetchone():
            sys.exit("Synthetic history already present; rebuild with prepare_local_db.sh instead.")

        suppliers = load_suppliers(conn)
        history = generate(suppliers, load_issue_targets(conn))

        insert(conn, "proc_purchase_orders", history.pos)
        insert(conn, "proc_po_lines", history.lines)
        insert(conn, "inv_transactions", [
            t | dict(created_by=SYNTHETIC_USER, currency="INR", transfer_location_id=None, is_synthetic=True)
            for t in history.transactions
        ])
        insert(conn, "proc_po_receipts", history.receipts)
        insert(conn, "proc_po_payment_tranches", history.tranches)
        levels = derive_reorder_levels(conn)
        conn.execute(
            """UPDATE inv_current_stock s SET last_transaction_at = t.last
               FROM (SELECT item_id, location_id, max(created_at) AS last FROM inv_transactions GROUP BY 1, 2) t
               WHERE t.item_id = s.item_id AND t.location_id = s.location_id
                 AND t.last > coalesce(s.last_transaction_at, '-infinity')"""
        )

    print(
        f"Synthetic history: {len(history.pos)} POs, {len(history.lines)} lines, {len(history.receipts)} receipts, "
        f"{len(history.transactions)} stock movements, {len(history.tranches)} payment tranches "
        f"from {len(suppliers)} suppliers; reorder levels derived for {levels} stock rows."
    )


if __name__ == "__main__":
    main()
