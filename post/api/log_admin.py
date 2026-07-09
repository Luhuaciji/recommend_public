from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from core.conversation_logger import (
    cleanup_old_logs,
    delete_conversation,
    delete_task_logs,
    get_conversation_detail,
    get_conversation_events,
    get_conversation_messages,
    get_log_db_path,
    get_task_logs,
    init_log_db,
    list_conversations,
    search_conversations,
)


router = APIRouter(prefix="/admin/logs", tags=["admin-logs"])


@router.get("/status")
async def log_status():
    init_log_db()
    return {
        "enabled": True,
        "db_path": str(get_log_db_path()),
    }


@router.get("/conversations")
async def conversations(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return {
        "items": list_conversations(limit=limit, offset=offset),
        "limit": limit,
        "offset": offset,
    }


@router.get("/conversations/search")
async def conversation_search(
    start: str = Query(..., description="Start time, e.g. 2026-06-03 09:00:00"),
    end: str = Query(..., description="End time, e.g. 2026-06-03 12:00:00"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return {
        "items": search_conversations(
            start=start,
            end=end,
            limit=limit,
            offset=offset,
        ),
        "start": start,
        "end": end,
        "limit": limit,
        "offset": offset,
    }


@router.get("/conversations/{conversation_id}")
async def conversation_detail(conversation_id: str):
    detail = get_conversation_detail(conversation_id)
    if not detail:
        raise HTTPException(status_code=404, detail="conversation not found")
    return detail


@router.get("/conversations/{conversation_id}/messages")
async def conversation_messages(conversation_id: str):
    return {
        "conversation_id": conversation_id,
        "messages": get_conversation_messages(conversation_id),
    }


@router.get("/conversations/{conversation_id}/events")
async def conversation_events(conversation_id: str):
    return {
        "conversation_id": conversation_id,
        "events": get_conversation_events(conversation_id),
    }


@router.get("/tasks/{task_id}")
async def task_logs(task_id: str):
    logs = get_task_logs(task_id)
    if not logs.get("messages") and not logs.get("events"):
        raise HTTPException(status_code=404, detail="task logs not found")
    return logs


@router.delete("/conversations/{conversation_id}")
async def remove_conversation(conversation_id: str):
    return {
        "conversation_id": conversation_id,
        "deleted": delete_conversation(conversation_id),
    }


@router.delete("/tasks/{task_id}")
async def remove_task(task_id: str):
    return {
        "task_id": task_id,
        "deleted": delete_task_logs(task_id),
    }


@router.post("/cleanup")
async def cleanup(days: int = Query(default=10, ge=1, le=3650)):
    return cleanup_old_logs(days=days)
