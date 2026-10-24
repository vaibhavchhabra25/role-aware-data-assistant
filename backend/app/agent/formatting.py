"""Numbers for the answer writer: Indian grouping, rupees in lakh/crore, exact totals.

Everything here is computed in Python so the model never does arithmetic or unit conversion.
"""

from typing import Any

MONEY_WORDS = ("amount", "spend", "value", "price", "cost", "inr", "line_total")
NOT_MONEY_WORDS = ("pct", "percent", "share", "ratio", "rate", "count", "qty", "quantity", "days")
QUANTITY_WORDS = (
    "quantity", "qty", "stock_level", "issued", "received", "ordered", "pending",
    "shortfall", "shortage", "reorder", "on_hand", "consumed",
)


def is_money(column: str) -> bool:
    name = column.lower()
    return any(w in name for w in MONEY_WORDS) and not any(w in name for w in NOT_MONEY_WORDS)


def is_quantity(column: str) -> bool:
    """A physical quantity (kg, pcs, ...), as opposed to money or a count."""
    return any(w in column.lower() for w in QUANTITY_WORDS) and not is_money(column)


def units_in(columns: list[str], rows: list[list[Any]]) -> set:
    """Distinct values of the result's unit column (empty if there is none)."""
    return {r[columns.index("unit")] for r in rows} if "unit" in columns else set()


def indian(n: float) -> str:
    """Indian digit grouping: 1234567.5 -> '12,34,567.50'."""
    sign, n = ("-", -n) if n < 0 else ("", n)
    whole, frac = divmod(round(n, 2), 1)
    digits = str(int(whole))
    head, tail = digits[:-3], digits[-3:]
    groups = [head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1]
    text = ",".join([*groups, tail]) if head else tail
    return sign + text + (f"{frac:.2f}"[1:] if frac else "")


def rupees(n: float) -> str:
    """₹ with exact lakh/crore wording."""
    if abs(n) >= 1e7:
        return f"₹{indian(n)} (₹{n / 1e7:.2f} crore)"
    if abs(n) >= 1e5:
        return f"₹{indian(n)} (₹{n / 1e5:.2f} lakh)"
    return f"₹{indian(n)}"


def display_value(value: Any, column: str) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return rupees(value) if is_money(column) else indian(value)


def summarize(columns: list[str], rows: list[list[Any]]) -> str:
    """Exact totals over all rows, so the answer writer never sums a truncated preview.
    Quantities are only totalled when every row has the same, known unit."""
    parts = [f"{len(rows)} rows"]
    units = units_in(columns, rows)
    for i, col in enumerate(columns):
        values = [r[i] for r in rows if isinstance(r[i], (int, float)) and not isinstance(r[i], bool)]
        if not values or len(values) < len(rows) * 0.5:
            continue
        part = f"{col}: min {display_value(min(values), col)} / max {display_value(max(values), col)}"
        if is_quantity(col) and len(units) != 1 and len(rows) > 1:
            part += " / no total: units differ or aren't shown"
        else:
            part += f" / sum {display_value(round(sum(values), 2), col)}"
        parts.append(part)
    if "unit" in columns:
        parts.append(f"units present: {', '.join(sorted(map(str, units)))}")
    return "; ".join(parts)
