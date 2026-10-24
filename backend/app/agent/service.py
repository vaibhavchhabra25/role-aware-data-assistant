"""Entry points used by the API and the eval runner: ask() and ask_stream()."""

import json
import logging
import os
import queue
import threading
import time
import uuid
from collections.abc import Iterator
from functools import cache

import psycopg
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from langgraph.graph.state import CompiledStateGraph
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.agent.graph import build_graph
from app.config import BACKEND_DIR, agent_state_url, database_url
from app.schemas import ChatResponse
from app.security.roles import get_role
from app.security.sql_validator import referenced_tables

log = logging.getLogger("app.requests")
REQUEST_LOG = BACKEND_DIR / "logs" / "requests.jsonl"
_lock = threading.Lock()

# Fields produced while answering one message; reset at the start of every request so
# nothing from the previous turn (SQL, rows, errors) carries over. Only messages persist.
PER_REQUEST_DEFAULTS = {
    "decision": None, "guard": {}, "standalone_question": "", "schema_context": "", "examples": [],
    "sql": None, "caveat_queries": [], "assumptions": [], "sql_error": None, "attempts": 0, "empty_rechecked": False,
    "columns": [], "rows": [], "caveats": [], "response": {},
}


@cache
def graph() -> CompiledStateGraph:
    """Compiled graph with a Postgres checkpointer in its own database (agent_state)."""
    with psycopg.connect(database_url(), autocommit=True) as admin:
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname = 'agent_state'").fetchone():
            admin.execute("CREATE DATABASE agent_state")
    pool = ConnectionPool(
        agent_state_url(), max_size=8, open=True,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    saver = PostgresSaver(pool)
    saver.setup()
    return build_graph(saver)


# What the user sees while a node runs (streamed to the UI).
STEP_LABELS = {
    "guard": "Understanding your question",
    "retrieve_context": "Finding the right data",
    "generate_sql": "Writing the query",
    "validate": "Checking the query",
    "execute": "Running the query",
    "format_response": "Preparing the answer",
    "draft_action": "Preparing the change",
    "confirm_action": "Saving the change",
}


class NothingToConfirm(Exception):
    """confirm() was called but no change is waiting for confirmation in this conversation."""
_DONE = object()


def warm_up() -> None:
    """Do the slow first-time setup at startup instead of on the first question:
    compile the graph and its checkpointer, open each role's DB pool, look up LangSmith."""
    graph()
    from app.db.execute import _pool
    from app.db.users import get_user
    from app.security.roles import load_roles

    for role in load_roles().values():
        with _pool(role.db_role).connection(timeout=5) as conn:  # fail fast if the DB isn't ready
            conn.execute("SELECT 1")
    get_user("owner")
    if os.getenv("LANGSMITH_TRACING", "").lower() == "true":
        try:
            _langsmith_project_url()
        except Exception:
            _langsmith_project_url.cache_clear()


def _config_for(role: str, username: str, thread_id: str) -> dict:
    # Threads are namespaced by user and role, so nobody's answers appear in another's history.
    return {"configurable": {"thread_id": f"{username}:{role}:{thread_id}"}}


def pending_confirmation(role: str, thread_id: str, username: str) -> dict | None:
    """The confirmation card waiting in this user's conversation, if any."""
    snapshot = graph().get_state(_config_for(get_role(role).name, username, thread_id))
    return snapshot.interrupts[0].value if snapshot.interrupts else None


def confirm(role: str, thread_id: str, decision: str, username: str) -> ChatResponse:
    """Resume a paused write action with 'confirm' or 'cancel'. Only the user who started it can."""
    if pending_confirmation(role, thread_id, username) is None:
        raise NothingToConfirm()
    result = {}
    for kind, payload in ask_stream(role, f"[{decision}]", thread_id, username, resume=decision):
        if kind == "result":
            result = payload
    return ChatResponse(**result)


def ask_stream(role: str, message: str, thread_id: str, username: str = "eval", resume: str | None = None) -> Iterator[tuple[str, dict]]:
    """Yield ("progress", {step, label}) while the graph runs, then ("result", ChatResponse dict).

    The graph runs in its own thread and hands events over a queue, so the caller
    (e.g. a web server iterating this generator from a thread pool) never resumes
    LangGraph's generator from a different thread.
    """
    role_cfg = get_role(role)  # raises for unknown roles; the role comes from the session, never the message
    events: queue.Queue = queue.Queue()
    worker = threading.Thread(target=_run, args=(role_cfg.name, message, thread_id, username, events, resume), daemon=True)
    worker.start()
    while (event := events.get()) is not _DONE:
        yield event


def ask(role: str, message: str, thread_id: str, username: str = "eval") -> ChatResponse:
    result = {}
    for kind, payload in ask_stream(role, message, thread_id, username):
        if kind == "result":
            result = payload
    return ChatResponse(**result)


def _run(role: str, message: str, thread_id: str, username: str, events: queue.Queue, resume: str | None) -> None:
    run_id = uuid.uuid4()
    usage = UsageMetadataCallbackHandler()
    config = {
        **_config_for(role, username, thread_id),
        "run_id": run_id,
        "run_name": "data-assistant",
        "tags": [role],
        "metadata": {"role": role, "user": username, "thread_id": thread_id},
        "callbacks": [usage],
    }
    start = time.perf_counter()
    state: dict = {}
    try:
        if resume is not None:
            inputs = Command(resume=resume)
        else:
            inputs = {**PER_REQUEST_DEFAULTS, "messages": [HumanMessage(message)], "role": role, "question": message, "username": username}
        for mode, chunk in graph().stream(inputs, config, stream_mode=["tasks", "values"]):
            if mode == "values":
                state = chunk
            elif "input" in chunk and chunk["name"] in STEP_LABELS:  # a node is starting
                events.put(("progress", {"step": chunk["name"], "label": STEP_LABELS[chunk["name"]]}))
        paused = graph().get_state(config).interrupts
        if paused:  # a write action is waiting for the user's confirmation
            card = paused[0].value
            response = ChatResponse(type="confirmation", answer=card["answer"], action=card["card"])
        else:
            response = ChatResponse(**state["response"])
    except Exception:
        log.exception("request failed")
        response = ChatResponse(type="error", answer="Sorry, something went wrong on my side. Please try again.")

    response.trace_url = trace_url(run_id)
    _log_request(role, username, thread_id, message, state, response, time.perf_counter() - start, usage.usage_metadata)
    events.put(("result", response.model_dump()))
    events.put(_DONE)


def _log_request(role, username, thread_id, message, state, response, seconds, usage) -> None:
    sql = response.sql
    try:
        tables = sorted(referenced_tables(sql)) if sql else []
    except Exception:
        tables = []
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "role": role,
        "user": username,
        "thread_id": thread_id,
        "question": message,
        "decision": state.get("decision"),
        "type": response.type,
        "answer": response.answer,
        "caveats": response.caveats,
        "row_count": len(response.table.rows) if response.table else 0,
        "chart": response.chart.kind if response.chart else None,
        "sql": sql,
        "tables": tables,
        "attempts": state.get("attempts", 0),
        "latency_ms": round(seconds * 1000),
        "tokens": sum(u.get("total_tokens", 0) for u in usage.values()),
        "tokens_by_model": {m: u.get("total_tokens", 0) for m, u in usage.items()},
    }
    with _lock:
        REQUEST_LOG.parent.mkdir(exist_ok=True)
        with REQUEST_LOG.open("a") as f:
            f.write(json.dumps(record) + "\n")


@cache
def _langsmith_project_url() -> str | None:
    from langsmith import Client

    project = Client().read_project(project_name=os.getenv("LANGSMITH_PROJECT", "default"))
    return project.url


def trace_url(run_id: uuid.UUID) -> str | None:
    """Link to this request's LangSmith trace, if tracing is on."""
    if os.getenv("LANGSMITH_TRACING", "").lower() != "true":
        return None
    try:
        base = _langsmith_project_url()
    except Exception:
        _langsmith_project_url.cache_clear()  # project appears after the first trace; try again next time
        return None
    return f"{base.split('?')[0]}/r/{run_id}?poll=true" if base else None
