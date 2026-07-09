from __future__ import annotations

import os
from typing import Any, Dict

import mysql.connector


def _env(name: str, prefix: str = "", default: str = "") -> str:
    if prefix:
        value = os.getenv(f"{prefix}_MYSQL_{name}")
        if value is not None:
            return value
    value = os.getenv(f"MYSQL_{name}")
    if value is not None:
        return value
    return default


def _env_bool(name: str, prefix: str = "", default: bool = False) -> bool:
    raw = _env(name, prefix, "true" if default else "false")
    return raw.lower() in {"1", "true", "yes", "on"}


def build_mysql_config(prefix: str = "") -> Dict[str, Any]:
    host = _env("HOST", prefix)
    user = _env("USER", prefix)
    database = _env("DATABASE", prefix)
    if not host or not user or not database:
        scope = f"{prefix}_MYSQL_* or MYSQL_*" if prefix else "MYSQL_*"
        raise RuntimeError(
            f"Missing MySQL config. Required env: {scope} HOST, USER, DATABASE."
        )

    config: Dict[str, Any] = {
        "host": host,
        "port": int(_env("PORT", prefix, "3306")),
        "user": user,
        "password": _env("PASSWORD", prefix, ""),
        "database": database,
        "charset": _env("CHARSET", prefix, "utf8mb4"),
        "connection_timeout": int(_env("CONNECT_TIMEOUT", prefix, "10")),
        "autocommit": False,
    }

    ssl_enabled = _env_bool("SSL", prefix, False)
    config["ssl_disabled"] = not ssl_enabled
    ssl_ca = _env("SSL_CA", prefix)
    ssl_cert = _env("SSL_CERT", prefix)
    ssl_key = _env("SSL_KEY", prefix)
    if ssl_enabled and ssl_ca:
        config["ssl_ca"] = ssl_ca
    if ssl_enabled and ssl_cert:
        config["ssl_cert"] = ssl_cert
    if ssl_enabled and ssl_key:
        config["ssl_key"] = ssl_key

    return config


def get_mysql_connection(prefix: str = ""):
    return mysql.connector.connect(**build_mysql_config(prefix))


def get_mysql_label(prefix: str = "") -> str:
    host = _env("HOST", prefix, "<missing-host>")
    port = _env("PORT", prefix, "3306")
    database = _env("DATABASE", prefix, "<missing-database>")
    return f"mysql://{host}:{port}/{database}"
