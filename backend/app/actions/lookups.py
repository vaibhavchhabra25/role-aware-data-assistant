"""Resolve names the user typed (supplier, item, warehouse) to exactly one record.

Lookups run under the user's own read-only role, so a role can only refer to records it may see.
"""

from app.actions.models import ActionError
from app.db.execute import run_query
from app.security.roles import RoleConfig

MAX_OPTIONS = 4


def _pattern(text: str) -> str:
    """'ss sheet 304 1250x2500' -> '%ss%sheet%304%1250%2500%' (also matches ×, –, extra spaces)."""
    words = [w for w in text.lower().replace("×", " ").replace("x", " ").replace("–", " ").replace("-", " ").split() if w]
    return "%" + "%".join(words) + "%"


def _one(rows: list[list], what: str, typed: str) -> list:
    if len(rows) == 1:
        return rows[0]
    if not rows:
        raise ActionError(f"I couldn't find a {what} matching “{typed}”. Could you check the name?")
    options = [r[1] for r in rows[:MAX_OPTIONS]]
    more = f" ({len(rows)} match)" if len(rows) > MAX_OPTIONS else ""
    raise ActionError(f"Which {what} do you mean{more}?", options)


def supplier(role: RoleConfig, typed: str) -> dict:
    rows = run_query(
        role,
        """SELECT id, name, payment_terms FROM proc_vendors
           WHERE is_active AND (lower(trim(name)) = lower(trim(%(t)s)) OR name ILIKE %(p)s) ORDER BY name""",
        {"t": typed, "p": _pattern(typed)},
    ).rows
    exact = [r for r in rows if r[1].strip().lower() == typed.strip().lower()]
    vid, name, terms = _one(exact or rows, "supplier", typed)
    return {"id": vid, "name": name, "payment_terms": terms}


def item(role: RoleConfig, typed: str) -> dict:
    rows = run_query(
        role,
        """SELECT id, trim(name), item_code, unit FROM inv_items
           WHERE status = 'active' AND (item_code = upper(trim(%(t)s)) OR lower(trim(name)) = lower(trim(%(t)s))
                                        OR translate(name, '×–', 'x-') ILIKE %(p)s)
           ORDER BY item_code""",
        {"t": typed, "p": _pattern(typed)},
    ).rows
    exact = [r for r in rows if r[2] == typed.strip().upper() or r[1].lower() == typed.strip().lower()]
    iid, name, code, unit = _one(exact or rows, "item", typed)
    return {"id": iid, "name": name, "item_code": code, "unit": unit}


def warehouse(role: RoleConfig, typed: str) -> dict:
    rows = run_query(
        role, "SELECT id, name FROM inv_locations WHERE is_active AND name ILIKE %(p)s ORDER BY name",
        {"p": f"%{typed.strip()}%"},
    ).rows
    exact = [r for r in rows if r[1].lower() == typed.strip().lower()]
    lid, name = _one(exact or rows, "warehouse", typed)
    return {"id": lid, "name": name}
