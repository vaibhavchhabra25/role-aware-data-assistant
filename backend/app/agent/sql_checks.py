"""Business-rule checks on generated SQL (not security: that is app/security/sql_validator.py).

Rules the prompt states but the model doesn't always follow are enforced here, and a
violation is sent back to the SQL generator as a correction.
"""

import sqlglot
from sqlglot import exp

# Columns that identify one item or one unit, so summing quantities within them is safe.
ITEM_OR_UNIT_COLUMNS = {"unit", "item_id", "inv_item_id", "item_code"}


def _is_quantity(column: exp.Column) -> bool:
    name = column.name.lower()
    return ("quantity" in name or "qty" in name) and "count" not in name


def _item_names_alias(select: exp.Select) -> set[str]:
    """Aliases of inv_items in this SELECT, so `i.name` counts as identifying an item."""
    return {t.alias_or_name for t in select.find_all(exp.Table) if t.name == "inv_items"}


def _identifies_item_or_unit(expr: exp.Expression, select: exp.Select) -> bool:
    items = _item_names_alias(select)
    for col in expr.find_all(exp.Column):
        if col.name in ITEM_OR_UNIT_COLUMNS or (col.name == "name" and col.table in items):
            return True
    return False


def _group_expressions(select: exp.Select) -> list[exp.Expression]:
    group = select.args.get("group")
    if not group:
        return []
    out = []
    for e in group.expressions:
        if isinstance(e, exp.Literal) and e.is_int:  # GROUP BY 1 -> first select expression
            index = int(e.this) - 1
            if 0 <= index < len(select.expressions):
                out.append(select.expressions[index])
        else:
            out.append(e)
    return out


def mixed_unit_sum(sql: str) -> str | None:
    """Return a correction if quantities of different items/units are added together."""
    tree = sqlglot.parse_one(sql, dialect="postgres")
    for total in tree.find_all(exp.Sum):
        columns = list(total.find_all(exp.Column))
        if not any(_is_quantity(c) for c in columns):
            continue
        if any(w in c.name.lower() for c in columns for w in ("price", "cost", "amount")):
            continue  # quantity x price is money, which can be added up
        select = total.find_ancestor(exp.Select)
        if select is None:
            continue
        where = select.args.get("where")
        safe = any(_identifies_item_or_unit(g, select) for g in _group_expressions(select)) or (
            where is not None and _identifies_item_or_unit(where, select)
        )
        if not safe:
            return (
                "This query adds up quantities of different items, which are in different units (kg, pcs, ...). "
                "For a trend or comparison across many items, count movements (and distinct items) per period or "
                "category instead, or add the unit as a grouping column. For specific items, group by item_code, "
                "name and unit."
            )
    return None


MONEY_HINTS = ("price", "cost", "amount", "value", "spend")
FULL_LIST = 500  # the validator's maximum LIMIT: anything smaller is a top-N cut


def _aliases(tree: exp.Expression) -> dict[str, exp.Expression]:
    return {a.alias: a.this for a in tree.find_all(exp.Alias) if a.alias}


def _from_quantity(expr: exp.Expression, aliases: dict[str, exp.Expression], seen: frozenset[str] = frozenset()) -> bool:
    """True if expr is a quantity (directly, via an alias or a CTE column), not money."""
    columns = list(expr.find_all(exp.Column)) or ([expr] if isinstance(expr, exp.Column) else [])
    if any(h in c.name.lower() for c in columns for h in MONEY_HINTS):
        return False
    for col in columns:
        if _is_quantity(col):
            return True
        if col.name in aliases and col.name not in seen and _from_quantity(aliases[col.name], aliases, seen | {col.name}):
            return True
    return False


def _limit(select: exp.Select) -> int | None:
    limit = select.args.get("limit")
    value = limit.expression if limit else None
    return int(value.this) if isinstance(value, exp.Literal) and value.is_int else None


def _filters_to_one_unit(tree: exp.Expression) -> bool:
    """True if the query keeps a single unit anywhere (e.g. a CTE with WHERE l.unit = 'kg')."""
    for predicate in tree.find_all(exp.EQ, exp.Like, exp.ILike):
        sides = (predicate.this, predicate.expression)
        if any(isinstance(s, exp.Column) and s.name == "unit" for s in sides) and any(isinstance(s, exp.Literal) for s in sides):
            return True
    return False


def mixed_unit_ranking(sql: str) -> str | None:
    """Return a correction if items in different units are ranked against each other by quantity
    (e.g. 'top 10 by quantity' putting kg next to pcs)."""
    tree = sqlglot.parse_one(sql, dialect="postgres")
    if _filters_to_one_unit(tree):
        return None
    aliases = _aliases(tree)
    message = (
        "This query ranks items by quantity across different units (kg, pcs, ...), which can't be compared. "
        "Rank within each unit instead (e.g. ROW_NUMBER() OVER (PARTITION BY unit ORDER BY quantity DESC) and "
        "keep the top N per unit), filter to one unit, or rank by value in INR."
    )
    for select in tree.find_all(exp.Select):
        where = select.args.get("where")
        if where is not None and _identifies_item_or_unit(where, select):
            continue
        order = select.args.get("order")
        limit = _limit(select)
        if order and order.expressions and limit is not None and limit < FULL_LIST:
            first = order.expressions[0].this
            if _from_quantity(first, aliases) and not any(c.name == "unit" for c in first.find_all(exp.Column)):
                return message
        for window in select.find_all(exp.Window):
            if window.find_ancestor(exp.Select) is not select:
                continue
            ordering = window.args.get("order")
            partition = window.args.get("partition_by") or []
            by_unit = any(c.name in ITEM_OR_UNIT_COLUMNS for p in partition for c in p.find_all(exp.Column))
            if ordering and not by_unit and any(_from_quantity(o.this, aliases) for o in ordering.expressions):
                return message
    return None
