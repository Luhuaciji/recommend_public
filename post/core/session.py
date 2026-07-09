from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from typing import Any, Dict, Optional

from .redis_client import (
    get_redis_client,
    get_redis_key_prefix,
    get_redis_lock_retry_seconds,
    get_redis_lock_ttl_seconds,
    get_redis_lock_wait_seconds,
    get_session_store_mode,
    get_session_ttl_seconds,
)
from .session_serialization import dumps_session, loads_session


_memory_sessions: Dict[str, Dict[str, Any]] = {}
_memory_locks = defaultdict(asyncio.Lock)
_STORE_MODE = get_session_store_mode()
if _STORE_MODE not in {"memory", "redis"}:
    raise RuntimeError("SESSION_STORE must be either 'memory' or 'redis'.")


def _using_redis() -> bool:
    return _STORE_MODE == "redis"


def _session_key(conversation_id: str) -> str:
    return f"{get_redis_key_prefix()}:session:{conversation_id}"


def _lock_key(conversation_id: str) -> str:
    return f"{get_redis_key_prefix()}:lock:{conversation_id}"


def _ttl_seconds() -> int:
    return get_session_ttl_seconds()


class _RedisSessionLock:
    _RELEASE_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    end
    return 0
    """

    def __init__(self, conversation_id: str):
        self._key = _lock_key(conversation_id)
        self._token = uuid.uuid4().hex

    async def __aenter__(self):
        client = get_redis_client()
        deadline = time.monotonic() + get_redis_lock_wait_seconds()
        retry_seconds = get_redis_lock_retry_seconds()
        ttl_seconds = get_redis_lock_ttl_seconds()

        while True:
            acquired = await asyncio.to_thread(
                client.set,
                self._key,
                self._token,
                nx=True,
                ex=ttl_seconds,
            )
            if acquired:
                return self
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for Redis session lock: {self._key}")
            await asyncio.sleep(retry_seconds)

    async def __aexit__(self, exc_type, exc, tb):
        client = get_redis_client()
        await asyncio.to_thread(client.eval, self._RELEASE_SCRIPT, 1, self._key, self._token)
        return False


class _SessionLockRegistry:
    def __getitem__(self, conversation_id: str):
        if _using_redis():
            return _RedisSessionLock(conversation_id)
        return _memory_locks[conversation_id]


session_locks = _SessionLockRegistry()


def get_session(conversation_id: str) -> Optional[Dict[str, Any]]:
    if _using_redis():
        payload = get_redis_client().get(_session_key(conversation_id))
        if not payload:
            return None
        session = loads_session(payload)
    else:
        session = _memory_sessions.get(conversation_id)

    if session and session.get("expire_at", 0) < time.time():
        delete_session(conversation_id)
        return None
    return session


def create_session(user_id: str, initial_history: list) -> Dict[str, Any]:
    initial_turn_count = sum(
        1
        for msg in initial_history
        if isinstance(msg, dict) and msg.get("role") == "user"
    )
    return {
        "user_id": user_id,
        "collected_slots": {},
        "llm_history": initial_history,
        "dialog_turn_count": initial_turn_count,
        "dialog_limit_reached": False,
        "expire_at": time.time() + _ttl_seconds(),
        "interrupted_ids": set(),
        "lastFrontendChatHistoriesSnapshot": "[]",
        "lastUserTaskId": "",
    }


def save_session(conversation_id: str, session: Dict[str, Any]) -> None:
    session["expire_at"] = time.time() + _ttl_seconds()
    if "interrupted_ids" not in session:
        session["interrupted_ids"] = set()

    if _using_redis():
        get_redis_client().set(
            _session_key(conversation_id),
            dumps_session(session),
            ex=_ttl_seconds(),
        )
        return

    _memory_sessions[conversation_id] = session


def delete_session(conversation_id: str) -> None:
    if _using_redis():
        get_redis_client().delete(_session_key(conversation_id))
        return

    _memory_sessions.pop(conversation_id, None)


def mark_interrupted(conversation_id: str, task_id: str) -> bool:
    session = get_session(conversation_id)
    if not session:
        return False
    if "interrupted_ids" not in session:
        session["interrupted_ids"] = set()
    session["interrupted_ids"].add(task_id)
    save_session(conversation_id, session)
    return True


def is_interrupted(conversation_id: str, task_id: str) -> bool:
    session = get_session(conversation_id)
    if not session:
        return False
    interrupted_ids = session.get("interrupted_ids", set())
    if isinstance(interrupted_ids, list):
        interrupted_ids = set(interrupted_ids)
    return task_id in interrupted_ids
