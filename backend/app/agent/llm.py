"""Chat models and a small on-disk cache for development.

The cache key is the full prompt plus model parameters, so editing a prompt, the
schema docs or an example automatically misses the cache: evals can't pass on
stale answers.
"""

import pickle
import sqlite3
import threading
from collections.abc import Sequence
from functools import cache
from pathlib import Path

from langchain_core.caches import BaseCache
from langchain_core.globals import set_llm_cache
from langchain_core.outputs import Generation
from langchain_openai import ChatOpenAI

from app.config import FAST_MODEL, GUARD_MODEL, LLM_API_KEY, LLM_BASE_URL, LLM_CACHE_PATH, SQL_MODEL


class SQLiteLLMCache(BaseCache):
    """Local dev cache. Uses pickle because structured outputs carry Pydantic objects;
    the file is written and read only by this process on this machine."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS cache (prompt TEXT, llm TEXT, value BLOB, PRIMARY KEY (prompt, llm))")
        self._lock = threading.Lock()

    def lookup(self, prompt: str, llm_string: str) -> Sequence[Generation] | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM cache WHERE prompt = ? AND llm = ?", (prompt, llm_string)).fetchone()
        return pickle.loads(row[0]) if row else None

    def update(self, prompt: str, llm_string: str, return_val: Sequence[Generation]) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (prompt, llm_string, pickle.dumps(list(return_val))))

    def clear(self, **kwargs) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM cache")


if LLM_CACHE_PATH:
    set_llm_cache(SQLiteLLMCache(LLM_CACHE_PATH))


def _chat(model: str, timeout: int) -> ChatOpenAI:
    return ChatOpenAI(model=model, temperature=0, timeout=timeout, max_retries=2, base_url=LLM_BASE_URL, api_key=LLM_API_KEY)


@cache
def fast_model() -> ChatOpenAI:
    return _chat(FAST_MODEL, 30)


@cache
def guard_model() -> ChatOpenAI:
    return _chat(GUARD_MODEL, 30)


@cache
def sql_model() -> ChatOpenAI:
    return _chat(SQL_MODEL, 60)
