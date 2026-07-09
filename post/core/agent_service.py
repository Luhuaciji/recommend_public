"""
核心业务层: Agent 业务逻辑


对外暴露 2 个主函数：
- handle_interrupt()  处理中断
- handle_chat()       处理正常对话
"""
import copy
import asyncio
import json
import time
from typing import Dict, Any, List
from .aliyun_response_guard import AliyunResponseGuard, GuardCheckResult
from .llm_client import call_llm
from .member_info_gender import get_gender_by_account_id
from .config import (
    SCENARIO_CONFIG,
    MOCK_PRODUCT_DB,
    MAX_HISTORY_LENGTH,
    MAX_DIALOG_TURNS,
    DIALOG_LIMIT_MESSAGE,
)
from .conversation_logger import (
    log_assistant_response,
    log_event,
    log_gift_state_snapshot,
    log_llm_call_summary,
    log_recommendation_analysis,
    log_user_message,
)
from .session import get_session, create_session, save_session, session_locks
from .gift import run_gift_turn
from .llm_trace import begin_llm_trace, end_llm_trace


_GIFT_STATE_KEYS = [
    "gift_state",
    "gift_stage",
    "gift_pending_categories",
    "gift_pending_selection_reason",
    "gift_rejected_categories",
    "gift_rejected_subcategories",
    "collected_slots",
    "last_recommended_slots",
]
_GIFT_SNAPSHOT_STACK_KEY = "gift_turn_stack"
_GIFT_SNAPSHOT_MAX_SIZE = 10
_RESPONSE_GUARD_FALLBACK_MESSAGE = "抱歉，该回答暂时无法展示。"
_response_guard = None


# ==========================================
# 【工具函数】数据库状态调试（开发用）
# ==========================================
def _get_db_debug_info() -> str:
    """调试用：获取商品库状态，退出场景时显示给开发人员看"""
    try:
        from pathlib import Path
        import sqlite3
        DB_FILE = Path(__file__).parent.parent / "product_data.db"
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as total_count FROM item_details")
        total_items = cursor.fetchone()[0]
        cursor.execute("SELECT value FROM sync_config WHERE key='last_update_time'")
        row = cursor.fetchone()
        last_sync = row[0] if row else "未同步"
        conn.close()
        return f"（商品库状态：{total_items} 条，最后同步：{last_sync}）"
    except Exception as e:
        return f"（数据库异常：{str(e)[:50]}）"


# ==========================================
# 【入口1】中断处理（带全局异常兜底）
# ==========================================
async def handle_interrupt(conversation_id: str, task_id: str) -> Dict[str, Any]:
    """
    兼容旧显式中断请求。
    新协议下用户中断主要由下一轮正常请求根据 ChatHistories 推断。
    """
    try:
        async with session_locks[conversation_id]:
            session = get_session(conversation_id)
            if session:
                _apply_interrupt_to_session(session, task_id)
                save_session(conversation_id, session)
            log_event(
                conversation_id,
                task_id,
                "interrupt_received",
                {"is_interrupted": True},
            )
            return {
                "action": "INTERRUPT",
                "data_blocks": [],
                "isGiftIntention": True,
                "isInterrupted": True
            }
    except Exception as e:
        print(f"[中断异常]: {e}")
        return {
            "action": "INTERRUPT",
            "data_blocks": [],
            "isGiftIntention": True,
            "isInterrupted": True
        }


def _message_task_id(message: Dict[str, Any]) -> str:
    return str(message.get("taskId") or message.get("task_id") or "")


def _remove_history_by_task_id(session: Dict[str, Any], task_id: str) -> None:
    history = session.get("llm_history", [])
    if not isinstance(history, list):
        session["llm_history"] = []
        return

    session["llm_history"] = [
        msg for msg in history
        if not isinstance(msg, dict) or _message_task_id(msg) != task_id
    ]


def _apply_interrupt_to_session(session: Dict[str, Any], task_id: str, user_query: str = "") -> bool:
    if "interrupted_ids" not in session:
        session["interrupted_ids"] = set()
    session["interrupted_ids"].add(task_id)
    _remove_history_by_task_id(session, task_id)

    rollback_applied = _rollback_gift_state(session, task_id)
    if rollback_applied:
        print(f"[Gift 回滚] 已恢复到 taskId={task_id} 处理前的送礼业务状态")
    return rollback_applied


def _should_infer_previous_interrupt(
    session: Dict[str, Any],
    chatHistoriesSnapshot: str,
    current_task_id: str
) -> bool:
    previous_snapshot = session.get("lastFrontendChatHistoriesSnapshot", "[]")
    previous_task_id = session.get("lastUserTaskId", "")
    if not previous_task_id or previous_task_id == current_task_id:
        return False
    return previous_snapshot == chatHistoriesSnapshot


def _ensure_dialog_limit_state(session: Dict[str, Any]) -> None:
    if "dialog_turn_count" not in session:
        session["dialog_turn_count"] = sum(
            1 for msg in session.get("llm_history", [])
            if isinstance(msg, dict) and msg.get("role") == "user"
        )
    if "dialog_limit_reached" not in session:
        session["dialog_limit_reached"] = bool(
            session.get("dialog_turn_count", 0) >= MAX_DIALOG_TURNS
        )


def _build_dialog_limit_response() -> Dict[str, Any]:
    return {
        "action": "EXIT",
        "data_blocks": [{"type": "text", "content": DIALOG_LIMIT_MESSAGE}],
        "isGiftIntention": False,
        "isInterrupted": False
    }


def _get_response_guard() -> AliyunResponseGuard:
    global _response_guard
    if _response_guard is None:
        _response_guard = AliyunResponseGuard()
    return _response_guard


def _extract_response_guard_text(data_blocks: List[Dict[str, Any]]) -> str:
    texts = []
    for block in data_blocks or []:
        if not isinstance(block, dict):
            continue
        if str(block.get("type", "") or "").lower() == "json":
            continue
        content = str(block.get("content", "") or "")
        if not content.strip() or content.lstrip().startswith("```json"):
            continue
        texts.append(content)
    return "\n".join(texts).strip()


def _build_response_guard_fallback() -> List[Dict[str, str]]:
    return [{"type": "text", "content": _RESPONSE_GUARD_FALLBACK_MESSAGE}]


def _guard_event_payload(guard_result: GuardCheckResult, checked_text: str) -> Dict[str, Any]:
    return {
        "success": guard_result.success,
        "allowed": guard_result.allowed,
        "suggestion": guard_result.suggestion,
        "request_id": guard_result.request_id,
        "code": guard_result.code,
        "message": guard_result.message,
        "error": guard_result.error,
        "checked_text_length": len(checked_text or ""),
    }


def _build_query_context(query_payload: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    previous = session.get("query_context", {})
    if not isinstance(previous, dict):
        previous = {}

    context = dict(previous)
    field_map = {
        "accountId": "account_id",
        "MessageID": "message_id",
        "userLocation": "user_location",
        "recipientLocation": "recipient_location",
    }
    for raw_key, normalized_key in field_map.items():
        value = query_payload.get(raw_key)
        if value is not None and str(value).strip():
            context[normalized_key] = str(value).strip()

    context.pop("token", None)
    return context


async def _resolve_member_profile(
    session: Dict[str, Any],
    account_id: str,
    query_token: str = "",
) -> Dict[str, Any]:
    account_id = (account_id or session.get("account_id", "") or "").strip()
    if not account_id:
        return {}

    profile = session.get("member_profile", {})
    if not isinstance(profile, dict):
        profile = {}

    if (
        profile.get("account_id") == account_id
        and profile.get("gender") in {"\u7537", "\u5973"}
    ):
        session["member_profile"] = profile
        return profile

    gender = ""
    try:
        resolved_gender = await asyncio.to_thread(
            get_gender_by_account_id,
            account_id,
            auth_token=query_token or "",
        )
        if resolved_gender in {"\u7537", "\u5973"}:
            gender = resolved_gender
    except Exception as exc:
        error_text = str(exc)
        print(f"[member-info-gender-error] accountId={account_id} error={error_text}")
        log_event(
            conversation_id=str(session.get("conversation_id", "") or ""),
            task_id=str(session.get("lastUserTaskId", "") or ""),
            event_type="member_info_gender_error",
            payload={
                "account_id": account_id,
                "message": "[member-info-gender-error]",
                "error_type": exc.__class__.__name__,
                "error": error_text,
                "has_query_token": bool(query_token),
            },
            user_id=str(session.get("user_id", "") or ""),
            account_id=account_id,
        )

    profile = {
        "account_id": account_id,
        "gender": gender or "",
        "gender_source": "open_platform_member_info" if gender else "",
    }
    session["member_profile"] = profile
    return profile


# ==========================================
# 【入口2】正常对话（带全局异常兜底）
# ==========================================
async def handle_chat(
    conversation_id: str,
    task_id: str,
    user_id: str,
    user_query: str,
    clean_llm_history: list,
    chatHistoriesSnapshot: str = "[]",
    query_extends: dict = None,
    account_id: str = "",
    query_token: str = "",
    message_id: Any = None,
    query_payload: dict = None,
) -> Dict[str, Any]:
    """处理正常聊天请求"""
    try:
        return await _handle_chat_unsafe(
            conversation_id, task_id, user_id, user_query,
            clean_llm_history, chatHistoriesSnapshot, query_extends,
            account_id, query_token, message_id, query_payload
        )
    except Exception as e:
        print(f"[严重异常] 业务逻辑崩溃: {e}")
        return {
            "action": "CHAT",
            "data_blocks": [{"type": "text", "content": f"抱歉，处理您的请求时遇到异常：{str(e)}..."}],
            "isGiftIntention": True,
            "isInterrupted": False
        }


async def _handle_chat_unsafe(
    conversation_id: str,
    task_id: str,
    user_id: str,
    user_query: str,
    clean_llm_history: list,
    chatHistoriesSnapshot: str = "[]",
    query_extends: dict = None,
    account_id: str = "",
    query_token: str = "",
    message_id: Any = None,
    query_payload: dict = None,
) -> Dict[str, Any]:
    """
    主流程流水线，一共 5 步：
        1. 冷热启动 + 推断上一轮中断
        2. 写入当前用户提问并推送 gift 快照
        3. 锁外执行核心业务
        4. 返回前检查当前轮是否已被后续请求覆盖
        5. 未覆盖时提交业务状态和 assistant 历史
    """

    # ---------- 第 1/2 步：短锁内维护会话和中断推断 ----------
    log_user_message(
        conversation_id=conversation_id,
        task_id=task_id,
        user_id=user_id,
        user_query=user_query,
        query_extends=query_extends or {},
        frontend_history_count=len(clean_llm_history or []),
        account_id=account_id,
        message_id=message_id,
    )

    async with session_locks[conversation_id]:
        session = get_session(conversation_id)
        if not session:
            session = create_session(user_id, clean_llm_history)
            print(f"[冷启动] 初始化会话，同步前端历史共 {len(clean_llm_history)} 轮")
        session["conversation_id"] = conversation_id
        session["account_id"] = account_id or session.get("account_id", "")
        session["message_id"] = message_id
        session["query_context"] = _build_query_context(query_payload or {}, session)

        if _should_infer_previous_interrupt(session, chatHistoriesSnapshot, task_id):
            interrupted_task_id = session.get("lastUserTaskId", "")
            if interrupted_task_id and interrupted_task_id not in session.get("interrupted_ids", set()):
                print(f"[推断中断] 检测到前端历史未推进，补记上一轮中断，taskId: {interrupted_task_id}")
                rollback_applied = _apply_interrupt_to_session(session, interrupted_task_id)
                log_event(
                    conversation_id,
                    interrupted_task_id,
                    "interrupt_inferred_from_frontend_history",
                    {
                        "interrupted_task_id": interrupted_task_id,
                        "trigger_task_id": task_id,
                        "rollback_applied": rollback_applied,
                        "reason": "frontend chat history snapshot did not advance before a new task",
                    },
                    user_id=user_id,
                    account_id=account_id,
                )

        if task_id in session.get("interrupted_ids", set()):
            print(f"[中断拦截] 当前请求 taskId 已被标记中断，跳过业务执行: {task_id}")
            _remove_history_by_task_id(session, task_id)
            save_session(conversation_id, session)
            log_event(
                conversation_id,
                task_id,
                "request_skipped_interrupted",
                {"is_interrupted": True},
                user_id=user_id,
                account_id=account_id,
            )
            return {
                "action": "INTERRUPT",
                "data_blocks": [],
                "isGiftIntention": True,
                "isInterrupted": True
            }

        _ensure_dialog_limit_state(session)
        if session.get("dialog_limit_reached"):
            print(
                f"[轮次限制] 会话已达到最大正常交互轮数 "
                f"{MAX_DIALOG_TURNS}，拦截 taskId: {task_id}"
            )
            save_session(conversation_id, session)
            response = _build_dialog_limit_response()
            log_assistant_response(
                conversation_id=conversation_id,
                task_id=task_id,
                action=response["action"],
                data_blocks=response["data_blocks"],
                is_gift_intention=response["isGiftIntention"],
                is_interrupted=response["isInterrupted"],
            )
            return response

        dialog_turn_count = int(session.get("dialog_turn_count", 0) or 0)
        if dialog_turn_count >= MAX_DIALOG_TURNS:
            session["dialog_limit_reached"] = True
            print(
                f"[轮次限制] 会话达到最大正常交互轮数 "
                f"{MAX_DIALOG_TURNS}，拦截 taskId: {task_id}"
            )
            save_session(conversation_id, session)
            response = _build_dialog_limit_response()
            log_assistant_response(
                conversation_id=conversation_id,
                task_id=task_id,
                action=response["action"],
                data_blocks=response["data_blocks"],
                is_gift_intention=response["isGiftIntention"],
                is_interrupted=response["isInterrupted"],
            )
            return response

        session["dialog_turn_count"] = dialog_turn_count + 1
        if session["dialog_turn_count"] >= MAX_DIALOG_TURNS:
            session["dialog_limit_reached"] = True

        if user_query:
            session["llm_history"].append({
                "role": "user",
                "content": user_query,
                "taskId": task_id
            })

        session["query_extends"] = query_extends or {}
        _push_gift_snapshot(session, task_id)
        session["lastFrontendChatHistoriesSnapshot"] = chatHistoriesSnapshot
        session["lastUserTaskId"] = task_id
        save_session(conversation_id, session)
        agent_session = copy.deepcopy(session)

    # ---------- 第 3 步：核心业务锁外执行 ----------
    await _resolve_member_profile(agent_session, account_id, query_token)
    llm_trace_collector, llm_trace_token = begin_llm_trace(
        conversation_id=conversation_id,
        task_id=task_id,
        user_id=user_id,
        account_id=account_id,
    )
    try:
        agent_result = await _agent_brain(user_query, agent_session)
    finally:
        llm_trace_summary = end_llm_trace(llm_trace_collector, llm_trace_token)
        log_llm_call_summary(
            conversation_id=conversation_id,
            task_id=task_id,
            trace_summary=llm_trace_summary,
            user_id=user_id,
            account_id=account_id,
        )
    action = agent_result["action"]
    data_blocks = agent_result["data_blocks"]
    collected_slots = agent_result.get("new_slots", agent_session.get("collected_slots", {}))
    is_gift_intention = agent_result.get("isGiftIntention", True)

    guard_text = _extract_response_guard_text(data_blocks)
    if guard_text:
        guard_result = await asyncio.to_thread(
            _get_response_guard().check_response,
            guard_text,
            f"{conversation_id}:{task_id}",
        )
    else:
        guard_result = GuardCheckResult(
            success=True,
            allowed=True,
            suggestion="no_text",
            request_id=None,
            code=None,
            message="No visible text to check",
            detail=None,
            raw=None,
            error=None,
        )
    guard_allowed = guard_result.allowed

    # ---------- 第 4 步：中断二次检查 ----------
    async with session_locks[conversation_id]:
        latest_session = get_session(conversation_id)
        if not latest_session:
            latest_session = agent_session

        if task_id in latest_session.get("interrupted_ids", set()):
            print(f"[中断覆盖] 当前轮返回前发现已被后续请求覆盖，不写入正常历史，taskId: {task_id}")
            _apply_interrupt_to_session(latest_session, task_id, user_query)
            save_session(conversation_id, latest_session)
            log_event(
                conversation_id=conversation_id,
                task_id=task_id,
                event_type="response_guard_checked",
                payload=_guard_event_payload(guard_result, guard_text),
                user_id=user_id,
                account_id=account_id,
            )
            return {
                "action": "INTERRUPT",
                "data_blocks": [],
                "isGiftIntention": is_gift_intention,
                "isInterrupted": True
            }

        session_updates = agent_result.get("session_updates", {})
        if guard_allowed and session_updates:
            _apply_session_updates(latest_session, session_updates)
        elif not guard_allowed:
            action = "CHAT"
            data_blocks = _build_response_guard_fallback()

        assistant_text = "".join([
            b["content"] for b in data_blocks
            if not b.get("content", "").startswith("```json")
        ])
        if assistant_text.strip():
            latest_session["llm_history"].append({
                "role": "assistant",
                "content": assistant_text.strip(),
                "taskId": task_id
            })

        if len(latest_session["llm_history"]) > MAX_HISTORY_LENGTH:
            latest_session["llm_history"] = latest_session["llm_history"][-MAX_HISTORY_LENGTH:]

        if guard_allowed:
            latest_session["collected_slots"] = collected_slots
        if agent_session.get("member_profile"):
            latest_session["member_profile"] = agent_session.get("member_profile")
        latest_session["account_id"] = account_id or latest_session.get("account_id", "")
        latest_session["query_context"] = agent_session.get(
            "query_context",
            latest_session.get("query_context", {}),
        )
        gift_state = latest_session.get("gift_state")
        gift_stage = latest_session.get("gift_stage", "")
        log_event(
            conversation_id=conversation_id,
            task_id=task_id,
            event_type="response_guard_checked",
            payload=_guard_event_payload(guard_result, guard_text),
            user_id=user_id,
            account_id=account_id,
        )
        if guard_allowed and gift_state is not None:
            log_gift_state_snapshot(
                conversation_id=conversation_id,
                task_id=task_id,
                state=gift_state,
                stage=gift_stage,
                pending_categories=latest_session.get("gift_pending_categories", []),
                pending_reason=latest_session.get("gift_pending_selection_reason", ""),
                rejected_categories=latest_session.get("gift_rejected_categories", []),
            )
            log_recommendation_analysis(
                conversation_id=conversation_id,
                task_id=task_id,
                state=gift_state,
                stage=gift_stage,
                action=action,
                data_blocks=data_blocks,
            )
        log_assistant_response(
            conversation_id=conversation_id,
            task_id=task_id,
            action=action,
            data_blocks=data_blocks,
            is_gift_intention=is_gift_intention,
            is_interrupted=False,
        )

        # 隐式中断协议下 EXIT 也可能被前端丢弃；不立即删除 session，交给 TTL 清理。
        save_session(conversation_id, latest_session)

    return {
        "action": action,
        "data_blocks": data_blocks,
        "isGiftIntention": is_gift_intention,
        "isInterrupted": False
    }


# ==========================================
# 【核心大脑】状态机分支实现
# ==========================================
async def _agent_brain(user_query: str, session: Dict[str, Any]) -> Dict[str, Any]:
    return await run_gift_turn(
        conversation_id=session.get("conversation_id") or session.get("user_id", ""),
        user_query=user_query,
        session=session,
        query_extends=session.get("query_extends"),
    )


def _apply_session_updates(session: Dict[str, Any], session_updates: Dict[str, Any]) -> None:
    for key, value in session_updates.items():
        if value is None:
            session.pop(key, None)
        else:
            session[key] = value


def _push_gift_snapshot(session: Dict[str, Any], task_id: str) -> None:
    stack = list(session.get(_GIFT_SNAPSHOT_STACK_KEY, []) or [])
    snapshot = {
        "task_id": task_id,
        "before": _capture_gift_state_snapshot(session),
    }
    stack.append(snapshot)
    if len(stack) > _GIFT_SNAPSHOT_MAX_SIZE:
        stack = stack[-_GIFT_SNAPSHOT_MAX_SIZE:]
    session[_GIFT_SNAPSHOT_STACK_KEY] = stack


def _capture_gift_state_snapshot(session: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    snapshot: Dict[str, Dict[str, Any]] = {}
    for key in _GIFT_STATE_KEYS:
        if key in session:
            snapshot[key] = {
                "exists": True,
                "value": copy.deepcopy(session[key]),
            }
        else:
            snapshot[key] = {"exists": False}
    return snapshot


def _rollback_gift_state(session: Dict[str, Any], task_id: str) -> bool:
    stack = list(session.get(_GIFT_SNAPSHOT_STACK_KEY, []) or [])
    if not stack:
        return False

    latest_snapshot = stack[-1]
    if latest_snapshot.get("task_id") != task_id:
        return False

    before = latest_snapshot.get("before", {})
    for key in _GIFT_STATE_KEYS:
        key_snapshot = before.get(key, {"exists": False})
        if key_snapshot.get("exists"):
            session[key] = copy.deepcopy(key_snapshot.get("value"))
        else:
            session.pop(key, None)

    stack.pop()
    if stack:
        session[_GIFT_SNAPSHOT_STACK_KEY] = stack
    else:
        session.pop(_GIFT_SNAPSHOT_STACK_KEY, None)
    return True


async def _agent_brain_legacy(user_query: str, session: Dict[str, Any]) -> Dict[str, Any]:
    collected_slots = session.get("collected_slots", {})
    history = session.get("llm_history", [])

    collected_slots.update(extract_entities(user_query))
    action = determine_dialog_action(user_query, collected_slots, session)

    # ---------- 分支1：退出场景（物流/快递/天气等） ----------
    if action == "EXIT":
        return {
            "action": "EXIT", 
            "data_blocks": [{"content": "好的，这就为您切换到综合助手办理其他业务。"}],
            "new_slots": collected_slots
        }

    # ---------- 分支2：收集信息（槽位不够时问用户） ----------
    elif action == "COLLECT_INFO":
        return {
            "action": "ASK", 
            "data_blocks": [{"content": SCENARIO_CONFIG["composite_prompt"]}], 
            "new_slots": collected_slots
        }

    # ---------- 分支3：商品推荐 ----------
    elif action == "RECOMMEND":
        current_skin = collected_slots.get('skin_type', '干皮')
        product_info = MOCK_PRODUCT_DB.get(current_skin)
        
        text_content = f"为您重新筛选了商品！根据您目前的条件（目标肤质：{current_skin}），我为您精选了以下产品：\n\n1. {product_info['productName']}：精准匹配您的需求，品质绝佳。"
        
        pro_recommend_data = {
            "type": "pro-recommend",
            "data": [{
                "productId": product_info["productId"],
                "productPic": product_info["productPic"],
                "productName": product_info["productName"],
                "payPrice": product_info["payPrice"],
                "purchaseType": "1",
                "merchantId": product_info["merchantId"],
                "showStrategy": False
            }]
        }
        card_content = f"```json\n{json.dumps(pro_recommend_data, ensure_ascii=False)}\n```"

        add_questions_data = {
            "type": "add-questions", 
            "title": "您可能还想问", 
            "data": [{"title": f"{product_info['productName']}怎么用？"}, {"title": "包装怎么样？"}]
        }
        questions_content = f"```json\n{json.dumps(add_questions_data, ensure_ascii=False)}\n```"

        session["last_recommended_slots"] = collected_slots.copy()
        
        return {
            "action": "SEND_CARD", 
            "data_blocks": [{"content": text_content}, {"content": card_content}, {"content": questions_content}], 
            "new_slots": collected_slots
        }

    # ---------- 分支4：QA 问答 / 自由聊天 ----------
    elif action in ["QA_CHAT", "GENERAL_CHAT"]:
        system_msg = {"role": "system", "content": "你是一个中免日上的高级专属导购小Q。请根据用户的提问和上下文历史，给出专业、友好、简明扼要的回复。"}
        
        clean_history = [{"role": msg["role"], "content": msg["content"]} for msg in history]
        messages = [system_msg] + clean_history[-10:]
        
        llm_reply = await call_llm(messages)
        
        return {
            "action": "CHAT", 
            "data_blocks": [{"content": llm_reply}], 
            "new_slots": collected_slots
        }

    return {"action": "EXIT", "data_blocks": [{"content": "系统异常，退出场景。"}], "new_slots": collected_slots}


# ==========================================
# 【基础组件1】实体抽取
# ==========================================
def extract_entities(user_query: str) -> Dict[str, str]:
    """
    从用户问题里扒出来有用的信息
    比如："我是干皮" → 抽出来 {"skin_type": "干皮"}
    
    真实场景：这里应该调用 NER 模型或者小 LLM
    现在是硬编码做演示
    """
    extracted = {}
    if "干皮" in user_query:
        extracted["skin_type"] = "干皮"
    elif "油皮" in user_query:
        extracted["skin_type"] = "油皮"
    return extracted


# ==========================================
# 【基础组件2】路由分发
# ==========================================
def determine_dialog_action(user_query: str, collected_slots: dict, session_context: dict) -> str:
    """
    🎯 路由决策器：这句话该走哪个分支？

    判断优先级：
        EXIT（退出）> QA_CHAT（问答）> RECOMMEND（推荐）> COLLECT_INFO（问信息）> GENERAL_CHAT（闲聊）

    加新功能：在这里加关键词，返回新的 action 就行
    """
    # 最高优先级：命中退出词 → 直接走
    exit_keywords = ["物流", "快递", "天气", "查订单", "不买了", "退出"]
    if any(kw in user_query for kw in exit_keywords): 
        return "EXIT"

    # 第二优先级：命中 QA 关键词 → 直接问答
    qa_keywords = ["怎么用", "功效", "区别", "多少钱", "适合", "包装", "有货", "介绍"]
    if any(kw in user_query for kw in qa_keywords): 
        return "QA_CHAT"

    # 第三优先级：重新推荐
    re_rec_keywords = ["换", "其他", "别的", "重新推荐", "再看看"]
    if any(kw in user_query for kw in re_rec_keywords): 
        return "RECOMMEND"
    
    # 第四优先级：必要信息没收集够 → 继续问
    missing_slots = [s for s in SCENARIO_CONFIG["required_slots"] if s not in collected_slots]
    if missing_slots: 
        return "COLLECT_INFO"
    
    # 第五优先级：槽位变了 → 重新推荐
    if collected_slots != session_context.get("last_recommended_slots"): 
        return "RECOMMEND"
        
    # 兜底：自由聊天
    return "GENERAL_CHAT"
