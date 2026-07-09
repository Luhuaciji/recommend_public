from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .llm_client import call_json
from .models import GiftRecommendationState


BOUNDARY_LLM_CONFIDENCE_THRESHOLD = 0.76

BOUNDARY_SYSTEM_PROMPT = """
You are a task-boundary detector for a Chinese gift recommendation flow.

Decide whether the user's latest message continues the current gift task or starts/corrects a different gift task.

Actions:
- continue_update: same recipient/task; user adds budget, preference, taboo, timing, style, or other filters.
- category_switch: same gift task; user wants a different product category.
- restart_flow: user starts another gift task for a different recipient/event.
- correct_current_task: user corrects the current gift task, e.g. "not mom, boyfriend".

Rules:
1. A new explicit recipient that conflicts with current recipient usually means restart_flow.
2. Phrases like "also", "another", "by the way", "help me give X too" plus a recipient usually mean restart_flow.
3. Phrases like "not X, it is Y", "I said it wrong", "change recipient to Y" mean correct_current_task.
4. Budget-only, preference-only, taboo-only, style-only, delivery-time-only updates are continue_update.
5. Product-category changes such as perfume/watch/skincare are category_switch, not restart_flow.
6. If unsure, choose continue_update.

Return strict JSON:
{
  "action": "continue_update",
  "confidence": 0.0,
  "reason": "short reason",
  "latest_frame": {
    "recipient_relation": "",
    "occasion": "",
    "budget_max": null,
    "time_expression": ""
  }
}
""".strip()


@dataclass
class GiftTaskBoundaryDecision:
    action: str = "continue_update"
    confidence: float = 0.0
    reason: str = ""
    latest_frame: Dict[str, Any] = field(default_factory=dict)
    source: str = "rule"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


RELATION_ALIASES: Tuple[Tuple[str, Tuple[str, ...], str], ...] = (
    ("mother", ("妈妈", "母亲", "老妈", "妈咪", "妈"), "妈妈"),
    ("father", ("爸爸", "父亲", "老爸", "爸"), "爸爸"),
    ("boyfriend", ("男朋友", "男友", "男票"), "男朋友"),
    ("girlfriend", ("女朋友", "女友", "女票"), "女朋友"),
    ("husband", ("老公", "丈夫", "先生"), "老公"),
    ("wife", ("老婆", "妻子", "太太"), "老婆"),
    ("partner", ("对象", "伴侣", "爱人", "恋人"), "伴侣"),
    ("grandmother", ("奶奶", "外婆", "姥姥", "祖母"), "奶奶"),
    ("grandfather", ("爷爷", "外公", "姥爷", "祖父"), "爷爷"),
    ("child", ("孩子", "小孩", "儿子", "女儿", "宝宝"), "孩子"),
    ("friend", ("朋友", "闺蜜", "兄弟", "哥们"), "朋友"),
    ("colleague", ("同事", "领导", "客户", "老师"), "同事"),
)

OCCASION_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("生日", ("生日", "过生日")),
    ("情人节", ("情人节", "520", "七夕")),
    ("母亲节", ("母亲节",)),
    ("父亲节", ("父亲节",)),
    ("春节", ("春节", "过年", "新年")),
    ("中秋", ("中秋", "中秋节")),
    ("乔迁", ("乔迁", "搬家")),
    ("结婚", ("结婚", "婚礼")),
    ("探望", ("探望", "看望", "拜访")),
)

NEW_TASK_MARKERS = (
    "另外",
    "另一个",
    "还有",
    "也给",
    "也帮",
    "再帮",
    "顺便",
    "再给",
    "还想给",
)

CORRECTION_MARKERS = (
    "不是",
    "不送",
    "说错",
    "搞错",
    "改成",
    "换成",
    "应该是",
    "其实是",
)

GIFT_NEED_MARKERS = (
    "生日",
    "礼物",
    "送",
    "预算",
    "推荐",
    "过生日",
    "下个月",
    "马上",
    "左右",
)


def detect_gift_task_boundary(
    state: GiftRecommendationState,
    stage: str,
    user_text: str,
    pending_categories: Optional[List[str]] = None,
) -> GiftTaskBoundaryDecision:
    text = (user_text or "").strip()
    if not text or not _has_active_task(state, stage, pending_categories):
        return GiftTaskBoundaryDecision(reason="no active gift task")

    latest_frame = extract_latest_task_frame(text)
    current_frame = _extract_current_task_frame(state)
    rule_decision = _decide_by_rule(text, latest_frame, current_frame)
    if rule_decision:
        return rule_decision

    if not _should_try_llm(text, latest_frame):
        return GiftTaskBoundaryDecision(
            confidence=0.55,
            reason="latest message does not contain enough new-task signals",
            latest_frame=latest_frame,
        )

    return _decide_by_llm(state, stage, text, latest_frame, current_frame)


def extract_latest_task_frame(text: str) -> Dict[str, Any]:
    relation_group, relation_value = _extract_relation(text)
    occasion = _extract_occasion(text)
    budget_min, budget_max = _extract_budget(text)
    return {
        "recipient_relation": relation_value,
        "recipient_relation_group": relation_group,
        "occasion": occasion,
        "budget_min": budget_min,
        "budget_max": budget_max,
        "time_expression": _extract_time_expression(text),
    }


def _decide_by_rule(
    text: str,
    latest_frame: Dict[str, Any],
    current_frame: Dict[str, Any],
) -> Optional[GiftTaskBoundaryDecision]:
    latest_group = str(latest_frame.get("recipient_relation_group") or "")
    current_group = str(current_frame.get("recipient_relation_group") or "")
    latest_relation = str(latest_frame.get("recipient_relation") or "")
    current_relation = str(current_frame.get("recipient_relation") or "")

    if not latest_group:
        return None

    has_conflict = bool(
        current_group and not _relation_groups_compatible(current_group, latest_group)
    )
    has_new_task_marker = _contains_any(text, NEW_TASK_MARKERS)
    has_correction_marker = _contains_any(text, CORRECTION_MARKERS)

    if (
        current_group
        and _relation_groups_compatible(current_group, latest_group)
        and not has_new_task_marker
    ):
        return GiftTaskBoundaryDecision(
            action="continue_update",
            confidence=0.78,
            reason=f"latest recipient {latest_relation} matches current recipient",
            latest_frame=latest_frame,
        )

    if has_conflict and has_correction_marker:
        return GiftTaskBoundaryDecision(
            action="correct_current_task",
            confidence=0.94,
            reason=(
                f"latest recipient {latest_relation} corrects current recipient "
                f"{current_relation or current_group}"
            ),
            latest_frame=latest_frame,
        )

    if has_conflict:
        return GiftTaskBoundaryDecision(
            action="restart_flow",
            confidence=0.92,
            reason=(
                f"latest recipient {latest_relation} conflicts with current recipient "
                f"{current_relation or current_group}"
            ),
            latest_frame=latest_frame,
        )

    if has_new_task_marker and latest_group:
        return GiftTaskBoundaryDecision(
            action="restart_flow",
            confidence=0.88,
            reason=f"latest message uses a new-task marker with recipient {latest_relation}",
            latest_frame=latest_frame,
        )

    return None


def _decide_by_llm(
    state: GiftRecommendationState,
    stage: str,
    user_text: str,
    latest_frame: Dict[str, Any],
    current_frame: Dict[str, Any],
) -> GiftTaskBoundaryDecision:
    prompt = {
        "current_stage": stage,
        "current_task_frame": current_frame,
        "latest_rule_frame": latest_frame,
        "recent_history": _recent_history(state),
        "user_latest_message": user_text,
    }
    try:
        result = call_json(
            prompt=json.dumps(prompt, ensure_ascii=False, indent=2),
            system_prompt=BOUNDARY_SYSTEM_PROMPT,
            temperature=0.1,
        )
    except Exception as exc:
        return GiftTaskBoundaryDecision(
            confidence=0.0,
            reason=f"boundary llm failed: {exc}",
            latest_frame=latest_frame,
            source="llm_error",
        )

    if not isinstance(result, dict):
        return GiftTaskBoundaryDecision(
            confidence=0.0,
            reason="boundary llm returned invalid payload",
            latest_frame=latest_frame,
            source="llm",
        )

    action = str(result.get("action", "") or "").strip()
    if action not in {
        "continue_update",
        "category_switch",
        "restart_flow",
        "correct_current_task",
    }:
        action = "continue_update"

    try:
        confidence = float(result.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    if action in {"restart_flow", "correct_current_task"} and confidence < BOUNDARY_LLM_CONFIDENCE_THRESHOLD:
        action = "continue_update"

    llm_frame = result.get("latest_frame") if isinstance(result.get("latest_frame"), dict) else {}
    return GiftTaskBoundaryDecision(
        action=action,
        confidence=confidence,
        reason=str(result.get("reason", "") or "").strip(),
        latest_frame={**latest_frame, **llm_frame},
        source="llm",
    )


def _extract_current_task_frame(state: GiftRecommendationState) -> Dict[str, Any]:
    relation = str(_get_slot_value(state, "recipient_relation") or "")
    relation_group, relation_value = _normalize_relation(relation)
    return {
        "recipient_relation": relation_value or relation,
        "recipient_relation_group": relation_group,
        "occasion": str(_get_slot_value(state, "occasion") or ""),
        "budget_min": _get_slot_value(state, "budget_min"),
        "budget_max": _get_slot_value(state, "budget_max"),
        "recipient_gender": str(_get_slot_value(state, "recipient_gender") or ""),
        "recipient_age": str(_get_slot_value(state, "recipient_age") or ""),
        "selected_category": getattr(getattr(state, "selected_category", None), "category_name", "") or "",
    }


def _extract_relation(text: str) -> Tuple[str, str]:
    normalized = str(text or "")
    matches: List[Tuple[int, int, str, str]] = []
    for group, aliases, canonical in RELATION_ALIASES:
        for alias in aliases:
            if not alias:
                continue
            index = normalized.find(alias)
            if index >= 0:
                matches.append((index, len(alias), group, canonical))
    if not matches:
        return "", ""
    matches.sort(key=lambda item: (item[0] + item[1], item[1]))
    _, __, group, canonical = matches[-1]
    return group, canonical


def _normalize_relation(value: str) -> Tuple[str, str]:
    value = (value or "").strip()
    if not value:
        return "", ""
    for group, aliases, canonical in RELATION_ALIASES:
        if value == canonical or any(alias and alias in value for alias in aliases):
            return group, canonical
    return value, value


def _relation_groups_compatible(current_group: str, latest_group: str) -> bool:
    if current_group == latest_group:
        return True
    partner_groups = {"partner", "boyfriend", "girlfriend", "husband", "wife"}
    return current_group in partner_groups and latest_group in partner_groups


def _extract_occasion(text: str) -> str:
    for occasion, keywords in OCCASION_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return occasion
    return ""


def _extract_budget(text: str) -> Tuple[Optional[float], Optional[float]]:
    match = re.search(
        r"(?:不超过|别超过|不要超过|最多|最高|控制在)\s*(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?",
        text,
    )
    if match:
        return 0.0, float(match.group(1))

    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?\s*(?:左右|大概|上下)", text)
    if match:
        value = float(match.group(1))
        return round(value * 0.8), round(value * 1.2)

    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:以内|以下|内)", text)
    if match:
        return 0.0, float(match.group(1))

    match = re.search(r"预算\s*(\d+(?:\.\d+)?)", text)
    if match:
        value = float(match.group(1))
        return round(value * 0.8), round(value * 1.2)

    return None, None


def _extract_time_expression(text: str) -> str:
    time_keywords = (
        "马上",
        "今天",
        "明天",
        "后天",
        "这周",
        "下周",
        "这个月",
        "下个月",
        "月底",
        "年前",
        "节前",
    )
    matched = [keyword for keyword in time_keywords if keyword in text]
    return "，".join(matched)


def _should_try_llm(text: str, latest_frame: Dict[str, Any]) -> bool:
    if latest_frame.get("recipient_relation"):
        return True
    return (
        _contains_any(text, NEW_TASK_MARKERS)
        or _contains_any(text, CORRECTION_MARKERS)
    ) and _contains_any(text, GIFT_NEED_MARKERS)


def _has_active_task(
    state: GiftRecommendationState,
    stage: str,
    pending_categories: Optional[List[str]],
) -> bool:
    if stage and stage != "init":
        return True
    if pending_categories:
        return True
    if getattr(state, "selected_category", None):
        return True
    for slot in getattr(state, "filled_slots", {}).values():
        if getattr(slot, "is_filled", False) and getattr(slot, "value", None) is not None:
            return True
    return False


def _recent_history(state: GiftRecommendationState, limit: int = 6) -> List[Dict[str, str]]:
    history = []
    for msg in (getattr(state, "chat_history", []) or [])[-limit:]:
        role = str(msg.get("role", "") or "").strip()
        content = str(msg.get("content", "") or "").strip()
        if role and content:
            history.append({"role": role, "content": content})
    return history


def _get_slot_value(state: GiftRecommendationState, slot_name: str) -> Any:
    slot = getattr(state, "filled_slots", {}).get(slot_name)
    return getattr(slot, "value", None) if slot else None


def _contains_any(text: str, keywords: Tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)
