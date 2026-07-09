"""
core 核心业务目录

文件说明：
- agent_service.py  写业务逻辑
- llm_client.py       调用大模型
- session.py          会话管理
- config.py           配置常量
"""
from .agent_service import handle_chat, handle_interrupt
from .session import get_session, create_session, save_session, delete_session, session_locks
from .llm_client import call_llm, get_llm_client
from .config import (
    API_BASE_URL, API_KEY, TEXT_MODEL_NAME,
    SCENARIO_CONFIG, MOCK_PRODUCT_DB,
    SESSION_EXPIRE_SECONDS, MAX_HISTORY_LENGTH
)

__all__ = [
    "handle_chat", "handle_interrupt",
    "get_session", "create_session", "save_session", "delete_session", "session_locks",
    "call_llm", "get_llm_client",
    "API_BASE_URL", "API_KEY", "TEXT_MODEL_NAME",
    "SCENARIO_CONFIG", "MOCK_PRODUCT_DB",
    "SESSION_EXPIRE_SECONDS", "MAX_HISTORY_LENGTH"
]
