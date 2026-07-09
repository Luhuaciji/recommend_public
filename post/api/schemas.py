"""
Request and response schemas for the CDF chat API.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


def _parse_query_payload(raw_query: Any) -> Dict[str, Any]:
    if isinstance(raw_query, dict):
        return raw_query
    if not isinstance(raw_query, str) or not raw_query.strip():
        return {}
    try:
        parsed = json.loads(raw_query)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_query_text(raw_query: Any) -> str:
    payload = _parse_query_payload(raw_query)
    if payload:
        return str(payload.get("queryText", "") or "")
    return str(raw_query or "")


class CDFRequest(BaseModel):
    QueryExtends: Optional[Dict[str, Any]] = None
    ConversationID: str
    taskId: str
    Query: str
    ChatHistories: List[Dict[str, Any]] = []
    UserID: str
    IsInterrupt: bool = False

    parsed_query_text: str = Field(default="", description="Parsed queryText from Query")
    clean_llm_history: List[Dict[str, Any]] = Field(default_factory=list)
    chatHistoriesSnapshot: str = Field(default="[]")
    query_payload: Dict[str, Any] = Field(default_factory=dict)
    account_id: str = Field(default="")
    query_token: str = Field(default="")
    message_id: Optional[Any] = Field(default=None)

    @model_validator(mode="after")
    def auto_parse_nested_fields(self) -> "CDFRequest":
        query_dict = _parse_query_payload(self.Query)
        self.query_payload = query_dict
        self.parsed_query_text = str(query_dict.get("queryText", "") or "") if query_dict else ""
        self.account_id = str(query_dict.get("accountId", "") or "").strip()
        self.query_token = str(query_dict.get("token", "") or "").strip()
        self.message_id = query_dict.get("MessageID")

        cleaned_history: List[Dict[str, Any]] = []
        sanitized_history: List[Dict[str, Any]] = []
        for hist in self.ChatHistories:
            q_text = _extract_query_text(hist.get("query", "")).strip()
            a_raw = str(hist.get("answer", "") or "")
            a_clean = re.sub(r"```json\s*(.*?)\s*```", "", a_raw, flags=re.DOTALL).strip()

            if q_text or a_clean:
                sanitized_history.append({"query": q_text, "answer": a_clean})

            if q_text and a_clean:
                cleaned_history.append({"role": "user", "content": q_text})
                cleaned_history.append({"role": "assistant", "content": a_clean})

        self.clean_llm_history = cleaned_history
        self.chatHistoriesSnapshot = json.dumps(
            sanitized_history,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return self


class CDFResponse(BaseModel):
    taskId: str
    data: List[Dict[str, Any]]
    isGiftIntention: bool
    isInterrupted: bool = False


class FeedbackRequest(BaseModel):
    ConversationID: str
    taskId: str
    LikeType: int
