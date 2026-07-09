from __future__ import annotations

import dataclasses
import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from db.mysql import get_mysql_connection, get_mysql_label


_DB_LOCK = threading.RLock()
_LOG_DB_READY = False


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_log_db_path() -> str:
    return get_mysql_label("LOG")


def _logging_enabled() -> bool:
    return os.getenv("CONVERSATION_LOG_ENABLED", "true").lower() not in {
        "0",
        "false",
        "no",
    }


def _mysql_sql(sql: str) -> str:
    return sql.replace("?", "%s")


class _MySQLConnectionAdapter:
    def __init__(self, conn: Any):
        self._conn = conn

    def execute(self, sql: str, params: tuple = ()):
        cursor = self._conn.cursor(dictionary=True)
        cursor.execute(_mysql_sql(sql), params or ())
        return cursor

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _connect() -> _MySQLConnectionAdapter:
    return _MySQLConnectionAdapter(get_mysql_connection("LOG"))


def init_log_db() -> None:
    global _LOG_DB_READY
    if not _logging_enabled():
        return
    if _LOG_DB_READY:
        return
    with _DB_LOCK:
        if _LOG_DB_READY:
            return
        conn = _connect()
        try:
            for statement in (
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id VARCHAR(191) PRIMARY KEY,
                    user_id VARCHAR(191),
                    started_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    last_task_id VARCHAR(191),
                    last_stage VARCHAR(64),
                    message_count INT DEFAULT 0,
                    INDEX idx_conversations_started_at (started_at),
                    INDEX idx_conversations_updated_at (updated_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """,
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    conversation_id VARCHAR(191) NOT NULL,
                    task_id VARCHAR(191) NOT NULL,
                    role VARCHAR(32) NOT NULL,
                    content LONGTEXT,
                    data_blocks_json LONGTEXT,
                    created_at DATETIME NOT NULL,
                    INDEX idx_messages_conv_created (conversation_id, created_at),
                    INDEX idx_messages_created_at (created_at),
                    INDEX idx_messages_task (task_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """,
                """
                CREATE TABLE IF NOT EXISTS conversation_events (
                    id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    conversation_id VARCHAR(191) NOT NULL,
                    task_id VARCHAR(191),
                    event_type VARCHAR(64) NOT NULL,
                    payload_json LONGTEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    INDEX idx_events_conv_created (conversation_id, created_at),
                    INDEX idx_events_created_at (created_at),
                    INDEX idx_events_task (task_id),
                    INDEX idx_events_type_created (event_type, created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """,
            ):
                conn.execute(statement)
            conn.commit()
            _LOG_DB_READY = True
        finally:
            conn.close()


def _safe_json_default(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, set):
        return list(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_safe_json_default)


def _json_loads(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _execute_write(callback) -> Any:
    if not _logging_enabled():
        return None
    try:
        init_log_db()
        with _DB_LOCK:
            conn = _connect()
            try:
                result = callback(conn)
                conn.commit()
                return result
            finally:
                conn.close()
    except Exception as e:
        print(f"[conversation-log-write-error] {e}")
        return None


def _ensure_conversation(
    conn: Any,
    conversation_id: str,
    user_id: str = "",
    task_id: str = "",
    stage: str = "",
) -> None:
    ts = _now()
    conn.execute(
        """
        INSERT INTO conversations (
            conversation_id, user_id, started_at, updated_at,
            last_task_id, last_stage, message_count
        )
        VALUES (?, ?, ?, ?, ?, ?, 0)
        ON DUPLICATE KEY UPDATE
            user_id = COALESCE(NULLIF(VALUES(user_id), ''), user_id),
            updated_at = VALUES(updated_at),
            last_task_id = COALESCE(NULLIF(VALUES(last_task_id), ''), last_task_id),
            last_stage = COALESCE(NULLIF(VALUES(last_stage), ''), last_stage)
        """,
        (conversation_id, user_id or "", ts, ts, task_id or "", stage or ""),
    )


def _refresh_message_count(conn: Any, conversation_id: str) -> None:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM conversation_messages
        WHERE conversation_id = ?
        """,
        (conversation_id,),
    ).fetchone()
    conn.execute(
        "UPDATE conversations SET message_count = ? WHERE conversation_id = ?",
        (int(row["count"] if row else 0), conversation_id),
    )


def _insert_message(
    conn: Any,
    conversation_id: str,
    task_id: str,
    role: str,
    content: str = "",
    data_blocks: Optional[List[Dict[str, Any]]] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO conversation_messages (
            conversation_id, task_id, role, content, data_blocks_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            task_id,
            role,
            content or "",
            _json_dumps(data_blocks) if data_blocks is not None else None,
            _now(),
        ),
    )
    _refresh_message_count(conn, conversation_id)


def _insert_event(
    conn: Any,
    conversation_id: str,
    task_id: str,
    event_type: str,
    payload: Dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO conversation_events (
            conversation_id, task_id, event_type, payload_json, created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            task_id or "",
            event_type,
            _json_dumps(payload or {}),
            _now(),
        ),
    )


def log_user_message(
    conversation_id: str,
    task_id: str,
    user_id: str,
    user_query: str,
    query_extends: Optional[Dict[str, Any]] = None,
    frontend_history_count: int = 0,
    account_id: str = "",
    message_id: Any = None,
) -> None:
    def write(conn: Any) -> None:
        _ensure_conversation(conn, conversation_id, user_id=user_id, task_id=task_id)
        _insert_message(conn, conversation_id, task_id, "user", user_query or "")
        _insert_event(
            conn,
            conversation_id,
            task_id,
            "user_message_received",
            {
                "user_id": user_id,
                "account_id": account_id or "",
                "message_id": message_id,
                "user_query": user_query or "",
                "query_extends": query_extends or {},
                "frontend_history_count": frontend_history_count,
            },
        )

    _execute_write(write)


def log_assistant_response(
    conversation_id: str,
    task_id: str,
    action: str,
    data_blocks: List[Dict[str, Any]],
    is_gift_intention: bool,
    is_interrupted: bool,
) -> None:
    assistant_text = _extract_assistant_text(data_blocks)
    product_cards = extract_product_cards(data_blocks)

    def write(conn: Any) -> None:
        _ensure_conversation(conn, conversation_id, task_id=task_id)
        _insert_message(
            conn,
            conversation_id,
            task_id,
            "assistant",
            assistant_text,
            data_blocks=data_blocks,
        )
        _insert_event(
            conn,
            conversation_id,
            task_id,
            "assistant_response_sent",
            {
                "action": action,
                "is_gift_intention": is_gift_intention,
                "is_interrupted": is_interrupted,
                "product_cards": product_cards,
                "data_blocks": data_blocks,
            },
        )

    _execute_write(write)


def log_event(
    conversation_id: str,
    task_id: str,
    event_type: str,
    payload: Dict[str, Any],
    user_id: str = "",
    stage: str = "",
    account_id: str = "",
) -> None:
    if account_id:
        payload = dict(payload or {})
        payload.setdefault("account_id", account_id)

    def write(conn: Any) -> None:
        _ensure_conversation(
            conn,
            conversation_id,
            user_id=user_id,
            task_id=task_id,
            stage=stage,
        )
        _insert_event(conn, conversation_id, task_id, event_type, payload)

    _execute_write(write)


def log_llm_call_summary(
    conversation_id: str,
    task_id: str,
    trace_summary: Dict[str, Any],
    user_id: str = "",
    account_id: str = "",
) -> None:
    log_event(
        conversation_id=conversation_id,
        task_id=task_id,
        event_type="llm_call_summary",
        payload=trace_summary or {},
        user_id=user_id,
        account_id=account_id,
    )


def log_gift_state_snapshot(
    conversation_id: str,
    task_id: str,
    state: Any,
    stage: str,
    pending_categories: Optional[List[str]] = None,
    pending_reason: str = "",
    rejected_categories: Optional[List[str]] = None,
) -> None:
    payload = build_gift_state_payload(
        state,
        stage=stage,
        pending_categories=pending_categories or [],
        pending_reason=pending_reason or "",
        rejected_categories=rejected_categories or [],
    )

    def write(conn: Any) -> None:
        _ensure_conversation(conn, conversation_id, task_id=task_id, stage=stage)
        _insert_event(conn, conversation_id, task_id, "gift_state_snapshot", payload)

    _execute_write(write)


def log_recommendation_analysis(
    conversation_id: str,
    task_id: str,
    state: Any,
    stage: str,
    action: str,
    data_blocks: List[Dict[str, Any]],
) -> None:
    payload = {
        "stage": stage,
        "action": action,
        "account_id": getattr(state, "account_id", "") or "",
        "filled_slots": _filled_slots_payload(state),
        "selected_category": _selected_category_payload(state),
        "candidate_pool_summary": getattr(state, "candidate_pool_summary", {}) or {},
        "candidate_pool_reason": getattr(state, "candidate_pool_reason", "") or "",
        "task_boundary_decision": getattr(state, "task_boundary_decision", {}) or {},
        "current_turn_slot_updates": getattr(state, "current_turn_slot_updates", {}) or {},
        "turn_understanding": getattr(state, "turn_understanding", {}) or {},
        "inference_results": getattr(state, "inference_results", []) or [],
        "detailed_dimensions": getattr(state, "detailed_dimensions", {}) or {},
        "downgrade_retry_triggered": bool(
            getattr(state, "downgrade_retry_triggered", False)
        ),
        "downgrade_retry_reason": getattr(state, "downgrade_retry_reason", "") or "",
        "product_cards": extract_product_cards(data_blocks),
        "filtered_products": _product_list_payload(
            getattr(state, "filtered_products", []) or [],
            limit=10,
        ),
        "final_product_cards": getattr(state, "final_product_cards", []) or [],
    }
    log_event(conversation_id, task_id, "recommendation_analysis", payload, stage=stage)


def build_gift_state_payload(
    state: Any,
    stage: str,
    pending_categories: List[str],
    pending_reason: str,
    rejected_categories: List[str],
) -> Dict[str, Any]:
    return {
        "stage": stage,
        "account_id": getattr(state, "account_id", "") or "",
        "filled_slots": _filled_slots_payload(state),
        "selected_category": _selected_category_payload(state),
        "recommended_categories": getattr(state, "recommended_categories", []) or [],
        "pending_categories": pending_categories,
        "pending_reason": pending_reason,
        "rejected_categories": rejected_categories,
        "detailed_dimensions": getattr(state, "detailed_dimensions", {}) or {},
        "inference_results": getattr(state, "inference_results", []) or [],
        "candidate_pool_summary": getattr(state, "candidate_pool_summary", {}) or {},
        "candidate_pool_reason": getattr(state, "candidate_pool_reason", "") or "",
        "task_boundary_decision": getattr(state, "task_boundary_decision", {}) or {},
        "current_turn_slot_updates": getattr(state, "current_turn_slot_updates", {}) or {},
        "turn_understanding": getattr(state, "turn_understanding", {}) or {},
        "candidate_products": _product_list_payload(
            getattr(state, "candidate_products", []) or [],
            limit=10,
        ),
        "filtered_products": _product_list_payload(
            getattr(state, "filtered_products", []) or [],
            limit=10,
        ),
        "final_product_cards": getattr(state, "final_product_cards", []) or [],
        "downgrade_retry_triggered": bool(
            getattr(state, "downgrade_retry_triggered", False)
        ),
        "downgrade_retry_reason": getattr(state, "downgrade_retry_reason", "") or "",
    }


def _filled_slots_payload(state: Any) -> Dict[str, Any]:
    slots = getattr(state, "filled_slots", {}) or {}
    payload: Dict[str, Any] = {}
    for name, slot in slots.items():
        value = getattr(slot, "value", None)
        if value is None:
            continue
        payload[name] = {
            "value": value,
            "is_filled": bool(getattr(slot, "is_filled", False)),
            "priority": getattr(slot, "priority", ""),
        }
    return payload


def _selected_category_payload(state: Any) -> Optional[Dict[str, Any]]:
    category = getattr(state, "selected_category", None)
    if not category:
        return None
    return {
        "category_id": getattr(category, "category_id", "") or "",
        "category_name": getattr(category, "category_name", "") or "",
        "description": getattr(category, "description", "") or "",
        "selection_reason": getattr(category, "selection_reason", "") or "",
        "selected_mid_category": getattr(state, "selected_mid_category", "") or "",
        "selected_subcategory": getattr(state, "selected_subcategory", "") or "",
        "selected_big_category": getattr(state, "selected_big_category", "") or "",
        "category_level": getattr(state, "category_level", "") or "",
    }


def _product_list_payload(products: List[Any], limit: int = 10) -> List[Dict[str, Any]]:
    result = []
    for product in list(products or [])[:limit]:
        result.append(
            {
                "sku_id": str(getattr(product, "sku_id", "") or ""),
                "name": getattr(product, "sku_name", None)
                or getattr(product, "name", "")
                or "",
                "price": getattr(product, "price", None),
                "brand": getattr(product, "brand", "") or "",
                "mid_category": getattr(product, "mid_category", "")
                or getattr(product, "category", "")
                or "",
                "small_category": getattr(product, "small_category", "") or "",
            }
        )
    return result


def _extract_assistant_text(data_blocks: List[Dict[str, Any]]) -> str:
    texts = []
    for block in data_blocks or []:
        content = str(block.get("content", "") or "")
        if not content or content.lstrip().startswith("```json"):
            continue
        texts.append(content)
    return "\n".join(texts).strip()


def extract_product_cards(data_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    for block in data_blocks or []:
        payload = _payload_from_block(block)
        if isinstance(payload, dict) and payload.get("type") == "pro-recommend":
            data = payload.get("data", [])
            if isinstance(data, list):
                cards.extend([item for item in data if isinstance(item, dict)])
    return cards


def _payload_from_block(block: Dict[str, Any]) -> Any:
    if "type" in block and "data" in block:
        return block

    content = str(block.get("content", "") or "").strip()
    if content.startswith("```json"):
        content = content[len("```json") :].strip()
        if content.endswith("```"):
            content = content[:-3].strip()
    if not content:
        return None
    try:
        return json.loads(content)
    except Exception:
        return None


def list_conversations(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    init_log_db()
    with _DB_LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    conversation_id,
                    user_id,
                    started_at AS created_at,
                    updated_at,
                    last_task_id,
                    last_stage,
                    message_count
                FROM conversations
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (max(1, min(int(limit), 500)), max(0, int(offset))),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()


def search_conversations(
    start: str,
    end: str,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    init_log_db()
    start = str(start or "").strip()
    end = str(end or "").strip()
    if not start or not end:
        return []

    with _DB_LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    c.conversation_id,
                    c.user_id,
                    c.started_at AS created_at,
                    c.updated_at,
                    c.last_task_id,
                    c.last_stage,
                    c.message_count,
                    (
                        SELECT COUNT(*)
                        FROM conversation_messages m
                        WHERE m.conversation_id = c.conversation_id
                          AND m.created_at BETWEEN ? AND ?
                    ) AS matched_message_count,
                    (
                        SELECT COUNT(*)
                        FROM conversation_events e
                        WHERE e.conversation_id = c.conversation_id
                          AND e.created_at BETWEEN ? AND ?
                    ) AS matched_event_count
                FROM conversations c
                WHERE
                    c.started_at BETWEEN ? AND ?
                    OR c.updated_at BETWEEN ? AND ?
                    OR EXISTS (
                        SELECT 1
                        FROM conversation_messages m2
                        WHERE m2.conversation_id = c.conversation_id
                          AND m2.created_at BETWEEN ? AND ?
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM conversation_events e2
                        WHERE e2.conversation_id = c.conversation_id
                          AND e2.created_at BETWEEN ? AND ?
                    )
                ORDER BY c.updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (
                    start,
                    end,
                    start,
                    end,
                    start,
                    end,
                    start,
                    end,
                    start,
                    end,
                    start,
                    end,
                    max(1, min(int(limit), 500)),
                    max(0, int(offset)),
                ),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()


def get_conversation_detail(conversation_id: str) -> Dict[str, Any]:
    init_log_db()
    with _DB_LOCK:
        conn = _connect()
        try:
            conversation = conn.execute(
                """
                SELECT
                    conversation_id,
                    user_id,
                    started_at AS created_at,
                    updated_at,
                    last_task_id,
                    last_stage,
                    message_count
                FROM conversations
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
            if not conversation:
                return {}
            return {
                "conversation": dict(conversation),
                "messages": _messages_for_conversation(conn, conversation_id),
                "events": _events_for_conversation(conn, conversation_id),
            }
        finally:
            conn.close()


def get_conversation_messages(conversation_id: str) -> List[Dict[str, Any]]:
    init_log_db()
    with _DB_LOCK:
        conn = _connect()
        try:
            return _messages_for_conversation(conn, conversation_id)
        finally:
            conn.close()


def get_conversation_events(conversation_id: str) -> List[Dict[str, Any]]:
    init_log_db()
    with _DB_LOCK:
        conn = _connect()
        try:
            return _events_for_conversation(conn, conversation_id)
        finally:
            conn.close()


def get_task_logs(task_id: str) -> Dict[str, Any]:
    init_log_db()
    with _DB_LOCK:
        conn = _connect()
        try:
            message_rows = conn.execute(
                """
                SELECT id, conversation_id, task_id, role, content,
                       data_blocks_json, created_at
                FROM conversation_messages
                WHERE task_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (task_id,),
            ).fetchall()
            event_rows = conn.execute(
                """
                SELECT id, conversation_id, task_id, event_type, payload_json, created_at
                FROM conversation_events
                WHERE task_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (task_id,),
            ).fetchall()
            return {
                "task_id": task_id,
                "messages": [_message_row_to_dict(row) for row in message_rows],
                "events": [_event_row_to_dict(row) for row in event_rows],
            }
        finally:
            conn.close()


def _messages_for_conversation(
    conn: Any,
    conversation_id: str,
) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, conversation_id, task_id, role, content,
               data_blocks_json, created_at
        FROM conversation_messages
        WHERE conversation_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (conversation_id,),
    ).fetchall()
    return [_message_row_to_dict(row) for row in rows]


def _events_for_conversation(
    conn: Any,
    conversation_id: str,
) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, conversation_id, task_id, event_type, payload_json, created_at
        FROM conversation_events
        WHERE conversation_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (conversation_id,),
    ).fetchall()
    return [_event_row_to_dict(row) for row in rows]


def _message_row_to_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["data_blocks"] = _json_loads(item.pop("data_blocks_json", None), None)
    return item


def _event_row_to_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["payload"] = _json_loads(item.pop("payload_json", None), {})
    return item


def delete_conversation(conversation_id: str) -> Dict[str, int]:
    def write(conn: Any) -> Dict[str, int]:
        message_count = conn.execute(
            "SELECT COUNT(*) AS count FROM conversation_messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()["count"]
        event_count = conn.execute(
            "SELECT COUNT(*) AS count FROM conversation_events WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()["count"]
        conversation_count = conn.execute(
            "SELECT COUNT(*) AS count FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()["count"]
        conn.execute(
            "DELETE FROM conversation_messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.execute(
            "DELETE FROM conversation_events WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.execute(
            "DELETE FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        )
        return {
            "conversations": int(conversation_count),
            "messages": int(message_count),
            "events": int(event_count),
        }

    return _execute_write(write) or {"conversations": 0, "messages": 0, "events": 0}


def delete_task_logs(task_id: str) -> Dict[str, int]:
    def write(conn: Any) -> Dict[str, int]:
        conversation_rows = conn.execute(
            """
            SELECT DISTINCT conversation_id
            FROM (
                SELECT conversation_id FROM conversation_messages WHERE task_id = ?
                UNION
                SELECT conversation_id FROM conversation_events WHERE task_id = ?
            )
            """,
            (task_id, task_id),
        ).fetchall()
        message_count = conn.execute(
            "SELECT COUNT(*) AS count FROM conversation_messages WHERE task_id = ?",
            (task_id,),
        ).fetchone()["count"]
        event_count = conn.execute(
            "SELECT COUNT(*) AS count FROM conversation_events WHERE task_id = ?",
            (task_id,),
        ).fetchone()["count"]
        conn.execute("DELETE FROM conversation_messages WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM conversation_events WHERE task_id = ?", (task_id,))
        for row in conversation_rows:
            _refresh_message_count(conn, row["conversation_id"])
            conn.execute(
                """
                UPDATE conversations
                SET updated_at = ?
                WHERE conversation_id = ?
                """,
                (_now(), row["conversation_id"]),
            )
        return {"messages": int(message_count), "events": int(event_count)}

    return _execute_write(write) or {"messages": 0, "events": 0}


def cleanup_old_logs(days: int = 10) -> Dict[str, int]:
    days = max(1, int(days))
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    def write(conn: Any) -> Dict[str, int]:
        rows = conn.execute(
            "SELECT conversation_id FROM conversations WHERE updated_at < ?",
            (cutoff,),
        ).fetchall()
        conversation_ids = [row["conversation_id"] for row in rows]
        message_count = 0
        event_count = 0
        for conversation_id in conversation_ids:
            message_count += conn.execute(
                "SELECT COUNT(*) AS count FROM conversation_messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()["count"]
            event_count += conn.execute(
                "SELECT COUNT(*) AS count FROM conversation_events WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()["count"]
            conn.execute(
                "DELETE FROM conversation_messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            conn.execute(
                "DELETE FROM conversation_events WHERE conversation_id = ?",
                (conversation_id,),
            )
            conn.execute(
                "DELETE FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            )
        return {
            "days": days,
            "cutoff": cutoff,
            "conversations": len(conversation_ids),
            "messages": int(message_count),
            "events": int(event_count),
        }

    return _execute_write(write) or {
        "days": days,
        "cutoff": cutoff,
        "conversations": 0,
        "messages": 0,
        "events": 0,
    }
