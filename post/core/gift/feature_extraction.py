import copy
import hashlib
import json
import re
from datetime import datetime
from typing import Any, Dict, Optional

from .models import GiftRecommendationState, GiftSlot
from .llm_client import call_json

BUDGET_EXTRACTION_PROMPT = """你是预算提取器。从用户输入中提取预算金额范围（单位：元）。

规则：
- "X元" / "X块" / "￥X" / "¥X" → budget_min=round(X*0.8), budget_max=round(X*1.2)
- "X以内" / "不超过X" / "X以下" / "低于X" / "不到X" → budget_max=X, budget_min不填
- "X以上" / "高于X" / "至少X" / "超过X" / "最低X" → budget_min=X, budget_max不填
- "X-Y" / "X到Y" / "X至Y" → budget_min=小值, budget_max=大值
- "人均X" / "每人X" → budget_min=round(X*0.8), budget_max=round(X*1.2)
- "不要太贵" / "便宜点" / "实惠" → budget_max=200
- "贵一点" / "高档" / "好一点/好点的" → budget_min=300
- "随便" / "无所谓" / 没提到预算 → budget_min=null, budget_max=null

只返回JSON，不要额外输出：
{"budget_min": float|null, "budget_max": float|null, "mentioned": bool}
""".strip()

BUDGET_EXTRACTION_PROMPT += """

Important override:
- Relative expressions such as "\u9884\u7b97\u518d\u9ad8\u70b9", "\u8d35\u4e00\u70b9", "\u597d\u4e00\u70b9", "\u66f4\u597d\u7684" require an existing budget or recommendation-price anchor.
- If no anchor is provided in the input, do not map these expressions to 300. Return mentioned=false instead.
- Never treat "\u66f4\u597d\u7684/\u597d\u4e00\u70b9/\u8d35\u4e00\u70b9" as a fixed budget_min=300 rule.
""".strip()

INFERENCE_PROMPT = """你是一个专业的送礼场景信息推理专家。

你的任务是：根据输入中已经明确提取出的送礼槽位，只补全少量高确定性的低风险槽位。

### 输入说明
用户会给你一个已知槽位字典，其中每个字段都表示已经明确或高置信提取出的信息。
你只能基于这些已知槽位进行确定性推理，不能脱离已知信息补充用户没有表达过的场景、偏好、禁忌或时间。

### 你可以推理的槽位：
1. recipient_age：收礼人年龄段
2. recipient_gender：收礼人性别

### 推理规则：
1. 只允许推理 recipient_age 和 recipient_gender。
2. 不要推理 occasion / recipient_preferences / delivery_time / taboo / budget_min / budget_max / recipient_relation。
3. occasion、recipient_preferences、delivery_time、taboo 必须来自用户明确表达，不能从收礼人关系、年龄、性别或通用人群画像中猜测。
4. 禁止示例：
   - 不能因为“收礼人是父亲”就推理“父亲节”。
   - 不能因为“40-70岁男性”就推理“养生、数码、运动”等偏好。
   - 不能因为“母亲/父亲/伴侣”等关系就推理具体节日或兴趣方向。
5. 每条推理必须给出：
   - slot_name
   - value
   - reasoning：说明为什么这样推理
   - confidence：0.0 到 1.0
6. 如果信息不足，请不要输出该槽位
7. 如果一个槽位有多种可能，不要强行输出
8. confidence 小于 0.8 的结果不要输出
9. 必须严格返回 JSON，不要输出任何额外内容

### 置信度参考：
- 1.0：几乎确定，例如“母亲 -> 女”
- 0.8~0.9：高度合理，例如“父亲 -> 中老年男性”
- <0.8：不要输出

### 输出格式示例：
{
  "inferred_slots": [
    {
      "slot_name": "recipient_gender",
      "value": "女",
      "reasoning": "收礼对象是母亲，母亲的性别可以确定为女性。",
      "confidence": 1.0
    },
    {
      "slot_name": "recipient_age",
      "value": "40-60岁",
      "reasoning": "收礼对象是母亲，通常属于中年到中老年年龄段，因此可粗略推断为40-60岁。",
      "confidence": 0.82
    }
  ]
}
"""

INFERENCE_ALLOWED_SLOTS = {"recipient_age", "recipient_gender"}
INFERENCE_MIN_CONFIDENCE = {
    "recipient_age": 0.8,
    "recipient_gender": 0.9,
}
SLOT_INFERENCE_CACHE_VERSION = "slot-inference-v1"
SLOT_INFERENCE_CACHE_MAX_ENTRIES = 16
DEFAULT_RELATIVE_BUDGET_ANCHOR = 500.0
RELATIVE_BUDGET_INCREASE_RATIO = 1.5
RELATIVE_BUDGET_DECREASE_RATIO = 0.5

RELATIVE_BUDGET_INCREASE_PATTERNS = (
    re.compile(r"(?:\u9884\u7b97)?(?:\u518d)?(?:\u9ad8|\u8d35)(?:\u4e00)?\u70b9"),
    re.compile(r"(?:\u63d0\u9ad8|\u4e0a\u8c03|\u52a0)(?:\u4e00)?\u70b9(?:\u9884\u7b97)?"),
    re.compile(r"(?:\u9884\u7b97).{0,4}(?:\u63d0\u9ad8|\u4e0a\u8c03|\u52a0)"),
    re.compile(r"(?:\u9ad8\u6863(?:\u4e00)?\u70b9|\u6863\u6b21\u9ad8(?:\u4e00)?\u70b9)"),
    re.compile(r"(?:\u9884\u7b97|\u4ef7\u4f4d).{0,6}(?:\u66f4\u597d|\u597d\u4e00\u70b9|\u597d\u70b9|\u9ad8\u6863)"),
)
RELATIVE_BUDGET_DECREASE_PATTERNS = (
    re.compile(r"(?:\u9884\u7b97)?(?:\u518d)?(?:\u4f4e|\u5c11)(?:\u4e00)?\u70b9"),
    re.compile(r"(?:\u4fbf\u5b9c|\u5b9e\u60e0)(?:\u4e00)?\u70b9"),
    re.compile(r"(?:\u964d\u4f4e|\u4e0b\u8c03|\u51cf)(?:\u4e00)?\u70b9(?:\u9884\u7b97)?"),
    re.compile(r"(?:\u9884\u7b97).{0,4}(?:\u964d\u4f4e|\u4e0b\u8c03|\u51cf|\u4f4e|\u5c11)"),
)

SELF_RECIPIENT_PATTERNS = (
    re.compile(r"(?:\u7ed9|\u9001\u7ed9|\u4e70\u7ed9|\u6311\u7ed9|\u9009\u7ed9)(?:\u6211\u81ea\u5df1|\u81ea\u5df1|\u672c\u4eba)"),
    re.compile(r"(?:\u9001|\u4e70|\u5956\u52b1|\u72b8\u52b3|\u5b89\u6392|\u5165\u624b|\u6311|\u9009|\u63a8\u8350).{0,4}(?:\u6211\u81ea\u5df1|\u81ea\u5df1|\u672c\u4eba)"),
    re.compile(r"(?:\u81ea\u7528|\u81ea\u5df1\u7528|\u6211\u81ea\u5df1\u7528|\u9001\u81ea\u5df1|\u7ed9\u81ea\u5df1|\u4e70\u7ed9\u81ea\u5df1)"),
)
SELF_RECIPIENT_NEGATION_PATTERNS = (
    re.compile(r"(?:\u4e0d\u662f|\u4e0d|\u522b|\u4e0d\u8981).{0,4}(?:\u7ed9|\u9001\u7ed9|\u9001|\u4e70\u7ed9|\u4e70).{0,4}(?:\u6211\u81ea\u5df1|\u81ea\u5df1|\u672c\u4eba)"),
)

SLOT_EXTRACTION_PROMPT = """你是一个专业的送礼场景槽位提取专家，请从用户的对话历史中提取以下送礼相关的槽位信息。

### 可提取的槽位列表：
#### 高优先级槽位（必须优先提取）：
1. recipient_relation：收礼人和用户的关系
2. occasion：送礼场景
3. budget_min：预算最小值，单位：元
4. budget_max：预算最大值，单位：元
5. recipient_preferences：收礼人的喜好、习惯或信仰，比如：运动, 美妆, 数码, 读书, 养生, 美食, 信佛, 禅修, 茶道, 书法, 音乐, 旅行, 手工等
6. recipient_gender：收礼人性别

#### 低优先级槽位：
7. recipient_age：收礼人年龄段
8. delivery_time：需要送达的时间
9. taboo：禁忌信息。如果收礼人有宗教信仰（如信佛），可推断禁忌：素食者应避免含动物成分的食品，佛教徒通常避免酒类

### 提取规则：
1. 只提取用户明确提到的信息，不要编造
2. 每个提取的槽位需要给出置信度，1.0表示完全确定，0.0表示完全不确定
3. 必须严格返回JSON格式，不要有其他任何额外内容
4. 请尽可能全地提取信息，不要有任何遗漏，特别是送礼对象、性别、年龄等信息
5. 如果输入中出现 assistant / 助手 的回复内容，不要从助手回复里提取槽位；只能从用户表达中提取。
6. 不要把系统推荐理由、商品标签、候选品类说明当成用户偏好或送礼场景。
7. 预算提取规则（重要）：
   - "预算X元" / "X块预算" → budget_min=X*0.8, budget_max=X*1.2（取整）
   - "X以内" / "不超过X" → budget_min=0, budget_max=X
   - "人均X" / "每人X" / "每个人X" → budget_min=X*0.8, budget_max=X*1.2（取整）
   - "X-Y元" / "X到Y块" → budget_min=X, budget_max=Y
   - "至少X元" / "X元以上" → budget_min=X, 不填budget_max
   - "X元左右" / "大概X" → budget_min=X*0.8, budget_max=X*1.2（取整）
   - 只有用户明确说了"正好X元" / "就要X块的"，才设 budget_min=X, budget_max=X

### 输出格式示例：
{
  "filled_slots": [
    {"slot_name": "recipient_relation", "value": "伴侣", "confidence": 1.0},
    {"slot_name": "occasion", "value": "情人节", "confidence": 0.95}
  ]
}
"""

SLOT_EXTRACTION_PROMPT += """

Additional extractable slots:
- recipient_location: 收礼人地点。只有用户明确表达收礼人所在地、收货地、寄送地时才提取。
- user_location: 用户地点。只有用户明确表达自己所在地、出发地、当前位置时才提取。

Do not extract user_gender or current_season from user text. They are system-context slots.
"""


def feature_extraction(state: GiftRecommendationState, user_text: str) -> GiftRecommendationState:
    _ensure_slots(state)
    slots_hash_before_update = _known_slots_hash(_collect_known_slots(state))
    chat_history_text = _build_chat_history_text(state, user_text)

    result = call_json(
        prompt=(
            f"对话历史：\n{chat_history_text}\n\n"
            "请提取上面对话中的送礼相关槽位信息。"
        ),
        system_prompt=SLOT_EXTRACTION_PROMPT,
        temperature=0.1,
    )

    filled_slots = result.get("filled_slots", []) if isinstance(result, dict) else []
    for slot_data in filled_slots:
        slot_name = slot_data.get("slot_name")
        if slot_name not in state.filled_slots:
            continue

        confidence = float(slot_data.get("confidence", 0.0) or 0.0)
        if confidence < 0.7:
            continue

        value = _normalize_slot_value(slot_name, slot_data.get("value"))
        if value is None:
            continue

        state.filled_slots[slot_name].value = value
        state.filled_slots[slot_name].is_filled = True

    budget_update = _extract_budget(user_text, state=state)
    if budget_update.get("mentioned"):
        _apply_budget_update(state, budget_update)

    slots_hash_after_update = _known_slots_hash(_collect_known_slots(state))
    infer_result = infer_slots_from_state(
        state,
        slot_state_changed=slots_hash_before_update != slots_hash_after_update,
    )
    _save_inference_results(state, infer_result)

    return state


def extract_current_turn_slot_updates(
    state: GiftRecommendationState,
    user_text: str,
) -> Dict[str, Any]:
    _ensure_slots(state)
    current_text = (user_text or "").strip()
    if not current_text:
        return {
            "slot_updates": {},
            "budget_update": {"mentioned": False},
            "raw_filled_slots": [],
        }

    result = call_json(
        prompt=(
            f"用户本轮输入：\n{current_text}\n\n"
            "请只提取这条用户输入中明确表达的送礼相关槽位信息。"
        ),
        system_prompt=SLOT_EXTRACTION_PROMPT,
        temperature=0.1,
    )

    raw_filled_slots = result.get("filled_slots", []) if isinstance(result, dict) else []
    slot_updates: Dict[str, Dict[str, Any]] = {}
    for slot_data in raw_filled_slots:
        slot_name = slot_data.get("slot_name")
        if slot_name not in state.filled_slots:
            continue

        try:
            confidence = float(slot_data.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < 0.7:
            continue

        value = _normalize_slot_value(slot_name, slot_data.get("value"))
        if value is None:
            continue
        slot_updates[slot_name] = {
            "value": value,
            "confidence": confidence,
            "source": "llm_current_turn",
        }

    budget_update = _extract_budget(current_text, state=state)
    if budget_update.get("mentioned"):
        budget_source = budget_update.get("source", "rule_budget")
        slot_updates["budget_min"] = {
            "value": budget_update.get("budget_min"),
            "confidence": 1.0,
            "source": budget_source,
        }
        slot_updates["budget_max"] = {
            "value": budget_update.get("budget_max"),
            "confidence": 1.0,
            "source": budget_source,
        }

    return {
        "slot_updates": slot_updates,
        "budget_update": budget_update,
        "raw_filled_slots": raw_filled_slots,
    }


def apply_current_turn_slot_updates(
    state: GiftRecommendationState,
    extraction_result: Dict[str, Any],
) -> Dict[str, Any]:
    _ensure_slots(state)
    extraction_result = extraction_result or {}
    slot_updates = extraction_result.get("slot_updates", {}) or {}
    budget_update = extraction_result.get("budget_update", {}) or {}
    applied: Dict[str, Dict[str, Any]] = {}

    if budget_update.get("mentioned"):
        applied.update(_apply_budget_update(state, budget_update))

    for slot_name, update in slot_updates.items():
        if slot_name in {"budget_min", "budget_max"}:
            continue
        if slot_name not in state.filled_slots:
            continue
        value = update.get("value") if isinstance(update, dict) else update
        value = _normalize_slot_value(slot_name, value)
        if value is None:
            continue
        slot = state.filled_slots[slot_name]
        old_value = slot.value
        old_is_filled = slot.is_filled
        slot.value = value
        slot.is_filled = True
        applied[slot_name] = {
            "old_value": old_value,
            "new_value": value,
            "source": update.get("source", "llm_current_turn") if isinstance(update, dict) else "llm_current_turn",
            "changed": old_value != value or not old_is_filled,
        }

    has_actual_slot_updates = any(
        bool(update.get("changed"))
        for update in applied.values()
        if isinstance(update, dict)
    )

    result = {
        "slot_updates": slot_updates,
        "budget_update": budget_update,
        "applied": applied,
        "has_actual_slot_updates": has_actual_slot_updates,
        "raw_filled_slots": extraction_result.get("raw_filled_slots", []) or [],
    }

    infer_result = infer_slots_from_state(
        state,
        slot_state_changed=has_actual_slot_updates,
    )
    _save_inference_results(state, infer_result)
    result["inference_results"] = infer_result.get("inferred_slots", [])
    result["inference_cache_hit"] = bool(infer_result.get("cache_hit"))
    result["inference_cache_status"] = str(
        infer_result.get("cache_status", "") or ""
    )

    try:
        state.current_turn_slot_updates = result
    except Exception:
        pass
    return result


def apply_system_context_slots(
    state: GiftRecommendationState,
    member_profile: Optional[Dict[str, Any]] = None,
    query_context: Optional[Dict[str, Any]] = None,
    user_text: str = "",
) -> Dict[str, Any]:
    _ensure_slots(state)
    member_profile = member_profile or {}
    query_context = query_context or {}
    applied: Dict[str, Dict[str, Any]] = {}

    gender = str(member_profile.get("gender", "") or "").strip()
    if gender in {"\u7537", "\u5973"}:
        applied["user_gender"] = _set_slot_value(
            state,
            "user_gender",
            gender,
            source="open_platform_member_info",
        )
        if _is_self_recipient_expression(user_text):
            recipient_slot = state.filled_slots.get("recipient_gender")
            existing_recipient_gender = (
                str(getattr(recipient_slot, "value", "") or "").strip()
                if recipient_slot is not None
                else ""
            )
            if not existing_recipient_gender or existing_recipient_gender == gender:
                applied["recipient_gender"] = _set_slot_value(
                    state,
                    "recipient_gender",
                    gender,
                    source="self_recipient_user_gender",
                )
            else:
                applied["recipient_gender_self_recipient_skipped"] = {
                    "old_value": existing_recipient_gender,
                    "new_value": gender,
                    "source": "self_recipient_user_gender",
                    "applied": False,
                    "reason": "existing_recipient_gender_conflict",
                }

    for context_key, slot_name in (
        ("recipient_location", "recipient_location"),
        ("user_location", "user_location"),
    ):
        value = str(query_context.get(context_key, "") or "").strip()
        if value:
            applied[slot_name] = _set_slot_value(
                state,
                slot_name,
                value,
                source="query_context",
            )

    applied["current_season"] = _set_slot_value(
        state,
        "current_season",
        _current_season(),
        source="system_date",
    )

    if isinstance(getattr(state, "current_turn_slot_updates", None), dict):
        state.current_turn_slot_updates["system_context_applied"] = applied
    if isinstance(getattr(state, "turn_understanding", None), dict):
        state.turn_understanding["system_context_slots"] = applied
    return applied


def _is_self_recipient_expression(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text or "")
    if not normalized:
        return False
    if any(pattern.search(normalized) for pattern in SELF_RECIPIENT_NEGATION_PATTERNS):
        return False
    return any(pattern.search(normalized) for pattern in SELF_RECIPIENT_PATTERNS)


def infer_slots_from_state(
    state: GiftRecommendationState,
    *,
    slot_state_changed: Optional[bool] = None,
) -> dict[str, Any]:
    """
    基于 state 中已填槽位进行推理，返回推理结果，并在满足条件时回填槽位。

    返回格式：
    {
        "inferred_slots": [
            {
                "slot_name": "recipient_gender",
                "value": "女",
                "reasoning": "...",
                "confidence": 1.0,
                "applied": True,
                "skip_reason": None
            }
        ]
    }
    """
    _ensure_slots(state)

    known_slots = _collect_known_slots(state)
    if not known_slots:
        return {
            "inferred_slots": [],
            "cache_hit": False,
            "cache_status": "no_known_slots",
        }

    known_slots_hash = _known_slots_hash(known_slots)
    cached_record = _get_slot_inference_cache_record(state, known_slots_hash)
    if cached_record is not None:
        if cached_record.get("state_role") == "post_apply":
            state.last_slot_inference_state_hash = known_slots_hash
            return {
                "inferred_slots": [],
                "cache_hit": True,
                "cache_status": (
                    "skipped_no_slot_change"
                    if slot_state_changed is False
                    else "post_apply_state_hit"
                ),
                "known_slots_hash": known_slots_hash,
                "preserve_existing_results": True,
            }

        cached_candidates = copy.deepcopy(cached_record.get("candidates", []))
        final_results = _apply_inference_candidates(state, cached_candidates)
        post_apply_hash = _known_slots_hash(_collect_known_slots(state))
        _store_slot_inference_cache_record(
            state,
            post_apply_hash,
            {
                "state_role": "post_apply",
                "candidates": cached_candidates,
                "final_results": final_results,
            },
        )
        state.last_slot_inference_state_hash = post_apply_hash
        return {
            "inferred_slots": final_results,
            "cache_hit": True,
            "cache_status": "request_state_hit",
            "known_slots_hash": known_slots_hash,
            "post_apply_hash": post_apply_hash,
        }

    result = call_json(
        prompt=(
            "以下是当前已经确定的送礼槽位信息（JSON）：\n"
            f"{json.dumps(known_slots, ensure_ascii=False)}\n\n"
            "请基于这些已知槽位推理更多可能成立的槽位信息。"
        ),
        system_prompt=INFERENCE_PROMPT,
        temperature=0.1,
    )

    if not isinstance(result, dict) or not isinstance(result.get("inferred_slots"), list):
        return {
            "inferred_slots": [],
            "cache_hit": False,
            "cache_status": "invalid_response",
            "known_slots_hash": known_slots_hash,
        }

    inferred_slots = copy.deepcopy(result.get("inferred_slots", []))
    final_results = _apply_inference_candidates(state, inferred_slots)
    post_apply_hash = _known_slots_hash(_collect_known_slots(state))
    cache_record = {
        "candidates": inferred_slots,
        "final_results": final_results,
    }
    _store_slot_inference_cache_record(
        state,
        known_slots_hash,
        {**cache_record, "state_role": "request"},
    )
    _store_slot_inference_cache_record(
        state,
        post_apply_hash,
        {**cache_record, "state_role": "post_apply"},
    )
    state.last_slot_inference_state_hash = post_apply_hash
    return {
        "inferred_slots": final_results,
        "cache_hit": False,
        "cache_status": "llm_call",
        "known_slots_hash": known_slots_hash,
        "post_apply_hash": post_apply_hash,
    }


def _collect_known_slots(state: GiftRecommendationState) -> Dict[str, Any]:
    return {
        slot_name: slot.value
        for slot_name, slot in state.filled_slots.items()
        if getattr(slot, "is_filled", False)
        and getattr(slot, "value", None) is not None
    }


def _known_slots_hash(known_slots: Dict[str, Any]) -> str:
    canonical_payload = json.dumps(
        {
            "version": SLOT_INFERENCE_CACHE_VERSION,
            "known_slots": known_slots,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def _get_slot_inference_cache(state: GiftRecommendationState) -> Dict[str, Dict[str, Any]]:
    cache = getattr(state, "slot_inference_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        state.slot_inference_cache = cache
    return cache


def _get_slot_inference_cache_record(
    state: GiftRecommendationState,
    known_slots_hash: str,
) -> Optional[Dict[str, Any]]:
    cache = _get_slot_inference_cache(state)
    record = cache.pop(known_slots_hash, None)
    if not isinstance(record, dict):
        return None
    cache[known_slots_hash] = record
    return copy.deepcopy(record)


def _store_slot_inference_cache_record(
    state: GiftRecommendationState,
    known_slots_hash: str,
    record: Dict[str, Any],
) -> None:
    cache = _get_slot_inference_cache(state)
    cache.pop(known_slots_hash, None)
    cache[known_slots_hash] = copy.deepcopy(record)
    while len(cache) > SLOT_INFERENCE_CACHE_MAX_ENTRIES:
        oldest_hash = next(iter(cache))
        cache.pop(oldest_hash, None)


def _apply_inference_candidates(
    state: GiftRecommendationState,
    inferred_slots: Any,
) -> list[Dict[str, Any]]:
    final_results = []

    for item in inferred_slots:
        if not isinstance(item, dict):
            continue
        slot_name = item.get("slot_name")
        if slot_name not in state.filled_slots:
            continue

        value = _normalize_slot_value(slot_name, item.get("value"))
        reasoning = item.get("reasoning")
        try:
            confidence = float(item.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        if isinstance(reasoning, str):
            reasoning = reasoning.strip()
        else:
            reasoning = None

        applied = False
        skip_reason = None

        existing_slot = state.filled_slots[slot_name]
        already_filled = (
            getattr(existing_slot, "is_filled", False)
            and getattr(existing_slot, "value", None) is not None
        )
        min_confidence = INFERENCE_MIN_CONFIDENCE.get(slot_name, 1.0)

        if already_filled:
            skip_reason = "slot_already_filled"
        elif slot_name not in INFERENCE_ALLOWED_SLOTS:
            skip_reason = "slot_not_allowed_for_inference"
        elif confidence < min_confidence:
            skip_reason = "low_confidence"
        elif value is None:
            skip_reason = "invalid_value"
        elif not reasoning:
            skip_reason = "missing_reasoning"
        else:
            existing_slot.value = value
            existing_slot.is_filled = True
            applied = True

        final_results.append(
            {
                "slot_name": slot_name,
                "value": value,
                "reasoning": reasoning,
                "confidence": confidence,
                "applied": applied,
                "skip_reason": skip_reason,
            }
        )

    return final_results


def _ensure_slots(state: GiftRecommendationState) -> None:
    slot_map = {
        "recipient_relation": ("Recipient Relation", "high"),
        "occasion": ("Occasion", "high"),
        "budget_min": ("Budget Min", "high"),
        "budget_max": ("Budget Max", "high"),
        "recipient_preferences": ("Recipient Preferences", "high"),
        "recipient_age": ("Recipient Age", "low"),
        "recipient_gender": ("Recipient Gender", "low"),
        "user_gender": ("User Gender", "low"),
        "recipient_location": ("Recipient Location", "low"),
        "user_location": ("User Location", "low"),
        "current_season": ("Current Season", "low"),
        "delivery_time": ("Delivery Time", "low"),
        "taboo": ("Taboo", "low"),
    }

    for slot_name, (display_name, priority) in slot_map.items():
        if slot_name not in state.filled_slots:
            state.filled_slots[slot_name] = GiftSlot(
                slot_name=slot_name,
                display_name=display_name,
                priority=priority,
            )


def _build_chat_history_text(state: GiftRecommendationState, user_text: str) -> str:
    current_text = (user_text or "").strip()
    history = [
        {"role": "user", "content": str(msg.get("content", "") or "").strip()}
        for msg in (state.chat_history or [])
        if msg.get("role") == "user" and str(msg.get("content", "") or "").strip()
    ]
    if current_text and (not history or history[-1].get("content") != current_text):
        history.append({"role": "user", "content": current_text})
    return "\n".join([f"{msg['role']}: {msg['content']}" for msg in history])


def _normalize_slot_value(slot_name: str, value: Any) -> Any:
    if value is None:
        return None

    if slot_name in {"budget_min", "budget_max"}:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    if slot_name in {
        "recipient_gender",
        "user_gender",
        "recipient_relation",
        "occasion",
        "recipient_age",
        "recipient_location",
        "user_location",
        "current_season",
        "delivery_time",
        "taboo",
    }:
        if isinstance(value, str):
            value = value.strip()
            return value or None

    if slot_name == "recipient_preferences":
        if isinstance(value, str):
            value = value.strip()
            return value or None
        if isinstance(value, list):
            value = [str(v).strip() for v in value if str(v).strip()]
            return value or None

    return value


def _set_slot_value(
    state: GiftRecommendationState,
    slot_name: str,
    value: Any,
    source: str,
) -> Dict[str, Any]:
    value = _normalize_slot_value(slot_name, value)
    if value is None:
        return {"old_value": None, "new_value": None, "source": source, "applied": False}
    slot = state.filled_slots[slot_name]
    old_value = slot.value
    slot.value = value
    slot.is_filled = True
    return {
        "old_value": old_value,
        "new_value": value,
        "source": source,
        "applied": True,
    }


def _current_season(now: Optional[datetime] = None) -> str:
    month = (now or datetime.now()).month
    if 3 <= month <= 5:
        return "\u6625\u5b63"
    if 6 <= month <= 8:
        return "\u590f\u5b63"
    if 9 <= month <= 11:
        return "\u79cb\u5b63"
    return "\u51ac\u5b63"


def _extract_relative_budget_update(
    text: str,
    state: Optional[GiftRecommendationState],
) -> Dict[str, Any]:
    normalized = re.sub(r"\s+", "", text or "")
    if not normalized:
        return {"mentioned": False}

    if any(pattern.search(normalized) for pattern in RELATIVE_BUDGET_INCREASE_PATTERNS):
        anchor, anchor_source = _get_relative_budget_anchor(state)
        budget_min = _round_budget_value(anchor)
        budget_max = _round_budget_value(anchor * RELATIVE_BUDGET_INCREASE_RATIO)
        if budget_max <= budget_min:
            budget_max = _round_budget_value(budget_min + 100.0)
        return {
            "mentioned": True,
            "mode": "relative_increase",
            "budget_min": budget_min,
            "budget_max": budget_max,
            "source": "relative_budget_rule",
            "anchor": anchor,
            "anchor_source": anchor_source,
        }

    if any(pattern.search(normalized) for pattern in RELATIVE_BUDGET_DECREASE_PATTERNS):
        anchor, anchor_source = _get_relative_budget_anchor(state)
        budget_min = _round_budget_value(anchor * RELATIVE_BUDGET_DECREASE_RATIO)
        budget_max = _round_budget_value(anchor)
        return {
            "mentioned": True,
            "mode": "relative_decrease",
            "budget_min": budget_min,
            "budget_max": budget_max,
            "source": "relative_budget_rule",
            "anchor": anchor,
            "anchor_source": anchor_source,
        }

    return {"mentioned": False}


def _get_relative_budget_anchor(
    state: Optional[GiftRecommendationState],
) -> tuple[float, str]:
    budget_min = _get_slot_budget_float(state, "budget_min")
    budget_max = _get_slot_budget_float(state, "budget_max")
    if budget_min is not None and budget_max is not None:
        return (budget_min + budget_max) / 2.0, "existing_budget_center"
    if budget_min is not None:
        return budget_min, "existing_budget_min"
    if budget_max is not None:
        return budget_max, "existing_budget_max"

    max_recommended_price = _get_last_recommended_max_price(state)
    if max_recommended_price is not None:
        return max_recommended_price, "last_recommended_max_price"

    return DEFAULT_RELATIVE_BUDGET_ANCHOR, "default_500"


def _get_slot_budget_float(
    state: Optional[GiftRecommendationState],
    slot_name: str,
) -> Optional[float]:
    if state is None:
        return None
    slot = getattr(state, "filled_slots", {}).get(slot_name)
    if slot is None or not getattr(slot, "is_filled", False):
        return None
    return _coerce_budget_float(getattr(slot, "value", None))


def _get_last_recommended_max_price(
    state: Optional[GiftRecommendationState],
) -> Optional[float]:
    if state is None:
        return None

    prices = []
    snapshot = getattr(state, "last_recommended_products_snapshot", []) or []
    if isinstance(snapshot, list):
        for item in snapshot:
            if not isinstance(item, dict):
                continue
            price = _coerce_budget_float(item.get("price"))
            if price is not None and price > 0:
                prices.append(price)

    if not prices:
        for product in getattr(state, "filtered_products", []) or []:
            price = _coerce_budget_float(getattr(product, "price", None))
            if price is not None and price > 0:
                prices.append(price)

    if not prices:
        for card in getattr(state, "final_product_cards", []) or []:
            if not isinstance(card, dict):
                continue
            price = _coerce_budget_float(card.get("payPrice"))
            if price is not None and price > 0:
                prices.append(price)

    return max(prices) if prices else None


def _coerce_budget_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _round_budget_value(value: float) -> float:
    step = 100.0 if value >= 1000 else 50.0
    return float(round(value / step) * step)


def _extract_budget_range_by_rule(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {"mentioned": False}

    exact_match = re.search(
        r"(?:正好|刚好|就要)\s*(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?",
        text,
    )
    if exact_match:
        value = float(exact_match.group(1))
        return _budget_update("exact", value, value)

    range_match = re.search(
        r"(?:预算\s*)?(\d+(?:\.\d+)?)\s*(?:-|~|到|至)\s*(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?",
        text,
    )
    if range_match:
        left = float(range_match.group(1))
        right = float(range_match.group(2))
        return _budget_update("range", min(left, right), max(left, right))

    upper_match = re.search(
        r"(?:预算\s*)?(?:不超过|别超过|不要超过|控制在|最多|最高)?\s*(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?\s*(?:以内|以下|内)",
        text,
    )
    if upper_match:
        return _budget_update("upper_bound", 0.0, float(upper_match.group(1)))

    upper_prefix_match = re.search(
        r"(?:不超过|别超过|不要超过|控制在|最多|最高|低于|小于|不大于|不高于|不到|不足|封顶)\s*(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?",
        text,
    )
    if upper_prefix_match:
        return _budget_update("upper_bound", 0.0, float(upper_prefix_match.group(1)))

    lower_match = re.search(
        r"(?:至少|不低于|起码|高于|大于|不少于|不小于|超过|最低)\s*(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?|(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?\s*(?:以上|起)",
        text,
    )
    if lower_match:
        value = float(lower_match.group(1) or lower_match.group(2))
        return _budget_update("lower_bound", value, None)

    per_person_match = re.search(
        r"(?:人均|每人|每个人|每位|一人)\s*(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?",
        text,
    )
    if per_person_match:
        value = float(per_person_match.group(1))
        return _budget_update("around", float(round(value * 0.8)), float(round(value * 1.2)))

    around_match = re.search(
        r"(?:大概|大约|约|差不多)?\s*(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?\s*(?:左右|上下|附近)|(?:大概|大约|约|差不多)\s*(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?",
        text,
    )
    if around_match:
        value = float(around_match.group(1) or around_match.group(2))
        return _budget_update("around", float(round(value * 0.8)), float(round(value * 1.2)))

    generic_match = re.search(
        r"(?:预算|价位)\s*(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?(?!\s*(?:-|~|到|至|以内|以下|内|以上|起|左右|上下|附近))",
        text,
    )
    if generic_match:
        value = float(generic_match.group(1))
        return _budget_update("around", float(round(value * 0.8)), float(round(value * 1.2)))

    return {"mentioned": False}


def _budget_update(
    mode: str,
    budget_min: Optional[float],
    budget_max: Optional[float],
) -> Dict[str, Any]:
    return {
        "mentioned": True,
        "mode": mode,
        "budget_min": budget_min,
        "budget_max": budget_max,
        "source": "rule_budget",
    }


def _extract_budget_by_llm(text: str) -> Dict[str, Any]:
    """当规则提取失败时，用 LLM 兜底提取预算。"""
    try:
        result = call_json(
            prompt=f"用户输入：{text}",
            system_prompt=BUDGET_EXTRACTION_PROMPT,
            temperature=0.0,
        )
    except Exception:
        return {"mentioned": False}

    if not isinstance(result, dict):
        return {"mentioned": False}
    if not result.get("mentioned"):
        return {"mentioned": False}

    budget_min = result.get("budget_min")
    budget_max = result.get("budget_max")
    if budget_min is not None:
        try:
            budget_min = float(budget_min)
        except (TypeError, ValueError):
            budget_min = None
    if budget_max is not None:
        try:
            budget_max = float(budget_max)
        except (TypeError, ValueError):
            budget_max = None

    if budget_min is None and budget_max is None:
        return {"mentioned": False}

    return {
        "mentioned": True,
        "mode": "llm_fallback",
        "budget_min": budget_min,
        "budget_max": budget_max,
        "source": "llm_budget",
    }


def _extract_budget(
    text: str,
    *,
    state: Optional[GiftRecommendationState] = None,
) -> Dict[str, Any]:
    """规则优先，规则未命中时 LLM 兜底。"""
    result = _extract_budget_range_by_rule(text)
    if result.get("mentioned"):
        return result
    relative_result = _extract_relative_budget_update(text, state)
    if relative_result.get("mentioned"):
        return relative_result
    return _extract_budget_by_llm(text)


def _budget_center_from_values(
    budget_min: Optional[float],
    budget_max: Optional[float],
) -> Optional[float]:
    if budget_min is not None and budget_max is not None:
        return (float(budget_min) + float(budget_max)) / 2.0
    if budget_min is not None:
        return float(budget_min)
    if budget_max is not None:
        return float(budget_max)
    return None


def _should_reject_budget_update(
    old_budget_min: Optional[float],
    old_budget_max: Optional[float],
    budget_update: Dict[str, Any],
) -> tuple[bool, str]:
    mode = str(budget_update.get("mode", "") or "")
    source = str(budget_update.get("source", "") or "")
    new_budget_min = _coerce_budget_float(budget_update.get("budget_min"))
    new_budget_max = _coerce_budget_float(budget_update.get("budget_max"))
    old_center = _budget_center_from_values(old_budget_min, old_budget_max)
    new_center = _budget_center_from_values(new_budget_min, new_budget_max)
    anchor = _coerce_budget_float(budget_update.get("anchor"))

    if new_center is None:
        return False, ""

    if mode == "relative_increase":
        baseline = anchor if anchor is not None else old_center
        if baseline is not None and new_center < baseline:
            return True, "relative_increase_below_anchor"
        return False, ""

    if mode == "relative_decrease":
        baseline = anchor if anchor is not None else old_center
        if baseline is not None and new_center > baseline:
            return True, "relative_decrease_above_anchor"
        return False, ""

    if mode == "llm_fallback" and source == "llm_budget" and old_center is not None:
        if new_center < old_center:
            return True, "llm_fallback_budget_decrease_without_explicit_number"

    return False, ""


def _apply_budget_update(
    state: GiftRecommendationState,
    budget_update: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    if not budget_update.get("mentioned"):
        return {}

    old_budget_min = _get_slot_budget_float(state, "budget_min")
    old_budget_max = _get_slot_budget_float(state, "budget_max")
    should_reject, reject_reason = _should_reject_budget_update(
        old_budget_min,
        old_budget_max,
        budget_update,
    )
    if should_reject:
        applied: Dict[str, Dict[str, Any]] = {}
        for slot_name in ("budget_min", "budget_max"):
            slot = state.filled_slots[slot_name]
            applied[slot_name] = {
                "old_value": slot.value,
                "new_value": slot.value,
                "source": budget_update.get("source", "rule_budget"),
                "mode": budget_update.get("mode", ""),
                "changed": False,
                "rejected": True,
                "reject_reason": reject_reason,
            }
        return applied

    applied: Dict[str, Dict[str, Any]] = {}
    for slot_name, key in (("budget_min", "budget_min"), ("budget_max", "budget_max")):
        slot = state.filled_slots[slot_name]
        old_value = slot.value
        old_is_filled = slot.is_filled
        new_value = budget_update.get(key)
        slot.value = float(new_value) if new_value is not None else None
        slot.is_filled = new_value is not None
        applied[slot_name] = {
            "old_value": old_value,
            "new_value": slot.value,
            "source": budget_update.get("source", "rule_budget"),
            "mode": budget_update.get("mode", ""),
            "changed": old_value != slot.value or old_is_filled != slot.is_filled,
        }
    return applied


def _save_inference_results(state: GiftRecommendationState, infer_result: dict[str, Any]) -> None:
    """
    尝试将推理结果保存到 state 上，便于后续日志、调试或前端展示。
    如果 state 没有对应字段，则静默跳过。
    """
    if not isinstance(infer_result, dict):
        return
    if infer_result.get("preserve_existing_results"):
        return

    inferred_slots = infer_result.get("inferred_slots", [])
    try:
        setattr(state, "inference_results", inferred_slots)
    except Exception:
        pass
