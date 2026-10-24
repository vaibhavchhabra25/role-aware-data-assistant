Write ONE PostgreSQL query that answers the question, using only the schema below.

$schema_context

## Examples
$examples

## Requirements
- A single SELECT (CTEs allowed). Never INSERT/UPDATE/DELETE/DDL. Never `SELECT *` or `t.*`: list columns.
- Use only the tables and columns listed above. When joining, qualify every column with its table alias (several tables share names like `unit`, `name`, `quantity`).
- Today is $as_of. Write `DATE '$as_of'`; CURRENT_DATE / NOW() are rejected.
- Return readable columns (item_code, names, po_number, warehouse) with short snake_case aliases. Never return id columns.
- Order rows meaningfully. LIMIT at most 500; for "top N" use LIMIT N.
- Group only by the dimensions the question asks for; don't add extra breakdowns (e.g. "by status" means one row per status).
- Trends: one row per period (`to_char(date_trunc('month', col), 'YYYY-MM') AS month`) in time order. Comparisons: one row per category.
- Missing values never match a filter: an unrecorded department is not "shop floor" (and the same for any field). Count such rows in a caveat instead.
- Missing data: exclude rows without a needed value and add a caveat query counting them (`SELECT count(*) ...`, caveat text contains `{n}` and uses plain business words, never column names). Group missing categories as 'Not recorded'.
- `assumptions`: at most 2, and only interpretations that change the answer (period chosen, definition used), in plain business words with no table or column names. Don't list routine filters or formulas. Usually empty.
- If the schema cannot answer the question, set `answerable` to false, leave `sql` empty and explain why.
