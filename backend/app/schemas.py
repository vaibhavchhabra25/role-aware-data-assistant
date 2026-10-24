"""API contract shared by the FastAPI routes, the agent and the eval runner."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.security.roles import RoleName

ResponseType = Literal["text", "table", "chart", "refusal", "clarification", "confirmation", "error"]


class ChatRequest(BaseModel):
    """The role is not part of the request: it comes from the signed-in user's token."""

    thread_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    message: str = Field(min_length=1, max_length=2000)


class ConfirmRequest(BaseModel):
    thread_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    decision: Literal["confirm", "cancel"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class UserInfo(BaseModel):
    username: str
    display_name: str
    role: RoleName


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserInfo


class TableData(BaseModel):
    columns: list[str]
    rows: list[list[Any]]


class ChartSpec(BaseModel):
    kind: Literal["bar", "line", "pie"]
    x: str
    y: list[str]
    title: str
    data: list[dict[str, Any]]


class ChatResponse(BaseModel):
    type: ResponseType
    answer: str  # plain-language summary, always present
    table: TableData | None = None
    chart: ChartSpec | None = None
    sql: str | None = None
    caveats: list[str] = []
    options: list[str] = []  # clickable replies to a clarifying question
    action: dict[str, Any] | None = None  # confirmation card for a drafted change (type "confirmation")
    trace_url: str | None = None


class RoleInfo(BaseModel):
    name: RoleName
    label: str
    description: str
    starter_questions: list[str]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    data_as_of: str
