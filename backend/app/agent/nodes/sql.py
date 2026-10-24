"""Retrieve context, write SQL, validate it, and run it as the user's role."""

import logging

import psycopg
from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.context import build_schema_context, select_examples
from app.agent.llm import sql_model
from app.agent.prompts import render
from app.agent.sql_checks import mixed_unit_ranking, mixed_unit_sum
from app.agent.state import AgentState, SQLDraft
from app.config import AS_OF_DATE
from app.db.execute import run_query
from app.security.roles import get_role
from app.security.sql_validator import SQLValidationError, validate_sql

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


def retrieve_context(state: AgentState) -> dict:
    role = get_role(state["role"])
    examples = select_examples(role, state["standalone_question"])
    return {
        "schema_context": build_schema_context(role),
        "examples": [
            {"question": e.question, "sql": e.sql, "caveats": [{"text": c.text, "sql": c.sql} for c in e.caveats]}
            for e in examples
        ],
    }


def _format_examples(examples: list[dict]) -> str:
    blocks = []
    for e in examples:
        block = f"Q: {e['question']}\n```sql\n{e['sql']}\n```"
        for c in e["caveats"]:
            block += f"\nCaveat query ({c['text']}):\n```sql\n{c['sql']}\n```"
        blocks.append(block)
    return "\n\n".join(blocks)


def generate_sql(state: AgentState) -> dict:
    prompt = render(
        "sql",
        schema_context=state["schema_context"],
        examples=_format_examples(state["examples"]),
        as_of=AS_OF_DATE.isoformat(),
    )
    messages = [SystemMessage(prompt), HumanMessage(state["standalone_question"])]
    if state.get("guard", {}).get("follows_up") and state.get("last_sql"):
        messages.append(HumanMessage(
            "This is a follow-up. The previous answer used this query; adjust it rather than starting over "
            "if it fits the new question. If you add a filter that drops rows the previous answer included, "
            f"add a caveat query counting them:\n```sql\n{state['last_sql']}\n```"
        ))
    if state.get("sql_error"):
        messages.append(HumanMessage(
            f"Your previous query was rejected:\n{state['sql_error']}\n\n```sql\n{state.get('sql') or ''}\n```\nWrite a corrected query."
        ))
    draft: SQLDraft = sql_model().with_structured_output(SQLDraft).invoke(messages)
    return {
        "attempts": state.get("attempts", 0) + 1,
        "sql": draft.sql.strip() if draft.answerable else None,
        "assumptions": draft.assumptions,
        "caveat_queries": [c.model_dump() for c in draft.caveat_queries],
        "sql_error": None if draft.answerable else f"NOT_ANSWERABLE: {draft.explanation}",
    }


def validate(state: AgentState) -> dict:
    role = get_role(state["role"])
    try:
        sql = validate_sql(state["sql"], role).sql
    except SQLValidationError as e:
        log.info("validator rejected SQL: %s", e)
        return {"sql_error": f"Validation failed: {e}"}

    if problem := mixed_unit_sum(sql) or mixed_unit_ranking(sql):
        log.info("business rule rejected SQL: %s", problem)
        return {"sql_error": problem}

    # Caveat counts are secondary: drop invalid ones instead of spending a retry.
    caveat_queries = []
    for c in state.get("caveat_queries", []):
        try:
            caveat_queries.append({"text": c["text"], "sql": validate_sql(c["sql"], role).sql})
        except SQLValidationError as e:
            log.info("dropped caveat query: %s", e)
    return {"sql": sql, "caveat_queries": caveat_queries, "sql_error": None}


def _db_error(e: psycopg.Error) -> str:
    """Postgres message plus its LINE/caret context, so a retry can see where it went wrong."""
    return "\n".join(str(e).splitlines()[:4])


def execute(state: AgentState) -> dict:
    role = get_role(state["role"])
    try:
        result = run_query(role, state["sql"])
    except psycopg.Error as e:
        log.info("query failed: %s", e)
        return {"sql_error": f"Database error: {_db_error(e)}"}

    # An empty result is the classic silent failure (e.g. '=' on a name that doesn't match
    # exactly). Ask for one re-check; if the corrected query is also empty, accept it.
    if not result.rows and not state.get("empty_rechecked") and state["attempts"] < MAX_ATTEMPTS:
        return {
            "empty_rechecked": True,
            "sql_error": "The query returned no rows. If it filters on names or text values, use ILIKE with "
                         "wildcards and check the values against the schema docs. If no rows is genuinely right, "
                         "return the same query.",
        }

    caveats = []
    for c in state.get("caveat_queries", []):
        try:
            n = run_query(role, c["sql"]).rows[0][0]
        except (psycopg.Error, IndexError) as e:
            log.info("caveat query failed: %s", e)
            continue
        if isinstance(n, (int, float)) and n > 0:
            caveats.append(c["text"].replace("{n}", f"{n:,}"))
    return {"columns": result.columns, "rows": result.rows, "caveats": caveats}


def route_after_generate(state: AgentState) -> str:
    if state["sql"] is None:
        return "unanswerable"
    return "validate"


def route_after_check(state: AgentState) -> str:
    if not state.get("sql_error"):
        return "ok"
    return "retry" if state["attempts"] < MAX_ATTEMPTS else "give_up"
