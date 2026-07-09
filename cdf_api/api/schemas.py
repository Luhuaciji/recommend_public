"""
请求/响应模型定义
✅ 所有协议修改都来这个文件
"""
from pydantic import BaseModel, Field, model_validator
from typing import List, Dict, Any, Optional
import json
import re

class CDFRequest(BaseModel):
    QueryExtends: Optional[Dict[str, Any]] = None
    ConversationID: str
    taskId: str
    Query: str
    ChatHistories: List[Dict[str, Any]] = []
    UserID: str

    parsed_query_text: str = Field(default="", description="解析后的纯文本意图")
    clean_llm_history: List[Dict[str, Any]] = Field(default_factory=list, description="清洗后的大模型历史")

    # 【新增】前端 ChatHistories 的完整快照
    # 用于判断“前端历史是否推进”，从而推断上一轮是否被中断
    chatHistoriesSnapshot: str = Field(default="[]", description="前端 ChatHistories 的完整快照")

    @model_validator(mode='after')
    def auto_parse_nested_fields(self) -> "CDFRequest":
        try:
            query_dict = json.loads(self.Query)
            self.parsed_query_text = query_dict.get("queryText", "")
        except json.JSONDecodeError:
            self.parsed_query_text = ""

        cleaned_history = []
        for hist in self.ChatHistories:
            q_raw_json_str = hist.get("query", "")
            a_raw = hist.get("answer", "")
            a_clean = re.sub(r'```json\s*(.*?)\s*```', '', a_raw, flags=re.DOTALL).strip()

            if q_raw_json_str and a_clean:
                cleaned_history.append({"role": "user", "content": q_raw_json_str})
                cleaned_history.append({"role": "assistant", "content": a_clean})

        self.clean_llm_history = cleaned_history
        self.chatHistoriesSnapshot = json.dumps(
            self.ChatHistories,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True
        )
        return self

class CDFResponse(BaseModel):
    taskId: str
    data: List[Dict[str, Any]]
    isGiftIntention: bool

class FeedbackRequest(BaseModel):
    ConversationID: str
    taskId: str
    LikeType: int  # 1 = 点赞, -1 = 点踩
