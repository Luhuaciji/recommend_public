from __future__ import annotations

import hashlib
import inspect
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token, copy_context
from datetime import datetime
from typing import Any, Dict, Optional


_CURRENT_TRACE: ContextVar[Optional["LLMTraceCollector"]] = ContextVar(
    "current_llm_trace",
    default=None,
)
_CURRENT_PARALLEL_GROUP: ContextVar[str] = ContextVar(
    "current_llm_parallel_group",
    default="",
)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


class LLMTraceCollector:
    def __init__(
        self,
        conversation_id: str,
        task_id: str,
        user_id: str = "",
        account_id: str = "",
    ) -> None:
        self.conversation_id = conversation_id
        self.task_id = task_id
        self.user_id = user_id
        self.account_id = account_id
        self.started_at = _now_iso()
        self._started_perf = time.perf_counter()
        self._lock = threading.Lock()
        self._start_sequence = 0
        self._completion_sequence = 0
        self._calls: list[Dict[str, Any]] = []

    def begin_call(
        self,
        *,
        call_name: str,
        call_type: str,
        model: str,
        temperature: float,
        prompt: str,
        system_prompt: Optional[str],
    ) -> Dict[str, Any]:
        started_perf = time.perf_counter()
        with self._lock:
            self._start_sequence += 1
            sequence = self._start_sequence

        prompt_text = str(prompt or "")
        system_text = str(system_prompt or "")
        call = {
            "sequence": sequence,
            "completion_sequence": None,
            "call_name": call_name,
            "call_type": call_type,
            "parallel_group": _CURRENT_PARALLEL_GROUP.get(),
            "model": model,
            "temperature": temperature,
            "thread_name": threading.current_thread().name,
            "started_at": _now_iso(),
            "start_offset_ms": round((started_perf - self._started_perf) * 1000, 2),
            "ended_at": None,
            "end_offset_ms": None,
            "duration_ms": None,
            "status": "running",
            "success": False,
            "prompt_chars": len(prompt_text),
            "system_prompt_chars": len(system_text),
            "prompt_hash": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
            "response_chars": 0,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "finish_reason": "",
            "request_id": "",
            "error_type": "",
            "error_message": "",
            "_started_perf": started_perf,
        }
        with self._lock:
            self._calls.append(call)
        return call

    def finish_call(
        self,
        call: Dict[str, Any],
        *,
        status: str,
        success: bool,
        response_text: str = "",
        usage: Any = None,
        finish_reason: str = "",
        request_id: str = "",
        error: Optional[BaseException] = None,
    ) -> None:
        ended_perf = time.perf_counter()
        prompt_tokens = _usage_value(usage, "prompt_tokens")
        completion_tokens = _usage_value(usage, "completion_tokens")
        total_tokens = _usage_value(usage, "total_tokens")

        with self._lock:
            self._completion_sequence += 1
            call["completion_sequence"] = self._completion_sequence
            call["ended_at"] = _now_iso()
            call["end_offset_ms"] = round(
                (ended_perf - self._started_perf) * 1000,
                2,
            )
            call["duration_ms"] = round(
                (ended_perf - float(call.pop("_started_perf"))) * 1000,
                2,
            )
            call["status"] = status
            call["success"] = bool(success)
            call["response_chars"] = len(str(response_text or ""))
            call["prompt_tokens"] = prompt_tokens
            call["completion_tokens"] = completion_tokens
            call["total_tokens"] = total_tokens
            call["finish_reason"] = finish_reason or ""
            call["request_id"] = request_id or ""
            if error is not None:
                call["error_type"] = type(error).__name__
                call["error_message"] = str(error)[:500]

    def summary(self) -> Dict[str, Any]:
        ended_at = _now_iso()
        elapsed_ms = round((time.perf_counter() - self._started_perf) * 1000, 2)
        with self._lock:
            calls = [dict(item) for item in self._calls]

        calls.sort(key=lambda item: int(item.get("sequence", 0)))
        completed_calls = [item for item in calls if item.get("status") != "running"]
        successful_calls = [item for item in completed_calls if item.get("success")]
        failed_calls = [item for item in completed_calls if not item.get("success")]

        prompt_tokens = _sum_optional(calls, "prompt_tokens")
        completion_tokens = _sum_optional(calls, "completion_tokens")
        total_tokens = _sum_optional(calls, "total_tokens")
        sum_duration_ms = round(
            sum(float(item.get("duration_ms") or 0.0) for item in completed_calls),
            2,
        )

        return {
            "conversation_id": self.conversation_id,
            "task_id": self.task_id,
            "user_id": self.user_id,
            "account_id": self.account_id,
            "started_at": self.started_at,
            "ended_at": ended_at,
            "summary": {
                "total_calls": len(calls),
                "completed_calls": len(completed_calls),
                "success_calls": len(successful_calls),
                "failed_calls": len(failed_calls),
                "json_calls": sum(1 for item in calls if item.get("call_type") == "json"),
                "text_calls": sum(1 for item in calls if item.get("call_type") == "text"),
                "sum_duration_ms": sum_duration_ms,
                "wall_span_ms": elapsed_ms,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "calls_by_name": _count_by(calls, "call_name"),
                "calls_by_parallel_group": _count_by(
                    [item for item in calls if item.get("parallel_group")],
                    "parallel_group",
                ),
            },
            "timeline": calls,
        }


def begin_llm_trace(
    conversation_id: str,
    task_id: str,
    user_id: str = "",
    account_id: str = "",
) -> tuple[LLMTraceCollector, Token]:
    collector = LLMTraceCollector(
        conversation_id=conversation_id,
        task_id=task_id,
        user_id=user_id,
        account_id=account_id,
    )
    token = _CURRENT_TRACE.set(collector)
    return collector, token


def end_llm_trace(collector: LLMTraceCollector, token: Token) -> Dict[str, Any]:
    try:
        return collector.summary()
    finally:
        _CURRENT_TRACE.reset(token)


def begin_llm_call(**kwargs: Any) -> Optional[Dict[str, Any]]:
    collector = _CURRENT_TRACE.get()
    if collector is None:
        return None
    return {
        "collector": collector,
        "call": collector.begin_call(**kwargs),
    }


def finish_llm_call(handle: Optional[Dict[str, Any]], **kwargs: Any) -> None:
    if not handle:
        return
    collector = handle.get("collector")
    call = handle.get("call")
    if isinstance(collector, LLMTraceCollector) and isinstance(call, dict):
        collector.finish_call(call, **kwargs)


def infer_llm_call_name(stack_depth: int = 1) -> str:
    frame = inspect.currentframe()
    try:
        for _ in range(max(1, stack_depth)):
            if frame is None:
                break
            frame = frame.f_back
        if frame is None:
            return "unknown"
        module_name = str(frame.f_globals.get("__name__", "") or "")
        function_name = str(frame.f_code.co_name or "unknown")
        return f"{module_name}.{function_name}" if module_name else function_name
    finally:
        del frame


@contextmanager
def llm_parallel_group(group: str):
    token = _CURRENT_PARALLEL_GROUP.set(group or "")
    try:
        yield
    finally:
        _CURRENT_PARALLEL_GROUP.reset(token)


def submit_with_llm_trace(executor: Any, func: Any, *args: Any, group: str = "", **kwargs: Any):
    context = copy_context()

    def run():
        with llm_parallel_group(group):
            return func(*args, **kwargs)

    return executor.submit(context.run, run)


def _usage_value(usage: Any, field: str) -> Optional[int]:
    if usage is None:
        return None
    if isinstance(usage, dict):
        value = usage.get(field)
    else:
        value = getattr(usage, field, None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _sum_optional(items: list[Dict[str, Any]], field: str) -> Optional[int]:
    values = [item.get(field) for item in items if item.get(field) is not None]
    if not values:
        return None
    return sum(int(value) for value in values)


def _count_by(items: list[Dict[str, Any]], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        key = str(item.get(field) or "").strip()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts
