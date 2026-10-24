"""Decide how to handle the message, using only the role's own table summaries."""

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.context import schema_docs
from app.agent.llm import guard_model
from app.agent.prompts import render
from app.agent.state import AgentState, GuardOutput
from app.security.roles import get_role, load_roles

HISTORY_TURNS = 6  # messages of earlier conversation given to the guard for follow-ups


def guard(state: AgentState) -> dict:
    role = get_role(state["role"])
    tables = schema_docs()["tables"]
    prompt = render(
        "guard",
        role_label=role.label,
        role_description=role.description,
        table_summaries="\n".join(f"- {t}: {tables[t]['description']}" for t in sorted(role.allowed_tables)),
        other_roles="\n".join(f"- {r.name} ({r.label}): {r.description}" for r in load_roles().values() if r.name != role.name),
    )
    history = state["messages"][-(HISTORY_TURNS + 1):-1]
    conversation = "\n".join(f"{m.type}: {m.content}" for m in history) or "(none)"
    pending = state.get("pending_action")
    if pending:
        conversation += f"\n(A change is being drafted and not yet confirmed: {pending['draft']})"
    result: GuardOutput = guard_model().with_structured_output(GuardOutput).invoke([
        SystemMessage(prompt),
        HumanMessage(f"Earlier conversation:\n{conversation}\n\nLatest message:\n{state['question']}"),
    ])
    return {
        "decision": result.decision,
        "guard": result.model_dump(),
        "standalone_question": result.standalone_question or state["question"],
    }
