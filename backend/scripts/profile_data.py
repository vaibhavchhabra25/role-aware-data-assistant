"""Profile data quality in the source tables.

Usage: python backend/scripts/profile_data.py  ->  docs/data_quality.md

Generic checks (nulls/blanks, declared FK orphans, negatives, future dates) run
over every table. Business-specific checks are listed explicitly below because
most relationships in this schema are not backed by FK constraints.
"""

from psycopg import Connection, sql

from source_db import DOCS_DIR, SOURCE_TABLES, connect

# Placeholder strings the app writes instead of NULL.
BLANK_VALUES = ("", "{}", "[]")

# (child_table, child_column, parent_table) relationships with no FK constraint.
IMPLICIT_REFS = [
    ("inv_current_stock", "item_id", "inv_items"),
    ("inv_transactions", "item_id", "inv_items"),
    ("inv_transactions", "transfer_location_id", "inv_locations"),
    ("inv_transactions", "issued_to_id", "inv_issued_to_targets"),
    ("proc_po_lines", "inv_item_id", "inv_items"),
    ("proc_po_receipts", "location_id", "inv_locations"),
    ("proc_po_payment_tranches", "po_id", "proc_purchase_orders"),
    ("proc_purchase_orders", "delivery_location_id", "inv_locations"),
    ("proc_unexpected_receipts", "inv_item_id", "inv_items"),
    ("proc_unexpected_receipts", "location_id", "inv_locations"),
]

DUPLICATE_KEYS = [
    ("inv_items", "item_code"),
    ("inv_items", "lower(trim(name))"),
    ("proc_vendors", "lower(trim(name))"),
    ("proc_purchase_orders", "po_number"),
]

CATEGORICAL_COLUMNS = [
    ("inv_items", "status"),
    ("inv_items", "unit"),
    ("inv_transactions", "transaction_type"),
    ("inv_transactions", "reference_type"),
    ("proc_purchase_orders", "status"),
    ("proc_po_lines", "unit"),
    ("proc_po_payment_tranches", "due_basis"),
    ("proc_po_payment_tranches", "source"),
]

BUSINESS_RULES = [
    (
        "Stock rows where max level < min level, or both are 0 (no reorder level set)",
        """SELECT count(*) FILTER (WHERE max_stock_level < min_stock_level),
                  count(*) FILTER (WHERE min_stock_level = 0 AND max_stock_level = 0)
           FROM inv_current_stock""",
    ),
    (
        "Stock rows below min level (excluding rows with no min level)",
        "SELECT count(*) FROM inv_current_stock WHERE min_stock_level > 0 AND quantity < min_stock_level",
    ),
    (
        "Current stock that does not match the sum of transactions (item, location)",
        """WITH tx AS (
               SELECT item_id, location_id, sum(quantity) AS qty
               FROM inv_transactions GROUP BY item_id, location_id)
           SELECT count(*) FROM inv_current_stock s
           LEFT JOIN tx USING (item_id, location_id)
           WHERE abs(s.quantity - coalesce(tx.qty, 0)) > 0.001""",
    ),
    (
        "Transactions whose quantity sign disagrees with the type (inbound < 0 or outbound > 0)",
        """SELECT transaction_type, count(*) FROM inv_transactions
           WHERE (transaction_type = 'inbound' AND quantity < 0)
              OR (transaction_type = 'outbound' AND quantity > 0)
           GROUP BY 1""",
    ),
    (
        "PO lines received more than ordered (over-receipt)",
        "SELECT count(*) FROM proc_po_lines WHERE quantity_received > quantity_ordered",
    ),
    (
        "PO lines where subtotal + tax != total",
        "SELECT count(*) FROM proc_po_lines WHERE abs(line_subtotal + line_tax - line_total) > 0.01",
    ),
    (
        "POs whose header total != sum of line totals",
        """SELECT count(*) FROM proc_purchase_orders po
           JOIN (SELECT po_id, sum(line_total) AS lines FROM proc_po_lines GROUP BY po_id) l
             ON l.po_id = po.id
           WHERE abs(po.total_amount - l.lines) > 0.01""",
    ),
    (
        "POs with no lines",
        "SELECT count(*) FROM proc_purchase_orders po WHERE NOT EXISTS (SELECT 1 FROM proc_po_lines l WHERE l.po_id = po.id)",
    ),
    (
        "POs missing placed_on / expected delivery date / payment terms",
        """SELECT count(*) FILTER (WHERE placed_on IS NULL),
                  count(*) FILTER (WHERE expected_delivery_date IS NULL
                                     OR json_array_length(expected_delivery_date) = 0),
                  count(*) FILTER (WHERE payment_terms IS NULL OR trim(payment_terms) = '')
           FROM proc_purchase_orders""",
    ),
    (
        "POs with more than one expected delivery date (revised dates)",
        """SELECT count(*) FROM proc_purchase_orders
           WHERE json_typeof(expected_delivery_date) = 'array' AND json_array_length(expected_delivery_date) > 1""",
    ),
    (
        "Open POs past their latest expected delivery date (overdue), as of today",
        """SELECT count(*) FROM proc_purchase_orders
           WHERE status NOT IN ('closed', 'cancelled', 'received')
             AND json_typeof(expected_delivery_date) = 'array'
             AND json_array_length(expected_delivery_date) > 0
             AND (expected_delivery_date ->> (json_array_length(expected_delivery_date) - 1))::date < current_date""",
    ),
    (
        "Expected delivery earlier than PO placed date",
        """SELECT count(*) FROM proc_purchase_orders
           WHERE json_typeof(expected_delivery_date) = 'array'
             AND json_array_length(expected_delivery_date) > 0
             AND (expected_delivery_date ->> 0)::date < placed_on""",
    ),
    (
        "Payment tranches per PO that do not add up to 100%",
        """SELECT count(*) FROM (SELECT po_id FROM proc_po_payment_tranches
                                 GROUP BY po_id HAVING abs(sum(percentage) - 100) > 0.01) t""",
    ),
    (
        "Unexpected receipts not yet resolved to a PO",
        "SELECT count(*) FILTER (WHERE resolved_po_id IS NULL), count(*) FROM proc_unexpected_receipts",
    ),
    (
        "Items without a category / sub-category / HSN code",
        """SELECT count(*) FILTER (WHERE category_id IS NULL),
                  count(*) FILTER (WHERE sub_category_id IS NULL),
                  count(*) FILTER (WHERE hsn_code IS NULL OR trim(hsn_code) = '')
           FROM inv_items""",
    ),
    (
        "Items with no stock row at any location",
        "SELECT count(*) FROM inv_items i WHERE NOT EXISTS (SELECT 1 FROM inv_current_stock s WHERE s.item_id = i.id)",
    ),
    (
        "Vendors missing code / city / payment terms",
        """SELECT count(*) FILTER (WHERE code IS NULL),
                  count(*) FILTER (WHERE city IS NULL OR trim(city) = ''),
                  count(*) FILTER (WHERE payment_terms IS NULL OR trim(payment_terms) = '')
           FROM proc_vendors""",
    ),
    (
        "Rows flagged is_synthetic (stock, transactions)",
        """SELECT (SELECT count(*) FROM inv_current_stock WHERE is_synthetic),
                  (SELECT count(*) FROM inv_transactions WHERE is_synthetic)""",
    ),
]

# Event dates to check for monthly coverage gaps.
ACTIVITY_DATES = [
    ("inv_transactions", "created_at"),
    ("proc_purchase_orders", "placed_on"),
    ("proc_po_receipts", "received_at"),
    ("proc_unexpected_receipts", "received_at"),
]

TEXT_TYPES = {"character varying", "text"}
NUMERIC_TYPES = {"numeric", "double precision", "integer", "bigint", "real", "smallint"}
DATE_TYPES = {"date", "timestamp without time zone", "timestamp with time zone"}


def md_table(headers: list[str], rows: list[list[object]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in rows]
    return lines + [""]


def column_types(conn: Connection, table: str) -> list[tuple[str, str]]:
    return conn.execute(
        """SELECT column_name, data_type FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position""",
        (table,),
    ).fetchall()


def missing_values(conn: Connection) -> list[str]:
    """Per column: NULL count and placeholder-blank count. Only columns with any missing values are listed."""
    rows = []
    for table in SOURCE_TABLES:
        cols = column_types(conn, table)
        exprs = [sql.SQL("count(*)")]
        for name, typ in cols:
            col = sql.Identifier(name)
            exprs.append(sql.SQL("count(*) FILTER (WHERE {} IS NULL)").format(col))
            if typ in TEXT_TYPES:
                exprs.append(sql.SQL("count(*) FILTER (WHERE trim({}) = ANY({}))").format(col, sql.Literal(list(BLANK_VALUES))))
            else:
                exprs.append(sql.SQL("0"))
        query = sql.SQL("SELECT {} FROM {}").format(sql.SQL(", ").join(exprs), sql.Identifier(table))
        total, *counts = conn.execute(query).fetchone()
        for i, (name, _) in enumerate(cols):
            nulls, blanks = counts[2 * i], counts[2 * i + 1]
            if nulls or blanks:
                pct = round(100 * (nulls + blanks) / total, 1) if total else 0
                rows.append([table, name, total, nulls, blanks, f"{pct}%"])
    return ["## Missing values", "", f"Blank = one of {list(BLANK_VALUES)} after trimming.", ""] + md_table(
        ["Table", "Column", "Rows", "NULL", "Blank", "Missing %"], rows
    )


def orphan_references(conn: Connection) -> list[str]:
    declared = conn.execute(
        """SELECT cl.relname, a.attname, pcl.relname
           FROM pg_constraint c
           JOIN pg_class cl ON cl.oid = c.conrelid
           JOIN pg_class pcl ON pcl.oid = c.confrelid
           JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
           WHERE c.contype = 'f' AND cl.relname = ANY(%s)""",
        (SOURCE_TABLES,),
    ).fetchall()
    rows = []
    for (child, col, parent), kind in [(r, "declared FK") for r in declared] + [(r, "implicit") for r in IMPLICIT_REFS]:
        query = sql.SQL(
            "SELECT count(*) FROM {child} c WHERE c.{col} IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE p.id = c.{col})"
        ).format(child=sql.Identifier(child), col=sql.Identifier(col), parent=sql.Identifier(parent))
        rows.append([f"{child}.{col}", parent, kind, conn.execute(query).fetchone()[0]])
    return ["## Orphan references", ""] + md_table(["Reference", "Parent", "Kind", "Orphans"], rows)


def duplicate_keys(conn: Connection) -> list[str]:
    rows = []
    for table, expr in DUPLICATE_KEYS:
        query = sql.SQL(
            "SELECT k, count(*) FROM (SELECT {expr} AS k FROM {t}) x WHERE k IS NOT NULL GROUP BY k HAVING count(*) > 1 ORDER BY 2 DESC"
        ).format(expr=sql.SQL(expr), t=sql.Identifier(table))
        dupes = conn.execute(query).fetchall()
        examples = ", ".join(f"`{k}` ×{n}" for k, n in dupes[:5])
        rows.append([table, f"`{expr}`", len(dupes), examples])
    return ["## Duplicate keys", "", "Primary keys are enforced; these are natural keys without a unique constraint.", ""] + md_table(
        ["Table", "Key", "Duplicated values", "Examples"], rows
    )


def impossible_values(conn: Connection) -> list[str]:
    rows = []
    for table in SOURCE_TABLES:
        for name, typ in column_types(conn, table):
            col, t = sql.Identifier(name), sql.Identifier(table)
            if typ in NUMERIC_TYPES:
                check, label = sql.SQL("{} < 0").format(col), "negative"
            elif typ in DATE_TYPES:
                check, label = sql.SQL("{}::date > current_date").format(col), "future date"
            else:
                continue
            n = conn.execute(sql.SQL("SELECT count(*) FROM {} WHERE {}").format(t, check)).fetchone()[0]
            if n:
                rows.append([table, name, label, n])
    return ["## Impossible values", "", "Negative numbers and dates after today.", ""] + md_table(
        ["Table", "Column", "Issue", "Rows"], rows
    )


def business_rules(conn: Connection) -> list[str]:
    out = ["## Business-rule checks", ""]
    for title, query in BUSINESS_RULES:
        result = conn.execute(query).fetchall()
        value = "; ".join(", ".join(str(v) for v in row) for row in result) or "0"
        out.append(f"- {title}: **{value}**")
    return out + [""]


def categorical_values(conn: Connection) -> list[str]:
    out = ["## Categorical values", ""]
    for table, col in CATEGORICAL_COLUMNS:
        query = sql.SQL("SELECT {c}, count(*) FROM {t} GROUP BY 1 ORDER BY 2 DESC").format(
            c=sql.Identifier(col), t=sql.Identifier(table)
        )
        values = ", ".join(f"`{v}` ({n})" for v, n in conn.execute(query).fetchall())
        out.append(f"- `{table}.{col}`: {values}")
    return out + [""]


def date_coverage(conn: Connection) -> list[str]:
    out = ["## Date coverage", ""]
    for table, col in ACTIVITY_DATES:
        query = sql.SQL(
            """WITH m AS (SELECT date_trunc('month', {c})::date AS month, count(*) AS n FROM {t}
                          WHERE {c} IS NOT NULL GROUP BY 1),
                    span AS (SELECT generate_series(min(month), max(month), interval '1 month')::date AS month FROM m)
               SELECT to_char(span.month, 'YYYY-MM'), coalesce(m.n, 0) FROM span LEFT JOIN m USING (month) ORDER BY 1"""
        ).format(c=sql.Identifier(col), t=sql.Identifier(table))
        months = conn.execute(query).fetchall()
        gaps = [m for m, n in months if n == 0]
        per_month = ", ".join(f"{m}: {n}" for m, n in months)
        out.append(f"- `{table}.{col}`: {per_month}" + (f" — **gaps: {', '.join(gaps)}**" if gaps else ""))
    return out + [""]


def main() -> None:
    lines = [
        "# Data quality",
        "",
        "Generated by `backend/scripts/profile_data.py` from the company-provided DB (read-only).",
        "",
    ]
    with connect() as conn:
        today = conn.execute("SELECT current_date").fetchone()[0]
        lines += [f"Profiled on {today}.", ""]
        for section in (
            missing_values,
            orphan_references,
            duplicate_keys,
            impossible_values,
            business_rules,
            categorical_values,
            date_coverage,
        ):
            lines += section(conn)

    (DOCS_DIR / "data_quality.md").write_text("\n".join(lines))
    print(f"Wrote {DOCS_DIR / 'data_quality.md'}")


if __name__ == "__main__":
    main()
