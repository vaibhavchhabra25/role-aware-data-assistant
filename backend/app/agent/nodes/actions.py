"""Write actions inside the graph: draft a structured action, then pause for the user's confirmation.

draft_action: the model fills in an ActionDraft; code checks the role may do it, resolves names
and validates (app/actions). confirm_action: interrupt() pauses the run and the UI shows a card;
only an explicit "confirm" resumes into execution. Every outcome is written to the audit log.
"""

import json
import logging
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt

from app.actions import ACTIONS
from app.actions.models import ActionDraft, ActionError, ActionPlan
from app.agent.llm import sql_model
from app.agent.prompts import render
from app.agent.state import AgentState
from app.config import AS_OF_DATE
from app.db import audit
from app.security.roles import get_role, load_roles

log = logging.getLogger(__name__)
CONFIRM_WITHIN_SECONDS = 15 * 60
HISTORY_TURNS = 6


def _reply(response: dict, pending: dict | None) -> dict:
    return {"response": response, "messages": [AIMessage(response["answer"])], "pending_action": pending}


def draft_action(state: AgentState) -> dict:
    role = get_role(state["role"])
    pending = state.get("pending_action") or {}
    prompt = render(
        "action",
        role_label=role.label,
        allowed_actions=", ".join(role.actions) or "none",
        as_of=AS_OF_DATE.isoformat(),
        pending_draft=json.dumps(pending.get("draft")) if pending.get("draft") else "(none)",
    )
    history = "\n".join(f"{m.type}: {m.content}" for m in state["messages"][-(HISTORY_TURNS + 1):-1]) or "(none)"
    draft: ActionDraft = sql_model().with_structured_output(ActionDraft).invoke([
        SystemMessage(prompt), HumanMessage(f"Earlier conversation:\n{history}\n\nLatest message:\n{state['question']}"),
    ])

    if draft.action == "unsupported":
        can_do = " and ".join(ACTIONS[a].label for a in role.actions)
        answer = "I can't make that change." + (f" I can help you {can_do}." if can_do else "")
        return _reply({"type": "refusal", "answer": answer, "caveats": []}, None)
    if draft.action not in role.actions:
        who = [r.label for r in load_roles().values() if draft.action in r.actions and r.name != role.name]
        answer = f"The {role.label} role can't {ACTIONS[draft.action].label}." + (f" The {' or '.join(who)} can." if who else "")
        return _reply({"type": "refusal", "answer": answer, "caveats": []}, None)

    draft_json = draft.model_dump(mode="json")
    if draft.missing:
        question = f"To {ACTIONS[draft.action].task}, I still need: {', '.join(draft.missing)}."
        return _reply({"type": "clarification", "answer": question, "caveats": []}, {"draft": draft_json})
    try:
        plan = ACTIONS[draft.action].module.plan(draft, role)
    except ActionError as e:
        kind = "clarification" if e.message.endswith("?") else "text"
        return _reply({"type": kind, "answer": e.message, "caveats": [], "options": e.options}, {"draft": draft_json})
    return {"pending_action": {"draft": draft_json, "plan": plan.model_dump()}}


def route_after_draft(state: AgentState) -> str:
    return "confirm_action" if (state.get("pending_action") or {}).get("plan") and not state.get("response") else "done"


def confirm_action(state: AgentState) -> dict:
    role = get_role(state["role"])
    username = state.get("username", "unknown")
    plan = ActionPlan(**state["pending_action"]["plan"])
    decision = interrupt({"card": plan.card(), "answer": "Please check the details below and confirm."})

    if decision != "confirm":
        audit.record(username, role.name, plan.action, "cancelled", plan.payload)
        return _reply({"type": "text", "answer": "Cancelled. Nothing was changed.", "caveats": []}, None)
    if time.time() - plan.created > CONFIRM_WITHIN_SECONDS:
        audit.record(username, role.name, plan.action, "expired", plan.payload)
        return _reply({"type": "text", "answer": "That confirmation expired, so nothing was changed. Please ask again.", "caveats": []}, None)
    if plan.action not in role.actions:  # re-check at execution time
        audit.record(username, role.name, plan.action, "failed", plan.payload, error="not permitted")
        return _reply({"type": "refusal", "answer": "Your role can't make this change.", "caveats": []}, None)

    module = ACTIONS[plan.action].module
    try:
        result = module.execute(plan, role, username)
    except ActionError as e:
        audit.record(username, role.name, plan.action, "failed", plan.payload, error=e.message)
        return _reply({"type": "text", "answer": e.message, "caveats": []}, None)
    except Exception as e:
        log.exception("write action failed")
        audit.record(username, role.name, plan.action, "failed", plan.payload, error=str(e)[:500])
        return _reply({"type": "error", "answer": "Sorry, the change couldn't be saved. Nothing was changed.", "caveats": []}, None)
    audit.record(username, role.name, plan.action, "executed", plan.payload, result)
    return _reply({"type": "text", "answer": module.done_message(result, plan), "caveats": []}, None)
