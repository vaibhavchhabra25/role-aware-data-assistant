"""LangGraph wiring.

guard ─┬─ data_question ──→ retrieve_context → generate_sql ─┬→ validate ─┬→ execute ─┬→ format_response
       ├─ needs_clarification → clarify                      └→ unanswerable│          │
       └─ anything else ─────→ refuse        (retry generate_sql ←──────────┴──────────┤, max 3 attempts)
                                                                            give_up ←──┘
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.nodes.actions import confirm_action, draft_action, route_after_draft
from app.agent.nodes.guard import guard
from app.agent.nodes.respond import clarify, format_response, give_up, refuse, unanswerable
from app.agent.nodes.sql import (
    execute,
    generate_sql,
    retrieve_context,
    route_after_check,
    route_after_generate,
    validate,
)
from app.agent.state import AgentState


def route_after_guard(state: AgentState) -> str:
    routes = {"data_question": "retrieve_context", "needs_clarification": "clarify", "write_request": "draft_action"}
    return routes.get(state["decision"], "refuse")


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    g = StateGraph(AgentState)
    for name, node in [
        ("guard", guard), ("retrieve_context", retrieve_context), ("generate_sql", generate_sql),
        ("validate", validate), ("execute", execute), ("format_response", format_response),
        ("refuse", refuse), ("clarify", clarify), ("unanswerable", unanswerable), ("give_up", give_up),
        ("draft_action", draft_action), ("confirm_action", confirm_action),
    ]:
        g.add_node(name, node)

    g.add_edge(START, "guard")
    g.add_conditional_edges("guard", route_after_guard, ["retrieve_context", "clarify", "refuse", "draft_action"])
    g.add_conditional_edges("draft_action", route_after_draft, {"confirm_action": "confirm_action", "done": END})
    g.add_edge("confirm_action", END)
    g.add_edge("retrieve_context", "generate_sql")
    g.add_conditional_edges("generate_sql", route_after_generate, {"validate": "validate", "unanswerable": "unanswerable"})
    g.add_conditional_edges("validate", route_after_check, {"ok": "execute", "retry": "generate_sql", "give_up": "give_up"})
    g.add_conditional_edges("execute", route_after_check, {"ok": "format_response", "retry": "generate_sql", "give_up": "give_up"})
    for terminal in ("format_response", "refuse", "clarify", "unanswerable", "give_up"):
        g.add_edge(terminal, END)
    return g.compile(checkpointer=checkpointer)
