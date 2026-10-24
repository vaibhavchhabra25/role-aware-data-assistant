"""Turn the outcome into the API response: formatted data, refusal, clarification or error."""

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage

from app.agent.charts import build_chart, chart_blocker, requested_format
from app.agent.formatting import display_value, summarize
from app.agent.llm import fast_model
from app.agent.prompts import render
from app.agent.state import AgentState, FormatOutput
from app.config import AS_OF_DATE
from app.security.roles import RoleConfig, get_role

log = logging.getLogger(__name__)
ROWS_SHOWN_TO_FORMATTER = 30
MAX_TEXT_ROWS = 2
CHART_KINDS = ("pie", "line", "bar")


def _reply(response: dict[str, Any]) -> dict:
    """Store the response and remember the answer in the conversation for follow-ups."""
    return {"response": response, "messages": [AIMessage(response["answer"])]}


def suggestions(role: RoleConfig, n: int = 3) -> list[str]:
    """The role's curated starter questions (same as the UI shows)."""
    return list(role.starter_questions[:n])


def format_response(state: AgentState) -> dict:
    columns, rows = state["columns"], state["rows"]
    requested = requested_format(state["question"], state["standalone_question"])
    blocker = chart_blocker(columns, rows)
    preview = "\n".join(
        json.dumps([display_value(v, c) for v, c in zip(r, columns)], ensure_ascii=False, default=str)
        for r in rows[:ROWS_SHOWN_TO_FORMATTER]
    )
    if len(rows) > ROWS_SHOWN_TO_FORMATTER:
        preview += f"\n... {len(rows) - ROWS_SHOWN_TO_FORMATTER} more rows"
    prompt = render(
        "format",
        as_of=AS_OF_DATE.strftime("%-d %b %Y"),
        question=state["standalone_question"],
        row_count=len(rows),
        columns=", ".join(columns),
        rows=preview or "(no rows)",
        summary=summarize(columns, rows),
        assumptions="; ".join(state.get("assumptions", [])) or "none",
        caveats="; ".join(state.get("caveats", [])) or "none",
        requested=requested or "none",
        charts=f"not possible, because {blocker}" if blocker else "possible (bar, line or pie)",
    )
    out: FormatOutput = fast_model().with_structured_output(FormatOutput).invoke(prompt)

    notes: list[str] = []
    chart = None
    wants_any_chart = requested in (*CHART_KINDS, "chart")
    # Unrequested charts need at least 3 points; two numbers read better as a sentence or table.
    if (wants_any_chart or (requested is None and out.type == "chart" and len(rows) >= 3)) and not blocker:
        kind = requested if requested in CHART_KINDS else (out.chart.kind if out.chart else "bar")
        chart, note = build_chart(out.chart, kind, columns, rows)
        if note:
            notes.append(note)
        if chart is None:
            log.info("chart not drawn: %s (columns=%s, rows=%d)", out.chart, columns, len(rows))
    if wants_any_chart and chart is None:
        notes.append(f"Shown as a table because {blocker or 'this result does not fit that chart'}.")

    if chart:
        kind = "chart"
    elif requested == "table" and rows:
        kind = "table"
    elif not rows or (out.type == "text" and len(rows) <= MAX_TEXT_ROWS):
        kind = "text"
    else:
        kind = "table"  # lists never collapse into a sentence
    caveats = [f"Assumed: {a}" for a in state.get("assumptions", [])] + state.get("caveats", []) + notes
    reply = _reply({
        "type": kind,
        "answer": out.answer,
        "table": {"columns": columns, "rows": rows} if rows else None,
        "chart": chart,
        "sql": state["sql"],
        "caveats": caveats,
    })
    return {**reply, "last_sql": state["sql"]}


def refuse(state: AgentState) -> dict:
    role = get_role(state["role"])
    guard = state.get("guard", {})
    decision = state["decision"]
    if decision == "out_of_role":
        other = guard.get("other_role")
        who = f" The {get_role(other).label} can see it." if other and other != role.name else ""
        answer = f"That information isn't available to the {role.label} role.{who}"
    elif decision == "not_available":
        answer = f"I can't answer that from the company's data. {guard.get('reason', '')}".strip()
    elif decision == "write_request":
        answer = "I can only look up information. I can't create, change or delete records."
    elif decision == "injection":
        answer = (f"I can't do that. I answer questions using only the data the {role.label} role can see, "
                  "and I can't change roles or run custom queries.")
    else:  # off_topic
        answer = "I can only help with questions about the company's stock, purchasing and supplier data."
    already_suggested = any("You could ask, for example" in str(m.content) for m in state.get("messages", []) if m.type == "ai")
    if not already_suggested:
        answer += " You could ask, for example: " + " / ".join(f"“{q}”" for q in suggestions(role))
    return _reply({"type": "refusal", "answer": answer, "caveats": []})


def clarify(state: AgentState) -> dict:
    guard = state.get("guard", {})
    question = guard.get("clarifying_question") or "Could you say a bit more about what you'd like to see?"
    return _reply({
        "type": "clarification", "answer": question, "caveats": [],
        "options": clarify_options(guard.get("clarify_options", []), get_role(state["role"])),
    })


def clarify_options(options: list[str], role: RoleConfig) -> list[str]:
    """At most 4 options. When they offer units (kg, pieces...), roles that can see purchase
    values also get 'By amount spent', the one measure comparable across all items."""
    offers_units = any(w in " ".join(options).lower() for w in ("kg", "piece", "pcs", "meter", "metre", "litre", "liter"))
    money = [o for o in options if any(w in o.lower() for w in ("amount", "spend", "spent", "value", "₹", "inr", "cost"))]
    if offers_units and "proc_po_lines" in role.allowed_tables:
        others = [o for o in options if o not in money]
        return [*others[:3], money[0] if money else "By amount spent"]  # keep the money option even if listed last
    return options[:4]


def unanswerable(state: AgentState) -> dict:
    reason = (state.get("sql_error") or "").removeprefix("NOT_ANSWERABLE: ")
    answer = f"I can't answer that from the data available to you. {reason}".strip()
    return _reply({"type": "refusal", "answer": answer, "caveats": []})


def give_up(state: AgentState) -> dict:
    return _reply({
        "type": "error",
        "answer": "Sorry, I couldn't build a working query for that. Could you rephrase it or make it more specific?",
        "caveats": [],
    })
