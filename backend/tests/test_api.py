"""API contract and login tests. The agent and the user store are faked: no LLM or DB calls."""

import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.agent import service
from app.db.users import UserRecord
from app.main import app
from app.security import auth

client = TestClient(app)
CALLS = []
USERS = {
    "inventory": UserRecord("inventory", "Inventory Manager", "inventory_manager", auth.hash_password("inv-pass")),
    "owner": UserRecord("owner", "Owner", "owner", auth.hash_password("owner-pass")),
}


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    CALLS.clear()
    from app.security import rate_limit

    for limiter in (rate_limit.login_limiter, rate_limit.chat_limiter):  # each test starts with empty counters
        monkeypatch.setattr(limiter, "_hits", rate_limit.defaultdict(rate_limit.deque))

    def ask_stream(role, message, thread_id, username="eval"):
        CALLS.append((role, username, message, thread_id))
        yield "progress", {"step": "guard", "label": "Understanding your question"}
        yield "result", {"type": "text", "answer": f"{role} asked: {message}", "caveats": []}

    monkeypatch.setattr(service, "ask_stream", ask_stream)
    monkeypatch.setattr(auth, "get_user", lambda username: USERS.get(username.strip().lower()))


def token_for(username: str) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": f"{'inv' if username == 'inventory' else username}-pass"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# --- login -------------------------------------------------------------------------------


def test_login_returns_token_and_user():
    r = client.post("/api/auth/login", json={"username": "Inventory ", "password": "inv-pass"})
    body = r.json()
    assert r.status_code == 200 and body["token_type"] == "bearer"
    assert body["user"] == {"username": "inventory", "display_name": "Inventory Manager", "role": "inventory_manager"}
    claims = jwt.decode(body["access_token"], auth._secret(), algorithms=["HS256"])
    assert claims["role"] == "inventory_manager" and claims["exp"] > claims["iat"]


def test_wrong_password_and_unknown_user_get_the_same_answer():
    wrong = client.post("/api/auth/login", json={"username": "owner", "password": "nope"})
    unknown = client.post("/api/auth/login", json={"username": "ghost", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_me_returns_the_signed_in_user():
    assert client.get("/api/auth/me", headers=token_for("owner")).json()["role"] == "owner"


# --- the role comes from the token only ----------------------------------------------------


def test_chat_uses_role_from_token_not_from_body():
    r = client.post(
        "/api/chat",
        json={"thread_id": "t-1", "role": "owner", "message": "show supplier prices"},  # role in body is ignored
        headers=token_for("inventory"),
    )
    assert r.status_code == 200
    assert CALLS == [("inventory_manager", "inventory", "show supplier prices", "t-1")]


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/stream"])
def test_chat_requires_a_token(path):
    assert client.post(path, json={"thread_id": "t", "message": "x"}).status_code == 401
    assert CALLS == []


def _forged(claims: dict, secret: str | None = None) -> dict:
    token = jwt.encode(claims, secret or auth._secret(), algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer not-a-jwt"},
        _forged({"sub": "owner", "role": "owner", "exp": datetime.now(UTC) - timedelta(minutes=1)}),          # expired
        _forged({"sub": "owner", "role": "owner", "exp": datetime.now(UTC) + timedelta(hours=1)}, "x" * 40),  # wrong key
        _forged({"sub": "owner", "role": "owner"}),                                                          # no expiry
        _forged({"sub": "deleted-user", "role": "owner", "exp": datetime.now(UTC) + timedelta(hours=1)}),    # no such user
    ],
    ids=["garbage", "expired", "wrong-key", "no-exp", "unknown-user"],
)
def test_bad_tokens_are_rejected(headers):
    assert client.post("/api/chat", json={"thread_id": "t", "message": "x"}, headers=headers).status_code == 401
    assert CALLS == []


def test_role_in_token_is_overridden_by_the_stored_user():
    # A validly signed token claiming 'owner' for the inventory user still runs as inventory.
    headers = _forged({"sub": "inventory", "role": "owner", "exp": datetime.now(UTC) + timedelta(hours=1)})
    client.post("/api/chat", json={"thread_id": "t", "message": "x"}, headers=headers)
    assert CALLS[0][0] == "inventory_manager"


# --- contract --------------------------------------------------------------------------------


def test_health_reports_database_and_as_of_date():
    body = client.get("/api/health").json()
    assert body["data_as_of"] == "2026-08-21"
    assert body["status"] in ("ok", "degraded")


def test_roles_lists_three_roles_with_starter_questions():
    roles = {r["name"]: r for r in client.get("/api/roles").json()}
    assert set(roles) == {"inventory_manager", "procurement_manager", "owner"}
    assert all(r["starter_questions"] for r in roles.values())


def test_chat_returns_contract():
    body = client.post("/api/chat", json={"thread_id": "t-1", "message": "hello"}, headers=token_for("owner")).json()
    assert set(body) >= {"type", "answer", "table", "chart", "sql", "caveats", "trace_url"}


def test_chat_stream_sends_progress_then_result():
    r = client.post("/api/chat/stream", json={"thread_id": "t-2", "message": "hi"}, headers=token_for("inventory"))
    assert r.headers["content-type"].startswith("text/event-stream")
    events = r.text.strip().split("\n\n")
    assert events[0].startswith("event: progress") and events[-1].startswith("event: result")
    assert json.loads(events[-1].split("data: ", 1)[1])["answer"] == "inventory_manager asked: hi"


@pytest.mark.parametrize(
    "payload",
    [
        {"thread_id": "t", "message": ""},           # empty message
        {"thread_id": "t", "message": "x" * 2001},   # too long
        {"thread_id": "../etc", "message": "x"},     # unsafe thread id
        {"message": "x"},                            # missing thread id
    ],
)
def test_invalid_requests_are_rejected_before_the_agent(payload):
    assert client.post("/api/chat", json=payload, headers=token_for("owner")).status_code == 422
    assert CALLS == []


def test_cors_allows_the_frontend_origin_only():
    ok = client.options("/api/chat", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    bad = client.options("/api/chat", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers


# --- rate limits ------------------------------------------------------------------------------


def test_login_is_rate_limited_per_ip(monkeypatch):
    from app.security import rate_limit

    monkeypatch.setattr(rate_limit.login_limiter, "limit", 3)
    headers = {"X-Forwarded-For": "203.0.113.7"}
    codes = [client.post("/api/auth/login", json={"username": "owner", "password": "x"}, headers=headers).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    # another IP is unaffected
    other = client.post("/api/auth/login", json={"username": "owner", "password": "x"}, headers={"X-Forwarded-For": "198.51.100.1"})
    assert other.status_code == 401


def test_chat_is_rate_limited_per_user(monkeypatch):
    from app.security import rate_limit

    monkeypatch.setattr(rate_limit.chat_limiter, "limit", 2)
    headers = token_for("owner")
    codes = [client.post("/api/chat", json={"thread_id": "t", "message": "x"}, headers=headers).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# --- confirming write actions ------------------------------------------------------------------


def test_confirm_requires_a_token():
    assert client.post("/api/chat/confirm", json={"thread_id": "t", "decision": "confirm"}).status_code == 401


def test_confirm_with_nothing_pending_is_409(monkeypatch):
    def nothing(*args, **kwargs):
        raise service.NothingToConfirm()

    monkeypatch.setattr(service, "confirm", nothing)
    r = client.post("/api/chat/confirm", json={"thread_id": "t", "decision": "confirm"}, headers=token_for("owner"))
    assert r.status_code == 409


def test_confirm_passes_the_users_own_role_and_name(monkeypatch):
    seen = []
    monkeypatch.setattr(service, "confirm", lambda role, thread, decision, username: seen.append((role, username, decision))
                        or service.ChatResponse(type="text", answer="ok"))
    client.post("/api/chat/confirm", json={"thread_id": "t", "decision": "cancel", "role": "owner"}, headers=token_for("inventory"))
    assert seen == [("inventory_manager", "inventory", "cancel")]


@pytest.mark.parametrize("decision", ["yes", "", "CONFIRM", "approve"])
def test_confirm_accepts_only_confirm_or_cancel(decision):
    r = client.post("/api/chat/confirm", json={"thread_id": "t", "decision": decision}, headers=token_for("owner"))
    assert r.status_code == 422
