from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from .mysql import get_mysql_connection, get_mysql_label


_FEEDBACK_DB_READY = False


def init_db() -> None:
    global _FEEDBACK_DB_READY
    if _FEEDBACK_DB_READY:
        return

    conn = get_mysql_connection("FEEDBACK")
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_records (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                `timestamp` DATETIME NOT NULL,
                conversation_id VARCHAR(191) NOT NULL,
                target_task_id VARCHAR(191) NOT NULL,
                user_id VARCHAR(191),
                like_type INT NOT NULL,
                history_json LONGTEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_conv_id (conversation_id),
                INDEX idx_like_type (like_type),
                INDEX idx_target_task_id (target_task_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        conn.commit()
        _FEEDBACK_DB_READY = True
    finally:
        cursor.close()
        conn.close()


async def save_feedback(feedback: Any, sampled_history: List[Dict[str, Any]]) -> None:
    insert_id = None
    try:
        init_db()
        history_json = json.dumps(sampled_history, ensure_ascii=False, default=str)

        conn = get_mysql_connection("FEEDBACK")
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO feedback_records
                    (`timestamp`, conversation_id, target_task_id, user_id, like_type, history_json)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    time.strftime("%Y-%m-%d %H:%M:%S"),
                    feedback.ConversationID,
                    feedback.taskId,
                    getattr(feedback, "UserID", None),
                    feedback.LikeType,
                    history_json,
                ),
            )
            insert_id = cursor.lastrowid
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    except Exception as e:
        print(f"[feedback-write-error] {e}")
        return

    action_text = "like" if feedback.LikeType == 1 else "dislike"
    print(f"\n===== [feedback saved] action={action_text} id={insert_id} =====")
    print(f"conversation_id: {feedback.ConversationID}")
    print(f"task_id: {feedback.taskId}")
    print(f"storage: {get_mysql_label('FEEDBACK')}")
    print("==========================================\n")


def get_all_feedback(limit: int = 10) -> List[Dict[str, Any]]:
    init_db()
    conn = get_mysql_connection("FEEDBACK")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT id, `timestamp`, conversation_id, target_task_id,
                   user_id, like_type, history_json, created_at
            FROM feedback_records
            ORDER BY id DESC
            LIMIT %s
            """,
            (max(1, min(int(limit), 500)),),
        )
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def get_feedback_stats() -> Dict[int, int]:
    init_db()
    conn = get_mysql_connection("FEEDBACK")
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT like_type, COUNT(*) AS cnt FROM feedback_records GROUP BY like_type"
        )
        return {int(row[0]): int(row[1]) for row in cursor.fetchall()}
    finally:
        cursor.close()
        conn.close()
