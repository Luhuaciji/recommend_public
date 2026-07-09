"""
FastAPI 入口文件

- 定义接口路径
- 自动验证请求签名
- 把请求转发给业务代码
"""
from fastapi import FastAPI, BackgroundTasks, Depends
from api import CDFRequest, CDFResponse, FeedbackRequest, verify_signature
from core import handle_chat, session_locks, get_session
from db import save_feedback
from db.test_db_api import router as db_test_router

app = FastAPI(title="复旦 Agent")
app.include_router(db_test_router)


@app.post("/cdfai-demo/v1/fudan/chat", response_model=CDFResponse, dependencies=[Depends(verify_signature)])
async def process_chat(request: CDFRequest):
    conversation_id = request.ConversationID
    taskId = request.taskId

    # ==========================================
    # 阶段一：正常请求
    # 说明：
    # 1. 现在没有专门的中断请求
    # 2. 是否发生上一轮中断，由 handle_chat() 内部根据 ChatHistories 推断
    # 3. 当前这一轮仍然是正常请求，需要继续正常处理
    # ==========================================
    result = await handle_chat(
        conversation_id=conversation_id,
        taskId=taskId,
        user_id=request.UserID,
        user_query=request.parsed_query_text,
        clean_llm_history=request.clean_llm_history,
        chatHistoriesSnapshot=request.chatHistoriesSnapshot,
        query_extends=request.QueryExtends
    )
    return CDFResponse(
        taskId=taskId,
        data=result["data_blocks"],
        isGiftIntention=result["isGiftIntention"]
    )


@app.post("/cdfai-demo/v1/fudan/feedback", dependencies=[Depends(verify_signature)])
async def process_feedback(feedback: FeedbackRequest, background_tasks: BackgroundTasks):
    """反馈接口"""
    async with session_locks[feedback.ConversationID]:
        session = get_session(feedback.ConversationID)
        if not session:
            return {"code": 404, "msg": "会话不存在或已过期"}

        history = session.get("llm_history", [])
        target_index = next((i for i in range(len(history) - 1, -1, -1) if history[i].get("taskId") == feedback.taskId), -1)
        if target_index == -1:
            return {"code": 404, "msg": "找不到指定的任务 ID"}

        start_index = max(0, target_index - 7)
        sampled_history = history[start_index: target_index + 1]

    background_tasks.add_task(save_feedback, feedback, sampled_history)
    return {"code": 200, "msg": "反馈已收到", "taskId": feedback.taskId}
