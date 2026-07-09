"""
会话管理工具文件
    get_session()       拿会话，过期了自动返回空
    create_session()    新建会话
    save_session()      存会话，自动续期30分钟
    delete_session()    删除会话
"""
import asyncio
import time
from collections import defaultdict
from typing import Dict, Any, Optional
DEBUG_SESSION = False

# 用于debug历史对话
def debug_session_snapshot(conversation_id: str, session: Dict[str, Any]) -> None:
    if not DEBUG_SESSION:
        return

    history = session.get("llm_history", [])
    print("\n===== session snapshot =====")
    print("conversation_id:", conversation_id)
    print("history_len:", len(history))
    print("interrupted_ids:", list(session.get("interrupted_ids", set())))
    print("lastFrontendChatHistoriesSnapshot:", session.get("lastFrontendChatHistoriesSnapshot"))
    print("lastUserTaskId:", session.get("lastUserTaskId"))
    print("last_history:")
    for msg in history[-10:]:
        print("  ", {
            "role": msg.get("role"),
            "taskId": msg.get("taskId"),
            "status": msg.get("status"),
            "content": msg.get("content", "")[:80]
        })
    print("============================\n")

# 全局会话存储 (生产环境替换为 Redis)
fake_redis_db: Dict[str, Any] = {}

# 每个会话一把锁，保证并发安全
session_locks = defaultdict(asyncio.Lock)


def get_session(conversation_id: str) -> Optional[Dict[str, Any]]:
    """获取会话，自动检查超时"""
    session = fake_redis_db.get(conversation_id)
    if session and session["expire_at"] < time.time():
        return None
    return session


def create_session(user_id: str, initial_history: list) -> Dict[str, Any]:
    """创建新会话"""
    return {
        "user_id": user_id,
        "collected_slots": {},
        "llm_history": initial_history,
        "expire_at": time.time() + 1800,
        "interrupted_ids": set(),
        "lastFrontendChatHistoriesSnapshot": "[]",
        "lastUserTaskId": ""
    }


def save_session(conversation_id: str, session: Dict[str, Any]) -> None:
    """保存会话，自动续期"""
    session["expire_at"] = time.time() + 1800
    fake_redis_db[conversation_id] = session
    debug_session_snapshot(conversation_id, session)


def delete_session(conversation_id: str) -> None:
    """删除会话（退出场景时调用）"""
    if conversation_id in fake_redis_db:
        del fake_redis_db[conversation_id]


def mark_interrupted(conversation_id: str, taskId: str) -> bool:
    """标记 task 为已中断，返回是否成功"""
    session = get_session(conversation_id)
    if not session:
        return False

    if "interrupted_ids" not in session:
        session["interrupted_ids"] = set()
    session["interrupted_ids"].add(taskId)
    save_session(conversation_id, session)
    return True


def is_interrupted(conversation_id: str, taskId: str) -> bool:
    """检查 task 是否已被中断"""
    session = get_session(conversation_id)
    if not session:
        return False
    return taskId in session.get("interrupted_ids", set())
