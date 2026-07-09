from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from openai import OpenAI
from .. import config as host_config
from ..llm_trace import begin_llm_call, finish_llm_call, infer_llm_call_name


_ENV_LOADED = False


def _load_env_file() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return

    # Always load the post service's own .env file.
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)

    _ENV_LOADED = True


def _get_client() -> OpenAI:
    _load_env_file()
    # Prefer the host service's legacy config path so migrated gift logic follows
    # the same configuration convention as the original project.
    api_key = (
        getattr(host_config, "API_KEY", "") or
        os.getenv("API_KEY") or
        os.getenv("DEEPSEEK_API_KEY")
    )
    base_url = (
        getattr(host_config, "API_BASE_URL", "") or
        os.getenv("API_BASE_URL") or
        os.getenv("DEEPSEEK_BASE_URL")
        or "https://api.deepseek.com"
    )

    if not api_key:
        raise RuntimeError(
            "Missing API key in environment variables. "
            "Set DEEPSEEK_API_KEY or API_KEY."
        )

    return OpenAI(api_key=api_key, base_url=base_url)


def _resolve_model() -> str:
    return (
        getattr(host_config, "TEXT_MODEL_NAME", "") or
        os.getenv("TEXT_MODEL_NAME") or
        os.getenv("DEEPSEEK_MODEL") or
        "deepseek-chat"
    )


def _response_metadata(response: Any) -> Dict[str, Any]:
    choice = response.choices[0] if getattr(response, "choices", None) else None
    return {
        "usage": getattr(response, "usage", None),
        "finish_reason": getattr(choice, "finish_reason", "") if choice else "",
        "request_id": (
            getattr(response, "_request_id", "")
            or getattr(response, "request_id", "")
            or ""
        ),
    }


def call_json(
    prompt: str,
    system_prompt: Optional[str] = None,
    temperature: float = 0.1,
    call_name: Optional[str] = None,
) -> Dict[str, Any]:
    _load_env_file()
    model = _resolve_model()
    trace_handle = begin_llm_call(
        call_name=call_name or infer_llm_call_name(stack_depth=2),
        call_type="json",
        model=model,
        temperature=temperature,
        prompt=prompt,
        system_prompt=system_prompt,
    )
    try:
        client = _get_client()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content.strip()
        metadata = _response_metadata(response)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            finish_llm_call(
                trace_handle,
                status="invalid_json",
                success=False,
                response_text=content,
                error=exc,
                **metadata,
            )
            return {}

        finish_llm_call(
            trace_handle,
            status="success",
            success=True,
            response_text=content,
            **metadata,
        )
        return parsed
    except Exception as exc:
        finish_llm_call(
            trace_handle,
            status="error",
            success=False,
            error=exc,
        )
        raise


def call_text(
    prompt: str,
    system_prompt: Optional[str] = None,
    temperature: float = 0.7,
    call_name: Optional[str] = None,
) -> str:
    _load_env_file()
    model = _resolve_model()
    trace_handle = begin_llm_call(
        call_name=call_name or infer_llm_call_name(stack_depth=2),
        call_type="text",
        model=model,
        temperature=temperature,
        prompt=prompt,
        system_prompt=system_prompt,
    )
    try:
        client = _get_client()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )
        content = response.choices[0].message.content.strip()
        finish_llm_call(
            trace_handle,
            status="success",
            success=True,
            response_text=content,
            **_response_metadata(response),
        )
        return content
    except Exception as exc:
        finish_llm_call(
            trace_handle,
            status="error",
            success=False,
            error=exc,
        )
        raise
