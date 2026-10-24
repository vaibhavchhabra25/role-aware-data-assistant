"""Simple in-memory rate limits for a single server instance.

Login is limited per client IP (slows password guessing); chat is limited per user
(protects the shared OpenAI budget). Several instances would need a shared store.
"""

import os
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        """Record a hit for key, or raise 429 if it's over the limit."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                retry = int(hits[0] + self.window - now) + 1
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    f"Too many requests. Please try again in {retry} seconds.",
                    {"Retry-After": str(retry)},
                )
            hits.append(now)


login_limiter = RateLimiter(int(os.getenv("LOGIN_RATE_LIMIT", "10")), 60)  # per IP per minute
chat_limiter = RateLimiter(int(os.getenv("CHAT_RATE_LIMIT", "30")), 600)  # per user per 10 minutes


def client_ip(request: Request) -> str:
    """The caller's IP, using the first X-Forwarded-For hop when behind a proxy (Railway, Vercel)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
