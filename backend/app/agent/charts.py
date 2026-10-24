"""Chart rules: which format the user asked for, whether the data can be charted, and the chart itself."""

import re
from typing import Any

from app.agent.formatting import is_quantity, units_in
from app.agent.state import ChartChoice

MAX_CHART_POINTS = {"bar": 25, "line": 60, "pie": 6}

FORMAT_WORDS = [
    ("pie", re.compile(r"\b(pie|donut|doughnut)\b")),
    ("line", re.compile(r"\b(line (chart|graph|plot)|trend ?line|area chart)\b")),
    ("bar", re.compile(r"\b(bar|bars|column chart|histogram|stacked)\b")),
    ("table", re.compile(r"\b(as a table|in a table|table form|tabular|as a list|spreadsheet)\b")),
    ("chart", re.compile(r"\b(chart|graph|plot|visuali[sz]e|visual)\b")),
]


def requested_format(*texts: str) -> str | None:
    """The format the user explicitly asked for, if any: pie, line, bar, table, or 'chart' (any chart)."""
    text = " ".join(texts).lower()
    return next((kind for kind, pattern in FORMAT_WORDS if pattern.search(text)), None)


def _numeric_columns(columns: list[str], rows: list[list[Any]]) -> list[str]:
    return [
        c for i, c in enumerate(columns)
        if any(isinstance(r[i], (int, float)) and not isinstance(r[i], bool) for r in rows)
        and all(r[i] is None or (isinstance(r[i], (int, float)) and not isinstance(r[i], bool)) for r in rows)
    ]


def _plottable_columns(columns: list[str], rows: list[list[Any]]) -> list[str]:
    """Numeric columns that can share one axis: quantities only when every row has the same known unit."""
    one_unit = len(units_in(columns, rows)) == 1
    return [c for c in _numeric_columns(columns, rows) if one_unit or not is_quantity(c)]


def chart_blocker(columns: list[str], rows: list[list[Any]]) -> str | None:
    """Why no chart can be drawn from this result, or None if one can."""
    if len(rows) < 2:
        return "there is only one value to show"
    if not _numeric_columns(columns, rows):
        return "the result has no numbers to plot"
    if not _plottable_columns(columns, rows):
        return "the quantities are in different units (e.g. kg and pcs), which can't share one axis"
    return None


def build_chart(choice: ChartChoice | None, kind: str, columns: list[str], rows: list[list[Any]]) -> tuple[dict | None, str | None]:
    """Build a chart of the given kind from the model's choice (or sensible defaults).

    Returns (chart, note). Large pies are folded into the top 5 + "Other"; long bar charts
    keep the top 25. Returns (None, None) if the data doesn't fit.
    """
    numeric = _plottable_columns(columns, rows)
    if choice and choice.x in columns and choice.y and set(choice.y) <= set(numeric):
        x, y, title = choice.x, list(choice.y), choice.title
    else:
        labels = [c for c in columns if c not in _numeric_columns(columns, rows)]
        if not labels or not numeric:
            return None, None
        x, y, title = labels[0], [numeric[0]], ""
    if kind == "pie":
        y = y[:1]
    index = {c: i for i, c in enumerate(columns)}
    if len({r[index[x]] for r in rows}) < len(rows):
        return None, None  # e.g. month x department rows: one line over repeated months would mislead
    data = [{c: r[index[c]] for c in (x, *y)} for r in rows]

    note = None
    if kind == "pie":
        if any(d[y[0]] is None or d[y[0]] < 0 for d in data):
            return None, None
        if len(data) > MAX_CHART_POINTS["pie"]:
            data.sort(key=lambda d: d[y[0]], reverse=True)
            top, rest = data[:5], data[5:]
            data = [*top, {x: f"Other ({len(rest)})", y[0]: round(sum(d[y[0]] for d in rest), 2)}]
            note = f"The pie shows the top 5; the other {len(rest)} are grouped as Other."
    elif kind == "bar" and len(data) > MAX_CHART_POINTS["bar"]:
        data.sort(key=lambda d: d[y[0]] or 0, reverse=True)
        note = f"The chart shows the top {MAX_CHART_POINTS['bar']} of {len(data)}; the table has all of them."
        data = data[: MAX_CHART_POINTS["bar"]]
    elif kind == "line" and len(data) > MAX_CHART_POINTS["line"]:
        return None, None
    return {"kind": kind, "x": x, "y": y, "title": title, "data": data}, note
