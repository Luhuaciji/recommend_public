"""
大模型客户端：只负责和 LLM 通信
"""
from typing import List, Dict, Any
from openai import AsyncOpenAI
from .config import API_BASE_URL, API_KEY, TEXT_MODEL_NAME, LLM_TIMEOUT, LLM_MAX_RETRIES
from .llm_trace import begin_llm_call, finish_llm_call, infer_llm_call_name

_llm_client = None

def get_llm_client():
    """懒加载单例：避免导入时跨事件循环绑定"""
    global _llm_client
    if _llm_client is None:
        _llm_client = AsyncOpenAI(
            api_key=API_KEY,
            base_url=API_BASE_URL,
            max_retries=LLM_MAX_RETRIES,
            timeout=LLM_TIMEOUT
        )
    return _llm_client

async def call_llm(messages: List[Dict[str, str]]) -> str:
    """
    调用大模型纯文本接口
    :param messages: 标准 OpenAI 格式 messages
    :return: 回复内容文本
    """
    client = get_llm_client()
    prompt_text = "\n".join(str(item.get("content", "")) for item in messages)
    trace_handle = begin_llm_call(
        call_name=infer_llm_call_name(stack_depth=2),
        call_type="text",
        model=TEXT_MODEL_NAME,
        temperature=0.7,
        prompt=prompt_text,
        system_prompt=None,
    )
    try:
        response = await client.chat.completions.create(
            model=TEXT_MODEL_NAME,
            messages=messages,
            temperature=0.7
        )
        content = response.choices[0].message.content.strip()
        choice = response.choices[0] if response.choices else None
        finish_llm_call(
            trace_handle,
            status="success",
            success=True,
            response_text=content,
            usage=getattr(response, "usage", None),
            finish_reason=getattr(choice, "finish_reason", "") if choice else "",
            request_id=(
                getattr(response, "_request_id", "")
                or getattr(response, "request_id", "")
                or ""
            ),
        )
        return content
    except Exception as e:
        finish_llm_call(
            trace_handle,
            status="error",
            success=False,
            error=e,
        )
        return f"抱歉，调用大模型时出现异常：{str(e)[:50]}"
