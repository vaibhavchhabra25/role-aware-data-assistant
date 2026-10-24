"""HTTP API. Chat endpoints take the role from the signed-in user's JWT, never from the request."""

import json
from collections.abc import Iterator
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.agent import service
from app.config import AS_OF_DATE
from app.db.execute import run_query
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ConfirmRequest,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    RoleInfo,
    UserInfo,
)
from app.security.auth import User, authenticate, create_token, current_user
from app.security.rate_limit import chat_limiter, client_ip, login_limiter
from app.security.roles import get_role, load_roles

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> HealthResponse:
    try:
        run_query(get_role("owner"), "SELECT 1")
        database = True
    except psycopg.Error:
        database = False
    return HealthResponse(status="ok" if database else "degraded", database=database, data_as_of=AS_OF_DATE.isoformat())


@router.get("/roles")
def roles() -> list[RoleInfo]:
    return [
        RoleInfo(name=r.name, label=r.label, description=r.description, starter_questions=list(r.starter_questions))
        for r in load_roles().values()
    ]


@router.post("/auth/login")
def login(request: LoginRequest, http: Request) -> LoginResponse:
    login_limiter.check(client_ip(http))
    record = authenticate(request.username, request.password)
    if record is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password.")
    user = UserInfo(username=record.username, display_name=record.display_name, role=record.role)
    return LoginResponse(access_token=create_token(record), user=user)


@router.get("/auth/me")
def me(user: Annotated[User, Depends(current_user)]) -> UserInfo:
    return UserInfo(**user.model_dump())


@router.post("/chat")
def chat(request: ChatRequest, user: Annotated[User, Depends(current_user)]) -> ChatResponse:
    chat_limiter.check(user.username)
    return service.ask(user.role, request.message, request.thread_id, username=user.username)


@router.post("/chat/confirm")
def chat_confirm(request: ConfirmRequest, user: Annotated[User, Depends(current_user)]) -> ChatResponse:
    """Confirm or cancel the change waiting in this conversation. Only its owner can."""
    chat_limiter.check(user.username)
    try:
        return service.confirm(user.role, request.thread_id, request.decision, username=user.username)
    except service.NothingToConfirm:
        raise HTTPException(status.HTTP_409_CONFLICT, "There's no change waiting for confirmation.") from None


@router.post("/chat/stream")
def chat_stream(request: ChatRequest, user: Annotated[User, Depends(current_user)]) -> StreamingResponse:
    """Server-Sent Events: `progress` events while the agent works, then one `result` event."""
    chat_limiter.check(user.username)

    def events() -> Iterator[str]:
        for kind, payload in service.ask_stream(user.role, request.message, request.thread_id, username=user.username):
            yield f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
