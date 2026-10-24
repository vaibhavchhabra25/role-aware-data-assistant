"""Login: bcrypt password hashes and short-lived JWTs that carry the user's role.

The role used for every query comes from a verified token, never from the request
body or the message text (fourth security layer).
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.db.users import UserRecord, get_user
from app.security.roles import RoleName

ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=False)
# Compared against when the username doesn't exist, so both failures take the same time.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode()


class User(BaseModel):
    username: str
    display_name: str
    role: RoleName


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _secret() -> str:
    secret = os.environ.get("JWT_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET must be set to a random string of at least 32 characters.")
    return secret


def authenticate(username: str, password: str) -> UserRecord | None:
    record = get_user(username)
    ok = bcrypt.checkpw(password.encode(), (record.password_hash if record else _DUMMY_HASH).encode())
    return record if record and ok else None


def create_token(user: UserRecord) -> str:
    now = datetime.now(UTC)
    ttl = timedelta(minutes=int(os.getenv("JWT_TTL_MINUTES", "480")))
    claims = {"sub": user.username, "role": user.role, "name": user.display_name, "iat": now, "exp": now + ttl}
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM)


def current_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> User:
    """FastAPI dependency: the signed-in user, or 401."""
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in again.", {"WWW-Authenticate": "Bearer"})
    if credentials is None:
        raise unauthorized
    try:
        claims = jwt.decode(credentials.credentials, _secret(), algorithms=[ALGORITHM], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        raise unauthorized from None
    # Re-read the user so a removed user or changed role takes effect immediately.
    record = get_user(claims["sub"])
    if record is None:
        raise unauthorized
    return User(username=record.username, display_name=record.display_name, role=record.role)
