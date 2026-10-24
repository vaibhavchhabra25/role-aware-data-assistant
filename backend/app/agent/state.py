"""Graph state and the structured outputs the LLM nodes must return."""

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

Decision = Literal[
    "data_question", "needs_clarification", "out_of_role", "not_available", "write_request", "injection", "off_topic"
]


class AgentState(TypedDict, total=False):
    # Persisted across turns (checkpointer, keyed by thread_id)
    messages: Annotated[list[AnyMessage], add_messages]
    last_sql: str | None  # the previous successful query, so follow-ups can adjust it
    pending_action: dict[str, Any] | None  # a drafted write action ({"draft", "plan"}) awaiting details or confirmation
    username: str
    # Set per request. role comes from the session, never from the message.
    role: str
    question: str
    # Filled by nodes (reset at the start of every request)
    decision: Decision
    guard: dict[str, Any]
    standalone_question: str
    schema_context: str
    examples: list[dict[str, str]]
    sql: str | None
    caveat_queries: list[dict[str, str]]
    assumptions: list[str]
    sql_error: str | None
    attempts: int
    empty_rechecked: bool
    columns: list[str]
    rows: list[list[Any]]
    caveats: list[str]
    response: dict[str, Any]


class GuardOutput(BaseModel):
    """How the latest user message should be handled."""

    decision: Decision
    standalone_question: str = Field(
        description="The question rewritten to stand alone (follow-ups resolved from the conversation). Empty if not a data question."
    )
    reason: str = Field(description="One short sentence explaining the decision.")
    clarifying_question: str | None = Field(None, description="Only for needs_clarification: one short question.")
    clarify_options: list[str] = Field(
        default_factory=list,
        description="Only for needs_clarification: 2–4 short replies the user can click, each a complete answer to the question.",
    )
    follows_up: bool = Field(False, description="True if the latest message adjusts or builds on the previous answer.")
    other_role: Literal["inventory_manager", "procurement_manager", "owner"] | None = Field(
        None, description="Only for out_of_role: the role that can answer this."
    )


class CaveatQuery(BaseModel):
    text: str = Field(description="Plain-language caveat containing {n}, e.g. '{n} open POs have no expected date.'")
    sql: str = Field(description="SELECT count(*) ... returning a single number.")


class SQLDraft(BaseModel):
    reasoning: str = Field(description="Brief plan: tables, joins, filters, grouping.")
    answerable: bool = Field(description="False if the schema cannot answer the question.")
    sql: str = Field(description="One PostgreSQL SELECT statement. Empty if not answerable.")
    assumptions: list[str] = Field(description="Interpretations made, in plain words for a business user.")
    caveat_queries: list[CaveatQuery] = Field(description="Counts of rows excluded because of missing data.")
    explanation: str = Field("", description="If not answerable: why, in one sentence.")


class ChartChoice(BaseModel):
    kind: Literal["bar", "line", "pie"]
    x: str = Field(description="Column for the category or period axis.")
    y: list[str] = Field(description="Numeric column(s) on the same scale.")
    title: str


class FormatOutput(BaseModel):
    type: Literal["text", "table", "chart"]
    answer: str = Field(description="1–3 plain sentences with the key numbers first.")
    chart: ChartChoice | None = None
