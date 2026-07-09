from __future__ import annotations

import json
from typing import Dict, List, Optional, Set

from .llm_client import call_json
from .models import GiftRecommendationState, RouterDecision


ROUTER_CONFIDENCE_THRESHOLD = 0.65

ALLOWED_ACTIONS_BY_STAGE: Dict[str, Set[str]] = {
    "init": {
        "ask_need",
        "run_category_flow",
        "ask_detail",
        "filter_products",
        "restart_flow",
        "exit_flow",
    },
    "await_need": {
        "ask_need",
        "run_category_flow",
        "ask_detail",
        "filter_products",
        "restart_flow",
        "exit_flow",
    },
    "need_more_info": {
        "ask_need",
        "run_category_flow",
        "ask_detail",
        "filter_products",
        "restart_flow",
        "exit_flow",
    },
    "choose_category": {
        "choose_pending_category",
        "re_prompt_choose_category",
        "run_category_flow",
        "ask_detail",
        "filter_products",
        "restart_flow",
        "exit_flow",
    },
    "detail_answer": {
        "filter_products",
        "run_category_flow",
        "restart_flow",
        "exit_flow",
    },
    "done": {
        "restart_flow",
        "run_category_flow",
        "ask_detail",
        "filter_products",
        "exit_flow",
    },
}

ROUTER_SYSTEM_PROMPT = """
你是送礼推荐流程的状态路由器。

你的任务是：根据当前状态、历史上下文、已知槽位、候选品类、候选商品池和用户最新输入，判断这一轮对话下一步应该执行什么动作。

你只能从以下 next_action 中选择一个：
- ask_need: 继续询问送礼需求。
- run_category_flow: 把当前输入当作需求或补充条件，重新做品类判断。
- choose_pending_category: 用户已经从候选品类中选中了一个。
- re_prompt_choose_category: 用户像是在选择候选品类，但表达不清，需要重新提示。
- ask_detail: 商品方向已经较明确，继续追问能帮助筛商品的细节。
- filter_products: 当前信息已经足够，可以开始筛商品并推荐。
- restart_flow: 用户想重新开始一轮送礼流程。
- exit_flow: 用户想结束当前送礼流程。

决策规则：
1. 如果用户明显在补充预算、偏好、对象、场景、品牌、功效、禁忌等信息，优先考虑 run_category_flow、ask_detail 或 filter_products，不要误判为 re_prompt_choose_category。
2. 如果当前在 choose_category，且用户明确提到某个候选品类名，或明确表达某个序号选择，选择 choose_pending_category。
3. 如果当前在 choose_category，且用户看起来在选编号但表达不完整，例如“第二”“选那个”，可以选择 re_prompt_choose_category。
4. 如果当前在 detail_answer，且用户输入了任何有效补充信息，优先选择 filter_products，先给出一批商品；只有用户明确说“换方向/换品类/重新判断”时才选择 run_category_flow。
5. 如果候选商品池已经较集中，且最新输入明显在补充品牌、预算、功效、对象或禁忌，可以直接选择 ask_detail 或 filter_products，不必强行先走 choose_category。
6. 如果候选商品池仍然分散在多个品类，优先 ask_need 或 run_category_flow，先把方向收窄。
7. 如果 top_products、top_brands 或 top_small_categories 已经很集中，说明当前商品方向较明确，可以跳过部分中间状态。
8. 如果用户明显表示“重新来”“重新送礼”“换个需求重新开始”，选择 restart_flow。
9. 如果不确定，优先保守，选择更安全的 ask_need / run_category_flow / ask_detail，不要激进地选择 exit_flow。
10. selected_category 只有在 next_action=choose_pending_category 时才填写，并且必须是候选品类中的原始名称。
11. confidence 范围是 0 到 1。

严格返回 JSON，格式如下：
{
  "next_action": "run_category_flow",
  "selected_category": "",
  "confidence": 0.82,
  "reason": "一句话解释"
}
""".strip()


def route_turn_with_llm(
    state: GiftRecommendationState,
    stage: str,
    user_text: str,
    pending_categories: List[str],
    pending_reason: str,
    is_entry_request: bool,
    selected_category_hint: str = "",
    numeric_selection_hint: bool = False,
) -> Optional[RouterDecision]:
    user_text = (user_text or "").strip()
    if not user_text:
        return None

    prompt = _build_router_prompt(
        state=state,
        stage=stage,
        user_text=user_text,
        pending_categories=pending_categories,
        pending_reason=pending_reason,
        is_entry_request=is_entry_request,
        selected_category_hint=selected_category_hint,
        numeric_selection_hint=numeric_selection_hint,
    )

    try:
        result = call_json(
            prompt=prompt,
            system_prompt=ROUTER_SYSTEM_PROMPT,
            temperature=0.1,
        )
    except Exception:
        return None

    decision = _parse_router_decision(result)
    if not decision:
        return None

    if not _is_decision_allowed(stage, decision, pending_categories):
        return None

    if decision.confidence < ROUTER_CONFIDENCE_THRESHOLD:
        return None

    return decision


def _build_router_prompt(
    state: GiftRecommendationState,
    stage: str,
    user_text: str,
    pending_categories: List[str],
    pending_reason: str,
    is_entry_request: bool,
    selected_category_hint: str,
    numeric_selection_hint: bool,
) -> str:
    payload = {
        "current_stage": stage,
        "is_entry_request": is_entry_request,
        "selected_category": getattr(getattr(state, "selected_category", None), "category_name", "") or "",
        "selected_mid_category": getattr(state, "selected_mid_category", "") or "",
        "category_level": getattr(state, "category_level", "") or "",
        "pending_categories": pending_categories,
        "pending_reason": pending_reason,
        "selected_category_hint": selected_category_hint,
        "numeric_selection_hint": numeric_selection_hint,
        "filled_slots": _build_filled_slots_payload(state),
        "candidate_pool_summary": _build_candidate_pool_payload(state),
        "recent_history": _build_recent_history_payload(state.chat_history),
        "user_input": user_text,
        "allowed_actions": sorted(ALLOWED_ACTIONS_BY_STAGE.get(stage, set())),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _build_filled_slots_payload(state: GiftRecommendationState) -> Dict[str, str]:
    payload: Dict[str, str] = {}
    for slot_name, slot in getattr(state, "filled_slots", {}).items():
        value = getattr(slot, "value", None)
        if value is None or value == "":
            continue
        payload[slot_name] = str(value)
    return payload


def _build_candidate_pool_payload(state: GiftRecommendationState) -> Dict[str, object]:
    summary = getattr(state, "candidate_pool_summary", None)
    if not isinstance(summary, dict):
        return {"count": 0, "confidence": 0.0}

    return {
        "count": summary.get("count", 0),
        "confidence": summary.get("confidence", 0.0),
        "reason": summary.get("reason", ""),
        "matched_signals": summary.get("matched_signals", []),
        "pending_categories": summary.get("pending_categories", []),
        "price_range": summary.get("price_range", {}),
        "top_mid_categories": summary.get("top_mid_categories", []),
        "top_small_categories": summary.get("top_small_categories", []),
        "top_brands": summary.get("top_brands", []),
        "top_products": summary.get("top_products", []),
    }


def _build_recent_history_payload(
    history: List[Dict[str, str]],
    limit: int = 6,
) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for msg in (history or [])[-limit:]:
        role = str(msg.get("role", "") or "").strip()
        content = str(msg.get("content", "") or "").strip()
        if role and content:
            items.append({"role": role, "content": content})
    return items


def _parse_router_decision(result: dict) -> Optional[RouterDecision]:
    if not isinstance(result, dict):
        return None

    next_action = str(result.get("next_action", "") or "").strip()
    if not next_action:
        return None

    try:
        confidence = float(result.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    return RouterDecision(
        next_action=next_action,  # type: ignore[arg-type]
        selected_category=str(result.get("selected_category", "") or "").strip(),
        confidence=confidence,
        reason=str(result.get("reason", "") or "").strip(),
    )


def _is_decision_allowed(
    stage: str,
    decision: RouterDecision,
    pending_categories: List[str],
) -> bool:
    allowed_actions = ALLOWED_ACTIONS_BY_STAGE.get(stage, set())
    if decision.next_action not in allowed_actions:
        return False

    if decision.next_action == "choose_pending_category":
        if not decision.selected_category:
            return False
        if decision.selected_category not in pending_categories:
            return False

    return True
