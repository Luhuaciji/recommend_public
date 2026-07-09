from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict

try:
    import redis
except ImportError:  # pragma: no cover - handled when redis mode is enabled.
    redis = None


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None else default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "true" if default else "false")
    return raw.lower() in {"1", "true", "yes", "on"}


def get_session_store_mode() -> str:
    return _env("SESSION_STORE", "memory").strip().lower()


def get_redis_key_prefix() -> str:
    return _env("REDIS_KEY_PREFIX", "product-recommend:post").strip().rstrip(":")


def get_session_ttl_seconds() -> int:
    return max(1, int(_env("SESSION_TTL_SECONDS", "1800")))


def get_redis_lock_ttl_seconds() -> int:
    return max(1, int(_env("REDIS_LOCK_TTL_SECONDS", "60")))


def get_redis_lock_wait_seconds() -> float:
    return max(0.1, float(_env("REDIS_LOCK_WAIT_SECONDS", "10")))


def get_redis_lock_retry_seconds() -> float:
    return max(0.01, float(_env("REDIS_LOCK_RETRY_SECONDS", "0.05")))


def _build_redis_kwargs() -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "host": _env("REDIS_HOST", "127.0.0.1"),
        "port": int(_env("REDIS_PORT", "6379")),
        "db": int(_env("REDIS_DB", "0")),
        "decode_responses": True,
        "socket_timeout": float(_env("REDIS_SOCKET_TIMEOUT", "5")),
        "socket_connect_timeout": float(_env("REDIS_CONNECT_TIMEOUT", "5")),
        "health_check_interval": int(_env("REDIS_HEALTH_CHECK_INTERVAL", "30")),
        "retry_on_timeout": _env_bool("REDIS_RETRY_ON_TIMEOUT", True),
    }

    username = _env("REDIS_USERNAME")
    password = _env("REDIS_PASSWORD")
    if username:
        kwargs["username"] = username
    if password:
        kwargs["password"] = password

    if _env_bool("REDIS_SSL", False):
        kwargs["ssl"] = True
        ssl_ca = _env("REDIS_SSL_CA")
        ssl_cert = _env("REDIS_SSL_CERT")
        ssl_key = _env("REDIS_SSL_KEY")
        if ssl_ca:
            kwargs["ssl_ca_certs"] = ssl_ca
        if ssl_cert:
            kwargs["ssl_certfile"] = ssl_cert
        if ssl_key:
            kwargs["ssl_keyfile"] = ssl_key

    return kwargs


@lru_cache(maxsize=1)
def get_redis_client():
    if redis is None:
        raise RuntimeError("redis package is not installed. Run pip install -r post/requirements.txt.")

    redis_url = _env("REDIS_URL")
    if redis_url:
        return redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=float(_env("REDIS_SOCKET_TIMEOUT", "5")),
            socket_connect_timeout=float(_env("REDIS_CONNECT_TIMEOUT", "5")),
            health_check_interval=int(_env("REDIS_HEALTH_CHECK_INTERVAL", "30")),
            retry_on_timeout=_env_bool("REDIS_RETRY_ON_TIMEOUT", True),
        )

    return redis.Redis(**_build_redis_kwargs())
