"""
大模型客户端：只负责和 LLM 通信
"""
from typing import List, Dict, Any
from openai import AsyncOpenAI
from .config import API_BASE_URL, API_KEY, TEXT_MODEL_NAME, LLM_TIMEOUT, LLM_MAX_RETRIES

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
    try:
        response = await client.chat.completions.create(
            model=TEXT_MODEL_NAME,
            messages=messages,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"抱歉，调用大模型时出现异常：{str(e)[:50]}"
