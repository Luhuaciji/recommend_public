"""
核心业务层：Agent 业务逻辑

对外暴露 2 个主函数：
- handle_interrupt()  处理中断
- handle_chat()       处理正常对话
"""
import json
import time
from typing import Dict, Any, List
from .llm_client import call_llm
from .config import SCENARIO_CONFIG, MOCK_PRODUCT_DB, MAX_HISTORY_LENGTH
from .session import (
    get_session, create_session, save_session, delete_session,
    mark_interrupted, is_interrupted, session_locks
)

INTERRUPTED_TEXT = "（生成已被用户中断）"


def _append_interrupt_placeholder(session: Dict[str, Any], taskId: str, user_query: str = "") -> None:
    history = session.get("llm_history", [])
    if not history:
        if user_query:
            history.append({"role": "user", "content": user_query, "taskId": taskId})
        history.append({
            "role": "assistant",
            "content": INTERRUPTED_TEXT,
            "taskId": taskId,
            "status": "interrupted"
        })
        return

    last_msg = history[-1]
    if last_msg.get("taskId") == taskId and last_msg.get("role") == "user":
        history.append({
            "role": "assistant",
            "content": INTERRUPTED_TEXT,
            "taskId": taskId,
            "status": "interrupted"
        })
    elif last_msg.get("taskId") == taskId and last_msg.get("role") == "assistant":
        last_msg["status"] = "interrupted"
        last_msg["content"] = INTERRUPTED_TEXT
    elif not any(msg.get("taskId") == taskId for msg in history):
        if user_query:
            history.append({"role": "user", "content": user_query, "taskId": taskId})
        history.append({
            "role": "assistant",
            "content": INTERRUPTED_TEXT,
            "taskId": taskId,
            "status": "interrupted"
        })


# ==========================================
# 【入口】中断处理（保留为通用能力）
# ==========================================
def _get_db_debug_info() -> str:
    """Debug only: show the latest CSV fetch status when exiting the scenario."""
    try:
        import csv
        from pathlib import Path

        export_dir = Path(__file__).parent.parent / "db_export"
        csv_files = sorted(export_dir.glob("dim_pub_sku_*.csv"), key=lambda p: p.stat().st_mtime)
        if not csv_files:
            return f"(fetch data status: no CSV export found yet; output dir: {export_dir})"

        latest_file = csv_files[-1]
        with latest_file.open("r", newline="", encoding="utf-8") as csvfile:
            reader = csv.reader(csvfile)
            header = next(reader, [])
            row_count = sum(1 for _ in reader)

        modified_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(latest_file.stat().st_mtime))
        columns_preview = ", ".join(header[:10]) if header else "no columns"
        return (
            f"(fetch data status: file={latest_file.name}, modified_at={modified_at}, "
            f"rows={row_count}, columns={len(header)}, preview=[{columns_preview}])"
        )
    except Exception as e:
        return f"(fetch data status unavailable: {str(e)[:80]})"


async def handle_interrupt(conversation_id: str, taskId: str, user_query: str = "") -> Dict[str, Any]:
    """
    通用中断处理函数：
    1. 如果后续同学仍然需要显式中断入口，可以继续复用
    2. 当前这版新的主逻辑里，也会在正常请求内部“推断上一轮被中断”后调用它
    """
    try:
        async with session_locks[conversation_id]:
            return await _handle_interrupt_unsafe(conversation_id, taskId, user_query)
    except Exception as e:
        print(f"[中断异常]: {e}")
        return {
            "action": "INTERRUPT",
            "data_blocks": [],
            "isGiftIntention": True
        }


async def _handle_interrupt_unsafe(conversation_id: str, taskId: str, user_query: str = "") -> Dict[str, Any]:
    """
    实际的中断逻辑：
    1. 先登记当前 taskId 已中断
    2. 如果会话已经存在，就把历史补齐/标记中断
    """
    mark_interrupted(conversation_id, taskId)

    session = get_session(conversation_id)
    if session:
        _append_interrupt_placeholder(session, taskId, user_query)
        save_session(conversation_id, session)

    return {
        "action": "INTERRUPT",
        "data_blocks": [],
        "isGiftIntention": True
    }


# ==========================================
# 【工具函数】根据连续两次 ChatHistories 判断上一轮是否被中断
# 说明：
# - 现在没有显式中断请求
# - 如果第二次请求的 ChatHistories 与第一次完全相同，
#   说明前端历史没有推进，可以推断上一轮未被保存，即上一轮被中断
# ==========================================
def _should_infer_previous_interrupt(
    session: Dict[str, Any],
    chatHistoriesSnapshot: str,
    currentTaskId: str
) -> bool:
    previousChatHistoriesSnapshot = session.get("lastFrontendChatHistoriesSnapshot", "[]")
    previousTaskId = session.get("lastUserTaskId", "")
    if not previousTaskId or previousTaskId == currentTaskId:
        return False
    return previousChatHistoriesSnapshot == chatHistoriesSnapshot


# ==========================================
# 【入口】正常对话（带全局异常兜底）
# ==========================================
async def handle_chat(
    conversation_id: str,
    taskId: str,
    user_id: str,
    user_query: str,
    clean_llm_history: list,
    chatHistoriesSnapshot: str = "[]",
    query_extends: dict = None
) -> Dict[str, Any]:
    """处理正常聊天请求"""
    try:
        return await _handle_chat_unsafe(
            conversation_id, taskId, user_id, user_query, clean_llm_history, chatHistoriesSnapshot, query_extends
        )
    except Exception as e:
        print(f"[严重异常] 业务逻辑崩溃: {e}")
        return {
            "action": "CHAT",
            "data_blocks": [{"type": "text", "content": f"抱歉，处理您的请求时遇到异常：{str(e)[:30]}..."}],
            "isGiftIntention": True
        }


async def _handle_chat_unsafe(
    conversation_id: str,
    taskId: str,
    user_id: str,
    user_query: str,
    clean_llm_history: list,
    chatHistoriesSnapshot: str = "[]",
    query_extends: dict = None
) -> Dict[str, Any]:
    """
    主流程流水线，一共 5 步：
        1. 冷热启动 + 推断上一轮中断
        2. 写入当前用户提问到历史
        3. 核心大脑处理
        4. 当前轮中断二次检查（如果这一轮后来被判定为中断，则不写入正常 assistant 历史）
        5. 保存会话并返回结果
    """

    # ---------- 第 1 步：会话初始化 + 推断上一轮中断 ----------
    async with session_locks[conversation_id]:
        session = get_session(conversation_id)
        if not session:
            session = create_session(user_id, clean_llm_history)
            save_session(conversation_id, session)
            print(f"[冷启动] 初始化会话，同步前端历史共 {len(clean_llm_history)} 轮")

        # 新逻辑：
        # 当前轮仍然是正常请求，但如果发现前端历史没推进，
        # 就先补记上一轮被中断
        if _should_infer_previous_interrupt(session, chatHistoriesSnapshot, taskId):
            interruptedTaskId = session.get("lastUserTaskId", "")
            if interruptedTaskId and not is_interrupted(conversation_id, interruptedTaskId):
                print(f"[推断中断] 检测到前端历史未推进，补记上一轮中断，taskId: {interruptedTaskId}")
                await _handle_interrupt_unsafe(conversation_id, interruptedTaskId)

        # ---------- 第 2 步：写入当前用户提问 ----------
        if user_query:
            session["llm_history"].append({
                "role": "user",
                "content": user_query,
                "taskId": taskId
            })

        # 记录这一轮看到的前端完整历史，用于下一轮判断上一轮是否被中断
        session["lastFrontendChatHistoriesSnapshot"] = chatHistoriesSnapshot
        session["lastUserTaskId"] = taskId
        save_session(conversation_id, session)

    # ---------- 第 3 步：核心大脑处理 ----------
    agent_result = await _agent_brain(user_query, session)
    action = agent_result["action"]
    data_blocks = agent_result["data_blocks"]
    collected_slots = agent_result.get("new_slots", session.get("collected_slots", {}))

    # ---------- 第 4 步：当前轮中断二次检查 ----------
    async with session_locks[conversation_id]:
        latest_session = get_session(conversation_id)
        if not latest_session:
            latest_session = session

        if is_interrupted(conversation_id, taskId):
            print(f"[隐式中断] 当前轮已被后续请求覆盖，保留本次返回结果，但不写入本地会话历史，taskId: {taskId}")
            _append_interrupt_placeholder(latest_session, taskId, user_query)
            save_session(conversation_id, latest_session)
            return {
                "action": action,
                "data_blocks": data_blocks,
                "isGiftIntention": False if action == "EXIT" else True
            }

        # ---------- 第 5 步：收尾并返回 ----------
        assistant_text = "".join([b["content"] for b in data_blocks if not b.get("content", "").startswith("```json")])
        if assistant_text.strip():
            latest_session["llm_history"].append({"role": "assistant", "content": assistant_text.strip(), "taskId": taskId})

        # 裁剪历史
        if len(latest_session["llm_history"]) > MAX_HISTORY_LENGTH:
            latest_session["llm_history"] = latest_session["llm_history"][-MAX_HISTORY_LENGTH:]

        latest_session["collected_slots"] = collected_slots

        if action == "EXIT":
            delete_session(conversation_id)
            db_debug = _get_db_debug_info()
            exit_block = {
                "type": "text",
                "content": f"好的，这就为您切换到综合助手办理其他业务。{db_debug}"
            }
            return {
                "action": "EXIT",
                "data_blocks": [exit_block],
                "isGiftIntention": False
            }

        save_session(conversation_id, latest_session)

    return {
        "action": action,
        "data_blocks": data_blocks,
        "isGiftIntention": True
    }


# ==========================================
# 【核心大脑】状态机分支实现
# ==========================================
async def _agent_brain(user_query: str, session: Dict[str, Any]) -> Dict[str, Any]:
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
                "showStrategy": True
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
    从用户问题里抽出有用的信息
    比如："我是干皮" -> 抽出 {"skin_type": "干皮"}

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
# 【基础组件2】路由分支
# ==========================================
def determine_dialog_action(user_query: str, collected_slots: dict, session_context: dict) -> str:
    """
    路由决策器：这句话该走哪一个分支？

    判断优先级：
        EXIT（退出）> QA_CHAT（问答）> RECOMMEND（推荐）> COLLECT_INFO（问信息）> GENERAL_CHAT（闲聊）

    加新功能：在这里加关键词，返回新的 action 就行
    """
    # 最高优先级：命中退出词 -> 直接走
    exit_keywords = ["物流", "快递", "天气", "查询单", "不买了", "退出"]
    if any(kw in user_query for kw in exit_keywords):
        return "EXIT"

    # 第二优先级：命中 QA 关键词 -> 直接问答
    qa_keywords = ["怎么用", "功效", "区别", "多少钱", "适合", "包装", "有货", "介绍"]
    if any(kw in user_query for kw in qa_keywords):
        return "QA_CHAT"

    # 第三优先级：重新推荐
    re_rec_keywords = ["换", "其他", "别的", "重新推荐", "再看看"]
    if any(kw in user_query for kw in re_rec_keywords):
        return "RECOMMEND"

    # 第四优先级：必要信息没收集够 -> 继续问
    missing_slots = [s for s in SCENARIO_CONFIG["required_slots"] if s not in collected_slots]
    if missing_slots:
        return "COLLECT_INFO"

    # 第五优先级：槽位变了 -> 重新推荐
    if collected_slots != session_context.get("last_recommended_slots"):
        return "RECOMMEND"

    # 兜底：自由聊天
    return "GENERAL_CHAT"
