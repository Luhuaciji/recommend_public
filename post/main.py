"""
FastAPI 入口文件

- 定义接口路径
- 自动验证请求签名
- 把请求转发给业务代码
"""
import asyncio
import os

from fastapi import FastAPI, BackgroundTasks, Depends
from api import CDFRequest, CDFResponse, FeedbackRequest, verify_signature
from api.log_admin import router as log_admin_router
from core import handle_chat, handle_interrupt, session_locks, get_session
from core.conversation_logger import cleanup_old_logs, init_log_db
from db import save_feedback
from db.test_db_api import router as db_test_router

app = FastAPI(title="复旦 Agent")
app.include_router(db_test_router)
app.include_router(log_admin_router)


async def _daily_log_cleanup_loop():
    while True:
        await asyncio.sleep(24 * 60 * 60)
        days = int(os.getenv("CONVERSATION_LOG_RETENTION_DAYS", "10") or "10")
        try:
            cleanup_old_logs(days=days)
        except Exception as e:
            print(f"[conversation-log-cleanup-error] {e}")


@app.on_event("startup")
async def startup_event():
    init_log_db()
    days = int(os.getenv("CONVERSATION_LOG_RETENTION_DAYS", "10") or "10")
    try:
        cleanup_old_logs(days=days)
    except Exception as e:
        print(f"[conversation-log-startup-cleanup-error] {e}")
    asyncio.create_task(_daily_log_cleanup_loop())


@app.post("/cdfai/v1/fudan/chat", response_model=CDFResponse, dependencies=[Depends(verify_signature)])
async def process_chat(request: CDFRequest):
    conversation_id = request.ConversationID
    task_id = request.taskId

    # ==========================================
    # 阶段一：兼容旧显式中断请求
    # 新中断协议不再依赖 IsInterrupt；正常请求内部会根据 ChatHistories 推断。
    # ==========================================
    if request.IsInterrupt:
        result = await handle_interrupt(conversation_id, task_id)
        return CDFResponse(
            taskId=task_id,
            data=result["data_blocks"],
            isGiftIntention=result["isGiftIntention"],
            isInterrupted=result["isInterrupted"]
        )

    # ==========================================
    # 阶段二：正常请求
    # ==========================================
    result = await handle_chat(
        conversation_id=conversation_id,
        task_id=task_id,
        user_id=request.UserID,
        user_query=request.parsed_query_text,
        clean_llm_history=request.clean_llm_history,
        chatHistoriesSnapshot=request.chatHistoriesSnapshot,
        query_extends=request.QueryExtends,
        account_id=request.account_id,
        query_token=request.query_token,
        message_id=request.message_id,
        query_payload=request.query_payload,
    )
    return CDFResponse(
        taskId=task_id,
        data=result["data_blocks"],
        isGiftIntention=result["isGiftIntention"],
        isInterrupted=result["isInterrupted"]
    )


@app.post("/cdfai/v1/fudan/feedback", dependencies=[Depends(verify_signature)])
async def process_feedback(feedback: FeedbackRequest, background_tasks: BackgroundTasks):
    """反馈接口"""
    async with session_locks[feedback.ConversationID]:
        session = get_session(feedback.ConversationID)
        if not session:
            return {"code": 404, "msg": "会话不存在或已过期"}

        history = session.get("llm_history", [])
        target_index = next((
            i for i in range(len(history)-1, -1, -1)
            if (history[i].get("taskId") or history[i].get("task_id")) == feedback.taskId
        ), -1)
        if target_index == -1:
            return {"code": 404, "msg": "找不到指定的任务 ID"}

        start_index = max(0, target_index - 7)
        sampled_history = history[start_index: target_index + 1]

    background_tasks.add_task(save_feedback, feedback, sampled_history)
    return {"code": 200, "msg": "反馈已收到", "taskId": feedback.taskId}
