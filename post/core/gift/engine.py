from __future__ import annotations

import asyncio
import copy
import json
import re
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .category_selection import (
    MID_CATEGORY_TO_BIG_CATEGORY_MAP,
    SMALL_TO_MID_CATEGORY_MAP,
    apply_mid_category_selection,
    apply_subcategory_selection,
    category_selection,
    subcategory_keyword_map,
)
from .category_catalog import (
    build_mid_category_candidate_text,
    build_small_category_candidate_text,
    get_complete_mid_to_big_category_map as _catalog_complete_mid_to_big_category_map,
    get_complete_small_to_mid_category_map as _catalog_complete_small_to_mid_category_map,
)
from .detailed_dimensions import (
    apply_detailed_dimensions_plan,
    detailed_dimensions,
    generate_detailed_dimensions_message,
    prepare_detailed_dimensions,
)
from .feature_extraction import (
    apply_current_turn_slot_updates,
    apply_system_context_slots,
    extract_current_turn_slot_updates,
    feature_extraction,
)
from .llm_client import call_json, call_text
from .models import CategorySelectionResult, GiftRecommendationState
from .product_entry_search import (
    DirectProductDetectionResult,
    apply_direct_product_detection,
    detect_direct_product_query,
    try_direct_product_search,
)
from .product_filtering import (
    _apply_explicit_hard_constraints,
    _load_products_from_csv,
    format_recommendation_cards,
    format_recommendations,
    product_filtering,
    product_search_text,
    record_product_filter_local_fill,
)
from .task_boundary import GiftTaskBoundaryDecision, detect_gift_task_boundary
from ..llm_trace import submit_with_llm_trace

SAFE_QUESTION_POOL = [
    "比{name}更实惠的有没有？",
    "预算再高点，有没有更好的？",
    "这个{name}不错，还有什么类似的吗？",
]

_EXIT_KEYWORDS = {"退出", "不需要了", "不用了", "结束", "再见"}
_NON_GIFT_SERVICE_KEYWORDS = (
    "物流",
    "快递",
    "配送",
    "发货",
    "收货",
    "运费",
    "订单",
    "查订单",
    "包裹",
    "售后",
    "退货",
    "退款",
    "发票",
    "支付",
    "会员",
    "客服",
    "天气",
)
_ENTRY_KEYWORDS = {"送礼", "礼物", "开始", "开始送礼", "我要送礼"}
_AMBIVALENT_KEYWORDS = {"随便", "都可以", "都行", "无所谓", "你定", "你决定", "看着办", "随意", "哪个都行", "都差不多", "你推荐", "你觉得呢", "你看着来", "选一个", "都可以的"}
_REJECT_PENDING_CATEGORY_KEYWORDS = (
    "都不合适",
    "不太合适",
    "不合适",
    "都不要",
    "不喜欢",
    "不是这些",
    "不要这些",
    "换一批",
    "换几个",
    "换一下",
    "其他选择",
    "别的选择",
    "还有别的",
    "还有其他",
    "有没有别的",
    "有没有其他",
)
_BRIEF_ONLY_KEYWORDS = ("少问", "少问点", "别问太多", "不用问太多", "问一个", "一个问题")
_DIRECT_RECOMMEND_KEYWORDS = ("直接推荐", "直接给", "直接来", "你看着办", "随便推荐", "不用问", "先推荐", "先给")
_HIGH_DETAIL_KEYWORDS = ("品牌", "预算", "场景", "禁忌", "过敏", "偏好", "风格", "功效", "用途", "使用", "材质", "颜色", "口味", "香型", "肤质", "敏感肌", "修护", "抗老", "区别", "哪个好", "怎么选", "比较")

_STATE_KEY = "gift_state"
_STAGE_KEY = "gift_stage"
_PENDING_CATEGORIES_KEY = "gift_pending_categories"
_PENDING_REASON_KEY = "gift_pending_selection_reason"
_REJECTED_CATEGORIES_KEY = "gift_rejected_categories"
_REJECTED_SUBCATEGORIES_KEY = "gift_rejected_subcategories"
_MAX_TRACKED_PRODUCT_IDS = 80
_EXIT_LLM_CONFIDENCE_THRESHOLD = 0.8
_GIFT_BOUNDARY_LLM_CONFIDENCE_THRESHOLD = 0.78
_CATEGORY_PREFERENCE_LLM_CONFIDENCE_THRESHOLD = 0.72
_EXPLICIT_CATEGORY_LLM_CONFIDENCE_THRESHOLD = 0.75
_CATEGORY_GROUP_ALIASES = {
    "酒": ["葡萄酒", "洋酒", "国酒"],
    "酒类": ["葡萄酒", "洋酒", "国酒"],
}
_PENDING_CATEGORY_ALIAS_MAP = {
    "黄金珠宝": ["首饰", "饰品", "珠宝", "珠宝首饰", "项链", "戒指", "耳饰", "耳环", "手链", "手镯", "吊坠", "黄金"],
    "腕表": ["手表", "表", "腕表"],
    "香水香氛": ["香水", "香氛", "香薰", "淡香水", "浓香水"],
    "女包（含中性）": ["包", "包包", "女包", "手提包", "单肩包", "斜挎包", "双肩包"],
    "面部彩妆": ["彩妆", "口红", "唇膏", "唇釉", "粉底", "眼影", "腮红"],
    "面部护肤": ["护肤", "护肤品", "面膜", "精华", "面霜", "洗面奶", "洁面"],
    "男士护肤": ["男士护肤", "男士洁面", "男士面霜", "剃须"],
    "美妆工具": ["美妆工具", "化妆刷", "美容仪"],
    "时尚配饰": ["配饰", "饰品", "腰带", "胸针", "挂饰"],
    "服配": ["围巾", "披肩", "丝巾", "帽子", "手套"],
    "眼镜": ["眼镜", "墨镜", "太阳镜"],
    "咖啡冲饮": ["咖啡", "咖啡豆", "咖啡粉", "冲饮"],
    "茗茶": ["茶", "茶叶", "绿茶", "红茶", "白茶", "乌龙茶"],
    "葡萄酒": ["红酒", "葡萄酒"],
    "洋酒": ["洋酒", "威士忌", "白兰地"],
    "国酒": ["白酒", "国酒"],
}
_NEGATIVE_CATEGORY_SIGNALS = (
    "不要",
    "不想要",
    "不考虑",
    "不推荐",
    "别推荐",
    "别送",
    "不送",
    "排除",
    "避开",
)
_POSITIVE_CATEGORY_SIGNALS = (
    "想要",
    "想看",
    "看看",
    "看下",
    "推荐",
    "要",
    "送",
    "买",
    "换成",
    "改成",
    "还是",
    "就",
    "可以",
    "也行",
)

_DEFAULT_NEED_PROMPT = (
    "欢迎进入智能选品模块，请问您的送礼需求是什么，"
    "可以告诉我您的送礼对象关系、性别、年龄以及送礼预算、偏好等信息。"
)


@dataclass
class FlowBoundaryResults:
    has_gift_continuation_signal: bool = False
    should_route_out: bool = False
    should_exit: bool = False


@dataclass
class TurnUnderstandingResults:
    boundary_decision: GiftTaskBoundaryDecision = field(
        default_factory=GiftTaskBoundaryDecision
    )
    slot_extraction_result: Dict[str, Any] = field(default_factory=dict)
    negative_mid_categories: List[str] = field(default_factory=list)
    negative_subcategories: List[str] = field(default_factory=list)
    positive_mid_categories: List[str] = field(default_factory=list)
    positive_subcategories: List[str] = field(default_factory=list)


@dataclass
class ProductCategoryResolutionResults:
    direct_product_detection: Optional[DirectProductDetectionResult] = None


@dataclass
class ChooseCategoryResolution:
    resolution_type: str = "no_match"
    target_mid_category: str = ""
    target_subcategory: str = ""
    confidence: float = 0.0
    source: str = ""
    reason: str = ""


_NEED_PROMPT_SYSTEM_PROMPT = """你是一个温和、自然的送礼选品助手。

请生成一句中文开场引导话术，用来开启送礼推荐流程。

要求：
1. 含义必须和固定话术一致：询问用户的送礼需求，并引导用户提供送礼对象关系、性别、年龄、预算、偏好等信息。
2. 语气自然、亲切，不要像模板。
3. 简洁，不超过 80 个中文字符。
4. 只输出话术本身，不要解释，不要编号，不要 Markdown。
"""

_EXIT_INTENT_SYSTEM_PROMPT = """你是送礼推荐流程的退出意图识别器。

你的任务是判断：用户最新输入，是否表达了“结束/停止当前送礼推荐流程，不再继续当前推荐和追问”的意图。

判断规则：
1. 只有在用户明确或强烈暗示要结束当前送礼推荐流程时，should_exit 才返回 true。
2. 像“先这样吧”“先到这吧”“先不看了”“暂时不用推荐了”“下次再说”“不用继续了”这类结束当前流程的话，可以返回 true。
3. 如果用户只是想换商品、换类目、补充条件、重新推荐、继续咨询、拒绝某个具体商品，必须返回 false。
4. 普通礼貌表达、寒暄、感谢，默认返回 false，除非同时明确表达不再继续当前流程。
5. 如果拿不准，必须返回 false。

请严格返回 JSON，格式如下：
{
  "should_exit": true,
  "confidence": 0.0,
  "reason": "一句话说明判断原因"
}
"""

_GIFT_BOUNDARY_SYSTEM_PROMPT = """你是送礼推荐系统的业务边界识别器。

系统职责：只承接“送礼选品/送礼推荐”相关输入。你的任务是判断用户最新输入是否已经超出送礼业务范围，需要转交给综合助手或其他业务系统。

判断为 route_out=true 的情况：
1. 用户询问物流、快递、配送、发货、收货、包裹、运费、订单查询、订单状态等履约/订单业务。
2. 用户询问售后、退货、退款、发票、支付、会员、账号、客服、天气、门店等非送礼选品业务。
3. 用户最新输入的核心诉求已经不是挑礼物、换品类、看商品、比较商品或补充送礼条件。

判断为 route_out=false 的情况：
1. 用户仍在表达送礼需求：送谁、预算、节日、收礼人偏好、年龄、性别、禁忌等。
2. 用户在送礼范围内换品类、换商品、继续推荐、说某个品类不错、要求再看看。
3. 用户询问商品是否适合作为礼物、价格、品牌、规格、功效、包装、适合人群或商品对比。

注意：
- 不要把“换一下”“还有别的吗”“腕表不错”“推荐贵一点”这类送礼选品内的表达判为转出。
- 如果用户问“这款商品什么时候发货/物流多久到/订单怎么查”，应判为转出，因为本系统不处理履约和订单业务。
- 如果拿不准，返回 route_out=false，继续留在送礼推荐流程。

请严格返回 JSON：
{
  "route_out": true,
  "target_domain": "logistics",
  "confidence": 0.0,
  "reason": "一句话说明判断依据"
}
"""

_GIFT_BOUNDARY_SYSTEM_PROMPT += """

Mixed-intent priority:
If the latest user input contains both gift-selection information and a non-gift
service question, return route_out=false. Examples include budget, recipient,
occasion, preference, taboo, style, category, or product-selection constraints
mixed with logistics, delivery, shipping, order, or after-sales questions.
In these mixed cases, the gift recommendation flow should process the gift
information and ignore the service question for this turn.
Only return route_out=true when the latest input has no gift-selection update
and the core request is purely logistics, order, after-sales, account, payment,
weather, store, or other non-gift business.
"""

_FLOW_BOUNDARY_SYSTEM_PROMPT = """你是送礼推荐流程的统一边界识别器。

请根据用户最新输入、当前流程阶段和最近对话，在以下三个互斥动作中选择一个：

1. route_out：用户的核心诉求已经超出送礼选品范围，应转交其他业务。
   - 包括纯物流、快递、配送、发货、订单、售后、退款、发票、支付、会员、账号、天气、门店等请求。
2. exit_flow：用户希望结束或暂停当前送礼推荐，不再继续本轮推荐和追问。
   - 包括“先这样吧”“先到这吧”“先不看了”“下次再说”“不用继续了”等明确结束表达。
3. continue_gift：用户仍在送礼选品流程内，或者意图不够明确。
   - 包括补充收礼人、预算、场景、偏好、禁忌，换商品、换品类、比较商品、继续推荐。

判定规则：
1. 三个动作必须互斥，只能返回一个 action。
2. 用户只是拒绝某个商品、要求换商品或换品类，不是退出流程。
3. 普通感谢、寒暄不等于退出；不确定时返回 continue_gift。
4. 同一句话同时包含送礼条件更新和物流、订单、售后等问题时，送礼信息优先，返回 continue_gift。
5. has_gift_continuation_signal=true 时必须返回 continue_gift。
6. allow_exit=false 时不能返回 exit_flow；没有活跃送礼上下文时，普通结束表达不应退出不存在的流程。

严格返回 JSON，不要输出额外内容：
{
  "action": "continue_gift",
  "target_domain": "",
  "confidence": 0.0,
  "reason": "一句话说明判断依据"
}
""".strip()

_NEGATIVE_CATEGORY_SYSTEM_PROMPT = """你是送礼推荐系统中的“负向品类约束识别器”。

任务：从用户最新输入中识别用户明确表示不要、排除、避开的商品小类或中类。

规则：
1. 只有用户明确表达“不要/不想要/不考虑/不推荐/别推荐/别送/不送/排除/避开”等负向意图时才返回。
2. 负向对象可以是小类，如口红、香水、面膜；也可以是中类，如面部彩妆、香水香氛。
3. 如果用户只是正向表达想看某类，不能放入 rejected。
4. 返回值必须来自候选小类或候选中类，不能编造。
5. 如果没有明确负向品类，返回空数组。

请严格返回 JSON：
{
  "rejected_subcategories": ["口红"],
  "rejected_mid_categories": [],
  "confidence": 0.0,
  "reason": "一句话说明判断依据"
}
"""

_POSITIVE_CATEGORY_SYSTEM_PROMPT = """你是送礼推荐系统中的“正向品类改口识别器”。

任务：从用户最新输入中识别用户明确想要重新考虑、推荐、查看的商品小类或中类。这类正向表达用于清除之前的拒绝约束。

规则：
1. 只有用户明确表达“想要/想看/推荐/还是/就/换成/改成/看看/可以/也行”等正向意图时才返回。
2. 如果同一句中某个品类被“不要/不想要/排除/避开”等否定，不能把该品类作为正向目标。
3. 返回值必须来自候选小类或候选中类，不能编造。
4. 如果没有明确正向品类，返回空数组。

请严格返回 JSON：
{
  "positive_subcategories": ["口红"],
  "positive_mid_categories": [],
  "confidence": 0.0,
  "reason": "一句话说明判断依据"
}
"""

_EXPLICIT_CATEGORY_REFERENCE_SYSTEM_PROMPT = """You are an explicit product-category resolver for a Chinese gift recommendation system.

Task: decide whether the user's latest message explicitly points to a product category or subcategory.

Rules:
1. Return matched=true only when the user is clearly choosing, asking for, or switching to a product category.
2. Do not infer a category from a vague gift need, recipient, occasion, style, budget, color, brand, or logistics/service question.
3. If pending_candidate_mid_categories is provided, only return one of those mid categories, or a subcategory whose parent mid category is in that list.
4. If pending_candidate_mid_categories is empty, you may return any known mid category or known subcategory from the candidate lists.
5. Never return a category/subcategory that appears in rejected_mid_categories or rejected_subcategories.
6. Never return a category/subcategory mentioned only as a negated object, such as "不要口红" or "不看香水".
7. Do not invent category names. Output names must exactly match the provided candidates.

Return strict JSON only:
{
  "matched": true,
  "target_mid_category": "女包（含中性）",
  "target_subcategory": "",
  "confidence": 0.0,
  "reason": "one short reason"
}
"""

_CHOOSE_CATEGORY_REFERENCE_SYSTEM_PROMPT = """You are a category-choice resolver for a Chinese gift recommendation system.

The system has already recommended pending_candidate_mid_categories to the user.
Your task is to decide whether the user's latest message:
1. selects one of the pending candidates,
2. explicitly switches to another known category outside pending candidates,
3. rejects the pending candidates without naming a new category,
4. or does not contain a category choice.

Rules:
1. If the user uses a colloquial, broad, abbreviated, or non-standard category name, normalize it to the closest standard mid category or subcategory from the provided candidate lists.
2. Prefer pending candidates only when the user's expression can reasonably refer to one of them.
3. Use resolution_type="pending_select" only when target_mid_category is one of pending_candidate_mid_categories, or target_subcategory belongs to one of them.
4. Use resolution_type="global_switch" only when the user clearly asks for a known category outside pending_candidate_mid_categories.
5. Use resolution_type="reject_pending" when the user rejects the pending candidates but does not name a clear new category.
6. Use resolution_type="no_match" for budget-only, style-only, brand-only, recipient, occasion, vague gift needs, or unclear messages.
7. Never select a category/subcategory that appears in rejected_mid_categories or rejected_subcategories.
8. Never select a category/subcategory mentioned only as a negated object, such as "不要口红" or "不看香水".
9. Do not invent category names. Output names must exactly match known_mid_categories or known_subcategories.

Examples:
- If pending candidates include "黄金珠宝", then "首饰", "饰品", "珠宝首饰" can map to "黄金珠宝".
- If pending candidates include "腕表", then "手表", "表" can map to "腕表".
- If pending candidates include "香水香氛", then "香水", "香氛" can map to "香水香氛" or subcategory "香水".
- If the user says "这些都不要了，送口红", and "口红" is outside pending candidates, use resolution_type="global_switch".

Return strict JSON only:
{
  "matched": true,
  "resolution_type": "pending_select",
  "target_mid_category": "黄金珠宝",
  "target_subcategory": "",
  "confidence": 0.0,
  "reason": "one short reason"
}
"""

_CATEGORY_SWITCH_INTENT_SYSTEM_PROMPT = """你是送礼推荐流程中的“品类切换意图识别器”。

你的任务是判断：用户最新输入是否表示想从当前已选品类切换到另一个商品品类，而不是只是在当前品类内补充偏好、预算、风格、品牌或禁忌。

判断规则：
1. 如果用户明确说“换成/改成/看看/推荐/想要”另一个商品品类，应返回 should_switch_category=true。
2. 如果用户只是补充预算、颜色、风格、品牌、规格、功效、香型、肤质、禁忌等筛选条件，应返回 false。
3. 如果用户说“还有别的吗/换一批/不合适”，但没有明确新类别，也可以返回 true，但 target_category 为空。
4. 如果用户提到的品类和当前品类相同，应返回 false。
5. 如果拿不准，返回 false。
6. 只允许 target_category 使用候选品类中的名称；无法确定则为空字符串。

严格返回 JSON：
{
  "should_switch_category": true,
  "target_category": "时尚配饰",
  "confidence": 0.0,
  "reason": "一句话说明判断依据"
}
"""


async def run_gift_turn(
    conversation_id: str,
    user_query: str,
    session: Dict[str, Any],
    query_extends: Optional[dict] = None,
) -> Dict[str, Any]:
    """Run the migrated present business flow without mutating the host session."""
    return await asyncio.to_thread(
        _run_gift_turn_sync,
        conversation_id,
        user_query,
        session,
        query_extends,
    )


def _run_gift_turn_sync(
    conversation_id: str,
    user_query: str,
    session: Dict[str, Any],
    query_extends: Optional[dict] = None,
) -> Dict[str, Any]:
    user_text = (user_query or "").strip()
    if user_text in _EXIT_KEYWORDS:
        return _build_exit_response(session, "好的，这就为您结束送礼推荐流程。")

    biz_state, stage, pending_categories, pending_reason = _load_biz_snapshot(
        conversation_id, session
    )
    rejected_categories = _normalize_category_list(
        session.get(_REJECTED_CATEGORIES_KEY, [])
    )
    rejected_subcategories = _normalize_category_list(
        session.get(_REJECTED_SUBCATEGORIES_KEY, [])
    )
    if stage == "init":
        rejected_categories = []
        rejected_subcategories = []
        _reset_product_tracking(biz_state)
    query_extends = query_extends or {}
    query_extends_product_ids = _extract_product_ids_from_query_extends(query_extends)
    replace_pending_categories_requested = _should_replace_pending_categories(
        stage,
        pending_categories,
        biz_state,
        user_text,
    )
    if (
        query_extends_product_ids or _is_product_replacement_request(user_text)
    ) and (
        _is_product_replacement_request(user_text)
        or _query_extends_has_replacement_action(query_extends)
    ) and not replace_pending_categories_requested and not _has_explicit_new_category_request(
        biz_state,
        user_text,
    ):
        _reject_replacement_products(
            biz_state,
            query_extends_product_ids,
        )
    is_entry_request = user_text in _ENTRY_KEYWORDS
    pending_selection_category = ""
    pending_selection_match_type = ""
    pending_selection_remainder = ""
    if user_text and stage == "choose_category" and pending_categories:
        (
            pending_selection_category,
            pending_selection_match_type,
            pending_selection_remainder,
        ) = _resolve_pending_category_selection_with_remainder(
            user_text,
            pending_categories,
        )
    turn_understanding_text = (
        pending_selection_remainder
        if pending_selection_category
        else user_text
    )

    if pending_selection_category:
        flow_boundary_results = FlowBoundaryResults(has_gift_continuation_signal=True)
    else:
        flow_boundary_results = _run_flow_boundary_parallel(
            biz_state,
            stage,
            user_text,
            pending_categories=pending_categories,
        )
    if flow_boundary_results.should_route_out:
        return _build_exit_response(
            session,
            "好的，小Q先退出送礼业务了。",
        )
    if flow_boundary_results.should_exit:
        return _build_exit_response(session)

    handled_done_followup = False
    turn_understanding_results: Optional[TurnUnderstandingResults] = None

    if turn_understanding_text and not is_entry_request:
        turn_understanding_results = _run_turn_understanding_parallel(
            biz_state,
            stage,
            turn_understanding_text,
            pending_categories=pending_categories,
        )
        boundary_decision = turn_understanding_results.boundary_decision
        _set_task_boundary_decision(biz_state, boundary_decision.to_dict())
        if boundary_decision.action in {"restart_flow", "correct_current_task"}:
            preserved_category = (
                _capture_category_selection_state(biz_state)
                if boundary_decision.action == "correct_current_task"
                else {}
            )
            preserved_slots = (
                _capture_correct_current_task_slot_state(biz_state)
                if boundary_decision.action == "correct_current_task"
                else {}
            )
            biz_state, stage, pending_categories, pending_reason = _reset_biz_state(
                conversation_id, stage="init", session=session
            )
            rejected_categories = []
            rejected_subcategories = []
            if preserved_slots:
                _restore_slot_state(biz_state, preserved_slots)
            if preserved_category:
                _restore_category_selection_state(biz_state, preserved_category)
                stage = "detail_answer"
            _set_task_boundary_decision(biz_state, boundary_decision.to_dict())

    if is_entry_request:
        biz_state, stage, pending_categories, pending_reason = _reset_biz_state(
            conversation_id, stage="await_need", session=session
        )
        rejected_categories = []
        rejected_subcategories = []

    current_turn_slots_applied = False
    if user_text and not is_entry_request:
        _init_turn_understanding(biz_state, stage, user_text)

    if is_entry_request:
        pass
    elif turn_understanding_text:
        old_rejected_categories = list(rejected_categories)
        old_rejected_subcategories = list(rejected_subcategories)
        if turn_understanding_results is not None:
            rejected_categories, rejected_subcategories = _apply_category_preference_reference_updates(
                biz_state,
                rejected_categories,
                rejected_subcategories,
                turn_understanding_results.negative_mid_categories,
                turn_understanding_results.negative_subcategories,
                turn_understanding_results.positive_mid_categories,
                turn_understanding_results.positive_subcategories,
            )
        else:
            rejected_categories, rejected_subcategories = _apply_category_preference_updates(
                biz_state,
                turn_understanding_text,
                rejected_categories,
                rejected_subcategories,
            )
        if (
            old_rejected_categories != rejected_categories
            or old_rejected_subcategories != rejected_subcategories
        ):
            _record_turn_intent(
                biz_state,
                "category_preference_update",
                rejected_mid_categories=rejected_categories,
                rejected_subcategories=rejected_subcategories,
            )

    if turn_understanding_text and not is_entry_request:
        if turn_understanding_results is not None:
            current_turn_slots_applied = _apply_current_turn_slot_extraction_result(
                biz_state,
                turn_understanding_results.slot_extraction_result,
            )
        else:
            current_turn_slots_applied = _apply_current_turn_slot_updates(biz_state, turn_understanding_text)
    apply_system_context_slots(
        biz_state,
        member_profile=session.get("member_profile", {}),
        query_context=session.get("query_context", {}),
        user_text=user_text,
    )
    product_category_resolution = _run_product_category_resolution_parallel(
        stage,
        user_text,
    )

    if not is_entry_request and stage == "done":
        if user_text:
            if _should_replace_products_in_current_category(biz_state, user_text):
                _reject_replacement_products(biz_state, query_extends_product_ids)
                _record_turn_intent(
                    biz_state,
                    "product_replacement",
                    source="done_replacement",
                )
                if not current_turn_slots_applied:
                    feature_extraction(biz_state, user_text)
                product_filtering(biz_state, user_text)
                contents = [_format_recommendation_text(biz_state)]
                stage = "done"
                pending_categories, pending_reason = [], ""
                handled_done_followup = True
            else:
                should_switch, target_mid_category, target_subcategory = _should_switch_category(
                    biz_state,
                    stage,
                    user_text,
                )
            if not handled_done_followup and should_switch:
                _set_task_boundary_decision(
                    biz_state,
                    {
                        "action": "category_switch",
                        "confidence": 0.86,
                        "reason": "current turn matched category-switch handling",
                        "latest_frame": {
                            "target_mid_category": target_mid_category,
                            "target_subcategory": target_subcategory,
                        },
                        "source": "engine_category_switch",
                    },
                )
                _record_turn_intent(
                    biz_state,
                    "category_switch",
                    target_mid_category=target_mid_category,
                    target_subcategory=target_subcategory,
                    source="engine_category_switch",
                )
                _clear_product_recommendation_state(biz_state)
                _append_history_once(biz_state, "user", user_text)
                if target_mid_category:
                    if target_subcategory:
                        apply_subcategory_selection(
                            biz_state,
                            target_subcategory,
                            selection_reason=f"用户明确切换到“小类：{target_subcategory}”，因此按该小类继续推荐。",
                            description=f"用户明确切换的小类：{target_subcategory}",
                        )
                    else:
                        apply_mid_category_selection(
                            biz_state,
                            target_mid_category,
                            selection_reason=f"用户明确切换到“中类：{target_mid_category}”，因此按该中类继续推荐。",
                            description=f"用户明确切换的中类：{target_mid_category}",
                        )
                    rejected_categories = _remove_categories(
                        rejected_categories,
                        [target_mid_category],
                    )
                    pending_categories, pending_reason = [], ""
                    stage, contents = _ask_for_details(biz_state)
                else:
                    current_mid_category = _get_current_mid_category(biz_state)
                    if current_mid_category:
                        rejected_categories = _merge_category_lists(
                            rejected_categories,
                            [current_mid_category],
                        )
                    stage, contents, selection_result = _run_category_flow(
                        biz_state,
                        user_text,
                        excluded_mid_categories=rejected_categories,
                        excluded_subcategories=rejected_subcategories,
                        slots_already_extracted=current_turn_slots_applied,
                    )
                    pending_categories, pending_reason = _pending_from_selection(selection_result)
            elif not handled_done_followup:
                direct_result = try_direct_product_search(biz_state, user_text)
                if direct_result is not None and direct_result[0] == "done":
                    _record_turn_intent(
                        biz_state,
                        "direct_product_search",
                        source="done_followup",
                    )
                    stage, contents = direct_result
                    pending_categories, pending_reason = [], ""
                else:
                    _record_turn_intent(
                        biz_state,
                        "category_flow",
                        source="done_followup",
                    )
                    stage, contents, selection_result = _run_category_flow(
                        biz_state,
                        user_text,
                        excluded_mid_categories=rejected_categories,
                        excluded_subcategories=rejected_subcategories,
                        slots_already_extracted=current_turn_slots_applied,
                    )
                    pending_categories, pending_reason = _pending_from_selection(selection_result)
            handled_done_followup = True
        else:
            biz_state, stage, pending_categories, pending_reason = _reset_biz_state(
                conversation_id, stage="init", session=session
            )

    if user_text and not is_entry_request:
        _append_history_once(biz_state, "user", user_text)

    contents: List[str]
    blocks: List[Dict[str, str]]
    selection_result: Optional[CategorySelectionResult] = None

    # 用户在早期阶段提到具体商品名，直接搜索商品，不走品类流程
    if stage in ("init", "await_need", "need_more_info") and user_text:
        direct_result = apply_direct_product_detection(
            biz_state,
            product_category_resolution.direct_product_detection,
            slots_already_extracted=current_turn_slots_applied,
        )
        if direct_result is not None and direct_result[0] == "done":
            _record_turn_intent(
                biz_state,
                "direct_product_search",
                source="early_stage",
            )
            stage, contents = direct_result
            pending_categories, pending_reason = [], ""

    if handled_done_followup:
        pass
    elif stage == "done":
        pass  # 直接商品搜索已处理
    elif stage == "init":
        if not user_text or is_entry_request:
            stage = "await_need"
            contents = [_build_need_prompt()]
            pending_categories, pending_reason = [], ""
        else:
            stage, contents, selection_result = _run_category_flow(
                biz_state,
                user_text,
                excluded_mid_categories=rejected_categories,
                excluded_subcategories=rejected_subcategories,
                slots_already_extracted=current_turn_slots_applied,
            )
            pending_categories, pending_reason = _pending_from_selection(selection_result)

    elif stage == "await_need":
        if not user_text or is_entry_request:
            contents = [_build_need_prompt()]
            pending_categories, pending_reason = [], ""
        else:
            stage, contents, selection_result = _run_category_flow(
                biz_state,
                user_text,
                excluded_mid_categories=rejected_categories,
                excluded_subcategories=rejected_subcategories,
                slots_already_extracted=current_turn_slots_applied,
            )
            pending_categories, pending_reason = _pending_from_selection(selection_result)

    elif stage == "need_more_info":
        if not user_text:
            contents = ["请再补充一下收礼对象、预算、送礼场景或偏好，方便小Q帮您缩小品类范围。"]
            pending_categories, pending_reason = [], ""
        else:
            stage, contents, selection_result = _run_category_flow(
                biz_state,
                user_text,
                excluded_mid_categories=rejected_categories,
                excluded_subcategories=rejected_subcategories,
                slots_already_extracted=current_turn_slots_applied,
            )
            pending_categories, pending_reason = _pending_from_selection(selection_result)

    elif stage == "choose_category":
        if not pending_categories:
            stage = "need_more_info"
            contents = ["请再补充一下偏好、预算或对象信息，小Q重新帮您判断品类方向。"]
            pending_categories, pending_reason = [], ""
        elif not user_text:
            _ensure_category_sample_products(
                biz_state,
                pending_categories,
                rejected_subcategories=rejected_subcategories,
            )
            contents = [
                _build_category_choice_message(
                    pending_categories, pending_reason,
                    sample_products=biz_state.filtered_products,
                )
            ]
        else:
            choice_resolution = ChooseCategoryResolution()
            if (
                not pending_selection_category
                and user_text.strip() not in _AMBIVALENT_KEYWORDS
            ):
                choice_resolution = _resolve_choose_category_reference(
                    user_text,
                    pending_categories,
                    rejected_mid_categories=rejected_categories,
                    rejected_subcategories=rejected_subcategories,
                )
                if choice_resolution.source:
                    _record_turn_intent(
                        biz_state,
                        "category_choice_resolution",
                        resolution_type=choice_resolution.resolution_type,
                        target_mid_category=choice_resolution.target_mid_category,
                        target_subcategory=choice_resolution.target_subcategory,
                        confidence=choice_resolution.confidence,
                        source=choice_resolution.source,
                        reason=choice_resolution.reason,
                        pending_categories=pending_categories,
                    )
            explicit_subcategory = choice_resolution.target_subcategory
            explicit_mid_category = choice_resolution.target_mid_category
            explicit_name = explicit_subcategory or explicit_mid_category
            if explicit_mid_category and not _is_explicit_category_rejected(user_text, explicit_name):
                is_global_switch = choice_resolution.resolution_type == "global_switch"
                _record_turn_intent(
                    biz_state,
                    "category_select",
                    target_mid_category=explicit_mid_category,
                    target_subcategory=explicit_subcategory,
                    resolution_type=choice_resolution.resolution_type,
                    confidence=choice_resolution.confidence,
                    source=choice_resolution.source or "choose_category_explicit",
                )
                if explicit_subcategory:
                    rejected_subcategories = _remove_categories(
                        rejected_subcategories,
                        [explicit_subcategory],
                    )
                    action_text = "切换到" if is_global_switch else "回选"
                    apply_subcategory_selection(
                        biz_state,
                        explicit_subcategory,
                        selection_reason=f"用户明确{action_text}“小类：{explicit_subcategory}”，因此按该小类继续推荐。",
                        description=f"用户明确{action_text}的小类：{explicit_subcategory}",
                    )
                else:
                    action_text = "切换到" if is_global_switch else "回选"
                    apply_mid_category_selection(
                        biz_state,
                        explicit_mid_category,
                        selection_reason=f"用户明确{action_text}“中类：{explicit_mid_category}”，因此按该中类继续推荐。",
                        description=f"用户明确{action_text}的中类：{explicit_mid_category}",
                    )
                rejected_categories = _remove_categories(
                    rejected_categories,
                    [explicit_mid_category],
                )
                pending_categories, pending_reason = [], ""
                stage, contents = _ask_for_details(biz_state)
            elif user_text.strip() in _AMBIVALENT_KEYWORDS and pending_categories:
                selected_cat_id = pending_categories[0]
                _record_turn_intent(
                    biz_state,
                    "category_select",
                    target_mid_category=selected_cat_id,
                    source="choose_category_ambivalent",
                )
                apply_mid_category_selection(
                    biz_state,
                    selected_cat_id,
                    selection_reason=f'用户表达”{user_text.strip()}”无偏好态度，自动选择第一个候选品类”{selected_cat_id}”。',
                    description=f'用户无偏好，系统自动选择中类：{selected_cat_id}',
                )
                rejected_categories = _remove_categories(
                    rejected_categories,
                    [selected_cat_id],
                )
                pending_categories, pending_reason = [], ""
                stage, contents = _ask_for_details(biz_state)
                contents = [f'既然您说随便，那小Q就先从【{selected_cat_id}】这个品类开始帮您挑～'] + contents
            else:
                if pending_selection_category:
                    selected_cat_id = pending_selection_category
                    match_type = pending_selection_match_type
                    selection_remainder = pending_selection_remainder
                else:
                    selected_cat_id, match_type = _resolve_pending_category_selection(user_text, pending_categories)
                    selection_remainder = ""
                if selected_cat_id:
                    _record_turn_intent(
                        biz_state,
                        "category_select",
                        target_mid_category=selected_cat_id,
                        match_type=match_type,
                        remainder_text=selection_remainder,
                        source="choose_category_pending",
                    )
                    if match_type == "index":
                        selection_reason = pending_reason or f"已从候选品类中选择“{selected_cat_id}”。"
                        description = f"用户从候选列表中按编号选择的中类：{selected_cat_id}"
                    elif match_type == "ordinal":
                        selection_reason = pending_reason or f"已根据序号表达选择“{selected_cat_id}”。"
                        description = f"用户从候选列表中按序号表达选择的中类：{selected_cat_id}"
                    else:
                        selection_reason = pending_reason or f"已根据用户表述选择“{selected_cat_id}”。"
                        description = f"用户直接指向候选中类：{selected_cat_id}"

                    apply_mid_category_selection(
                        biz_state,
                        selected_cat_id,
                        selection_reason=selection_reason,
                        description=description,
                    )
                    rejected_categories = _remove_categories(
                        rejected_categories,
                        [selected_cat_id],
                    )
                    pending_categories, pending_reason = [], ""
                    stage, contents = _ask_for_details(
                        biz_state,
                        detail_answer=selection_remainder,
                    )
                elif (
                    replace_pending_categories_requested
                    or choice_resolution.resolution_type == "reject_pending"
                ):
                    _record_turn_intent(
                        biz_state,
                        "replace_pending_categories",
                        rejected_mid_categories=pending_categories,
                        resolution_type=choice_resolution.resolution_type,
                        confidence=choice_resolution.confidence,
                        source=choice_resolution.source or "choose_category_replace",
                        reason=choice_resolution.reason,
                    )
                    rejected_categories = _merge_category_lists(
                        rejected_categories,
                        pending_categories,
                    )
                    pending_categories, pending_reason = [], ""
                    stage, contents, selection_result = _run_category_flow(
                        biz_state,
                        user_text,
                        excluded_mid_categories=rejected_categories,
                        excluded_subcategories=rejected_subcategories,
                        slots_already_extracted=current_turn_slots_applied,
                    )
                    pending_categories, pending_reason = _pending_from_selection(selection_result)
                elif _looks_like_numeric_selection_attempt(user_text):
                    _record_turn_intent(
                        biz_state,
                        "category_select_invalid",
                        source="choose_category_numeric",
                    )
                    _ensure_category_sample_products(
                        biz_state,
                        pending_categories,
                        rejected_subcategories=rejected_subcategories,
                    )
                    contents = [
                        _build_category_choice_message(
                            pending_categories,
                            pending_reason,
                            prefix=f"请输入 1-{len(pending_categories)} 之间的品类编号，或者直接说品类名称：",
                            sample_products=biz_state.filtered_products,
                        )
                    ]
                elif user_text.strip() in _AMBIVALENT_KEYWORDS and pending_categories:
                    selected_cat_id = pending_categories[0]
                    _record_turn_intent(
                        biz_state,
                        "category_select",
                        target_mid_category=selected_cat_id,
                        source="choose_category_ambivalent",
                    )
                    apply_mid_category_selection(
                        biz_state,
                        selected_cat_id,
                        selection_reason=f'用户表达”{user_text.strip()}”无偏好态度，自动选择第一个候选品类”{selected_cat_id}”。',
                        description=f'用户无偏好，系统自动选择中类：{selected_cat_id}',
                    )
                    rejected_categories = _remove_categories(
                        rejected_categories,
                        [selected_cat_id],
                    )
                    pending_categories, pending_reason = [], ''
                    stage, contents = _ask_for_details(biz_state)
                    contents = [f'既然您说随便，那小Q就先从【{selected_cat_id}】这个品类开始帮您挑～'] + contents
                else:
                    if _has_reject_pending_categories_intent(user_text) and pending_categories:
                        _record_turn_intent(
                            biz_state,
                            "category_reject_pending",
                            rejected_mid_categories=pending_categories,
                            source="choose_category_reject",
                        )
                        rejected_categories = _merge_category_lists(
                            rejected_categories,
                            pending_categories,
                        )
                    stage, contents, selection_result = _run_category_flow(
                        biz_state,
                        user_text,
                        excluded_mid_categories=rejected_categories,
                        excluded_subcategories=rejected_subcategories,
                        slots_already_extracted=current_turn_slots_applied,
                    )
                    pending_categories, pending_reason = _pending_from_selection(selection_result)

    elif stage == "detail_answer":
        if not user_text:
            stage, contents = _ask_for_details(biz_state)
        else:
            explicit_subcategory, explicit_mid_category = _resolve_explicit_category_reference_with_fallback(
                user_text,
                rejected_mid_categories=rejected_categories,
                rejected_subcategories=rejected_subcategories,
            )
            current_mid_category = _get_current_mid_category(biz_state)
            explicit_name = explicit_subcategory or explicit_mid_category
            if (
                explicit_mid_category
                and explicit_mid_category != current_mid_category
                and not _is_explicit_category_rejected(user_text, explicit_name)
            ):
                _set_task_boundary_decision(
                    biz_state,
                    {
                        "action": "category_switch",
                        "confidence": 0.9,
                        "reason": "detail stage matched explicit category switch",
                        "latest_frame": {
                            "target_mid_category": explicit_mid_category,
                            "target_subcategory": explicit_subcategory,
                            "previous_mid_category": current_mid_category,
                        },
                        "source": "engine_detail_category_switch",
                    },
                )
                _record_turn_intent(
                    biz_state,
                    "category_switch",
                    target_mid_category=explicit_mid_category,
                    target_subcategory=explicit_subcategory,
                    previous_mid_category=current_mid_category,
                    source="engine_detail_category_switch",
                )
                if explicit_subcategory:
                    rejected_subcategories = _remove_categories(
                        rejected_subcategories,
                        [explicit_subcategory],
                    )
                    apply_subcategory_selection(
                        biz_state,
                        explicit_subcategory,
                        selection_reason=f"用户明确切换到“小类：{explicit_subcategory}”，因此按该小类继续推荐。",
                        description=f"用户明确切换的小类：{explicit_subcategory}",
                    )
                else:
                    apply_mid_category_selection(
                        biz_state,
                        explicit_mid_category,
                        selection_reason=f"用户明确切换到“中类：{explicit_mid_category}”，因此按该中类继续推荐。",
                        description=f"用户明确切换的中类：{explicit_mid_category}",
                    )
                rejected_categories = _remove_categories(
                    rejected_categories,
                    [explicit_mid_category],
                )
                pending_categories, pending_reason = [], ""
                stage, contents = _ask_for_details(biz_state)
            elif _should_replace_products_in_current_category(biz_state, user_text):
                _reject_replacement_products(biz_state, query_extends_product_ids)
                _record_turn_intent(
                    biz_state,
                    "product_replacement",
                    source="detail_answer_replacement",
                )
                if not current_turn_slots_applied:
                    feature_extraction(biz_state, user_text)
                product_filtering(biz_state, user_text)
                contents = [_format_recommendation_text(biz_state)]
                stage = "done"
                pending_categories, pending_reason = [], ""
            elif _has_reject_pending_categories_intent(user_text):
                if current_mid_category:
                    _record_turn_intent(
                        biz_state,
                        "category_reject_current",
                        rejected_mid_category=current_mid_category,
                        source="detail_answer_reject",
                    )
                    rejected_categories = _merge_category_lists(
                        rejected_categories,
                        [current_mid_category],
                    )
                stage, contents, selection_result = _run_category_flow(
                    biz_state,
                    user_text,
                    excluded_mid_categories=rejected_categories,
                    excluded_subcategories=rejected_subcategories,
                    slots_already_extracted=current_turn_slots_applied,
                )
                pending_categories, pending_reason = _pending_from_selection(selection_result)
                if selection_result and selection_result.result_type == "need_more_info":
                    contents = [
                        "这个方向小Q先排除。请补充一下预算、兴趣偏好或想避开的类型，"
                        "小Q再帮您换一个更合适的品类。"
                    ]
            else:
                if not current_turn_slots_applied:
                    feature_extraction(biz_state, user_text)
                product_filtering(biz_state, user_text)
                contents = [_format_recommendation_text(biz_state)]
                stage = "done"
                pending_categories, pending_reason = [], ""

    else:
        stage = "await_need"
        contents = [_build_need_prompt()]
        pending_categories, pending_reason = [], ""

    _append_assistant_history(biz_state, contents)
    setattr(biz_state, "rejected_subcategories", rejected_subcategories)
    setattr(biz_state, "rejected_mid_categories", rejected_categories)

    if pending_categories:
        _ensure_category_sample_products(
            biz_state,
            pending_categories,
            rejected_subcategories=rejected_subcategories,
        )
    else:
        _ensure_three_products(biz_state, user_text)
    blocks = _build_recommendation_blocks(biz_state, contents)
    action = "SEND_CARD" if len(blocks) > 1 else "CHAT"

    return {
        "action": action,
        "data_blocks": blocks,
        "new_slots": session.get("collected_slots", {}),
        "isGiftIntention": True,
        "session_updates": {
            _STATE_KEY: biz_state,
            _STAGE_KEY: stage,
            _PENDING_CATEGORIES_KEY: pending_categories,
            _PENDING_REASON_KEY: pending_reason,
            _REJECTED_CATEGORIES_KEY: rejected_categories,
            _REJECTED_SUBCATEGORIES_KEY: rejected_subcategories,
        },
    }


def _run_flow_boundary_parallel(
    state: GiftRecommendationState,
    stage: str,
    user_text: str,
    pending_categories: Optional[List[str]] = None,
) -> FlowBoundaryResults:
    text = (user_text or "").strip()
    has_gift_continuation_signal = _has_gift_continuation_signal(
        state,
        stage,
        text,
        pending_categories=pending_categories,
    )
    results = FlowBoundaryResults(
        has_gift_continuation_signal=has_gift_continuation_signal
    )
    if not text or text in _ENTRY_KEYWORDS:
        return results

    if _is_non_gift_service_intent(text):
        if not has_gift_continuation_signal:
            results.should_route_out = True
        return results

    unified_results = _detect_flow_boundary_by_llm(
        state,
        stage,
        text,
        pending_categories=pending_categories,
        has_gift_continuation_signal=has_gift_continuation_signal,
    )
    if unified_results is not None:
        return unified_results

    print("[flow-boundary] unified decision invalid; falling back to legacy calls")
    return _run_flow_boundary_legacy_parallel(
        state,
        stage,
        text,
        pending_categories=pending_categories,
        has_gift_continuation_signal=has_gift_continuation_signal,
    )


def _detect_flow_boundary_by_llm(
    state: GiftRecommendationState,
    stage: str,
    user_text: str,
    pending_categories: Optional[List[str]] = None,
    has_gift_continuation_signal: bool = False,
) -> Optional[FlowBoundaryResults]:
    allow_exit = not has_gift_continuation_signal and not (
        stage == "init" and not state.chat_history
    )
    pending_category_text = "、".join(
        _normalize_category_list(pending_categories or [])
    ) or "无"
    prompt = (
        f"当前送礼流程阶段：{stage}\n"
        f"allow_exit：{str(allow_exit).lower()}\n"
        "has_gift_continuation_signal："
        f"{str(has_gift_continuation_signal).lower()}\n\n"
        f"最近对话历史：\n{_build_recent_history_text(state.chat_history)}\n\n"
        f"当前已选品类：{_get_current_mid_category(state) or '无'}\n"
        f"待用户选择的品类：{pending_category_text}\n\n"
        f"用户最新输入：\n{user_text}\n\n"
        "请返回唯一的边界动作。"
    )
    try:
        result = call_json(
            prompt=prompt,
            system_prompt=_FLOW_BOUNDARY_SYSTEM_PROMPT,
            temperature=0.1,
        )
    except Exception as exc:
        print(f"统一流程边界 LLM 判断失败: {exc}")
        return None

    if not isinstance(result, dict):
        return None
    action = str(result.get("action", "") or "").strip().lower()
    if action not in {"route_out", "exit_flow", "continue_gift"}:
        return None

    try:
        confidence = float(result.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    results = FlowBoundaryResults(
        has_gift_continuation_signal=has_gift_continuation_signal
    )
    if action == "route_out":
        results.should_route_out = (
            not has_gift_continuation_signal
            and confidence >= _GIFT_BOUNDARY_LLM_CONFIDENCE_THRESHOLD
        )
    elif action == "exit_flow":
        results.should_exit = (
            allow_exit
            and confidence >= _EXIT_LLM_CONFIDENCE_THRESHOLD
        )
    return results


def _run_flow_boundary_legacy_parallel(
    state: GiftRecommendationState,
    stage: str,
    user_text: str,
    pending_categories: Optional[List[str]] = None,
    has_gift_continuation_signal: bool = False,
) -> FlowBoundaryResults:
    results = FlowBoundaryResults(
        has_gift_continuation_signal=has_gift_continuation_signal
    )
    route_state = copy.deepcopy(state)
    exit_state = copy.deepcopy(state) if not has_gift_continuation_signal else None
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            submit_with_llm_trace(
                executor,
                _should_route_out_of_gift_flow,
                route_state,
                stage,
                user_text,
                pending_categories=list(pending_categories or []),
                has_gift_continuation_signal=has_gift_continuation_signal,
                group="flow_boundary.route_out",
            ): "route_out"
        }
        if exit_state is not None:
            futures[
                submit_with_llm_trace(
                    executor,
                    _should_exit_by_llm,
                    exit_state,
                    stage,
                    user_text,
                    group="flow_boundary.exit",
                )
            ] = "exit"

        for future in as_completed(futures):
            task_name = futures[future]
            try:
                value = bool(future.result())
            except Exception as exc:
                print(f"[flow-boundary-parallel-error] task={task_name} error={exc}")
                value = False

            if task_name == "route_out":
                results.should_route_out = value
            elif task_name == "exit":
                results.should_exit = value

    return results


def _should_exit_by_llm(
    state: GiftRecommendationState,
    stage: str,
    user_text: str,
) -> bool:
    user_text = (user_text or "").strip()
    if not user_text:
        return False

    # Avoid extra model calls when there is no active gift-flow context yet.
    if stage == "init" and not state.chat_history:
        return False

    prompt = (
        f"当前送礼流程阶段：{stage}\n\n"
        f"最近对话历史：\n{_build_recent_history_text(state.chat_history)}\n\n"
        f"用户最新输入：\n{user_text}\n\n"
        "请判断用户是否想结束当前送礼推荐流程。"
    )

    try:
        result = call_json(
            prompt=prompt,
            system_prompt=_EXIT_INTENT_SYSTEM_PROMPT,
            temperature=0.1,
        )
    except Exception:
        return False

    raw_should_exit = result.get("should_exit", False)
    if isinstance(raw_should_exit, str):
        raw_should_exit = raw_should_exit.strip().lower() in {"true", "1", "yes"}

    try:
        confidence = float(result.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    return bool(raw_should_exit) and confidence >= _EXIT_LLM_CONFIDENCE_THRESHOLD


def _build_exit_response(
    session: Dict[str, Any],
    message: str = "好的，小Q这就为您结束送礼推荐流程。",
) -> Dict[str, Any]:
    return {
        "action": "EXIT",
        "data_blocks": _build_text_blocks([message]),
        "new_slots": session.get("collected_slots", {}),
        "isGiftIntention": False,
        "session_updates": {
            _STATE_KEY: None,
            _STAGE_KEY: "init",
            _PENDING_CATEGORIES_KEY: [],
            _PENDING_REASON_KEY: "",
            _REJECTED_CATEGORIES_KEY: [],
            _REJECTED_SUBCATEGORIES_KEY: [],
        },
    }


def _should_route_out_of_gift_flow(
    state: GiftRecommendationState,
    stage: str,
    user_text: str,
    pending_categories: Optional[List[str]] = None,
    has_gift_continuation_signal: Optional[bool] = None,
) -> bool:
    user_text = (user_text or "").strip()
    if not user_text:
        return False

    if has_gift_continuation_signal is None:
        has_gift_continuation_signal = _has_gift_continuation_signal(
            state,
            stage,
            user_text,
            pending_categories=pending_categories,
        )

    if _is_non_gift_service_intent(user_text):
        if has_gift_continuation_signal:
            return False
        return True

    if user_text in _ENTRY_KEYWORDS:
        return False

    route_out = _should_route_out_of_gift_flow_by_llm(state, stage, user_text)
    if route_out and has_gift_continuation_signal:
        return False
    return route_out


def _has_gift_continuation_signal(
    state: GiftRecommendationState,
    stage: str,
    user_text: str,
    pending_categories: Optional[List[str]] = None,
) -> bool:
    del stage
    text = (user_text or "").strip()
    if not text:
        return False

    if _has_budget_signal(text):
        return True

    strong_gift_keywords = (
        "送礼",
        "礼物",
        "生日",
        "节日",
        "父亲节",
        "母亲节",
        "情人节",
        "纪念日",
        "结婚",
        "婚礼",
        "乔迁",
        "拜访",
        "探望",
    )
    if any(keyword in text for keyword in strong_gift_keywords):
        return True

    relation_keywords = (
        "妈妈",
        "母亲",
        "爸爸",
        "父亲",
        "男朋友",
        "男友",
        "女朋友",
        "女友",
        "老婆",
        "妻子",
        "老公",
        "丈夫",
        "孩子",
        "朋友",
        "闺蜜",
        "同事",
        "领导",
        "客户",
        "老师",
        "长辈",
    )
    if any(keyword in text for keyword in relation_keywords):
        return True

    preference_keywords = (
        "喜欢",
        "偏好",
        "风格",
        "禁忌",
        "不要",
        "别太",
        "避开",
        "适合",
        "实用",
        "体面",
        "正式",
        "有质感",
        "包装",
    )
    if any(keyword in text for keyword in preference_keywords):
        return True

    if _normalize_category_list(pending_categories or []):
        for category in pending_categories or []:
            if category and str(category) in text:
                return True

    selected_category = getattr(state, "selected_category", None)
    selected_name = str(getattr(selected_category, "category_name", "") or "").strip()
    if selected_name and selected_name in text:
        return True

    if _find_mid_categories_in_text(text) or _find_subcategories_in_text(text):
        return True

    return False


def _has_budget_signal(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return False
    if "预算" in value:
        return True
    return bool(
        re.search(
            r"\d+(?:\.\d+)?\s*(?:元|块|rmb|RMB)\s*(?:左右|以内|以下|以上|上下)?",
            value,
        )
        or re.search(
            r"(?:不超过|别超过|不要超过|控制在|最高|最多|大概|大约)\s*\d+(?:\.\d+)?",
            value,
        )
    )


def _apply_category_preference_updates(
    state: GiftRecommendationState,
    user_text: str,
    rejected_mid_categories: List[str],
    rejected_subcategories: List[str],
) -> Tuple[List[str], List[str]]:
    negative_mid_categories, negative_subcategories = _resolve_negative_category_references(
        user_text
    )
    positive_mid_categories, positive_subcategories = _resolve_positive_category_references(
        user_text
    )

    return _apply_category_preference_reference_updates(
        state,
        rejected_mid_categories,
        rejected_subcategories,
        negative_mid_categories,
        negative_subcategories,
        positive_mid_categories,
        positive_subcategories,
    )


def _apply_category_preference_reference_updates(
    state: GiftRecommendationState,
    rejected_mid_categories: List[str],
    rejected_subcategories: List[str],
    negative_mid_categories: List[str],
    negative_subcategories: List[str],
    positive_mid_categories: List[str],
    positive_subcategories: List[str],
) -> Tuple[List[str], List[str]]:
    if negative_mid_categories:
        rejected_mid_categories = _merge_category_lists(
            rejected_mid_categories,
            negative_mid_categories,
        )
    if negative_subcategories:
        rejected_subcategories = _merge_category_lists(
            rejected_subcategories,
            negative_subcategories,
        )

    if positive_mid_categories:
        rejected_mid_categories = _remove_categories(
            rejected_mid_categories,
            positive_mid_categories,
        )
    if positive_subcategories:
        rejected_subcategories = _remove_categories(
            rejected_subcategories,
            positive_subcategories,
        )

    if positive_mid_categories or positive_subcategories:
        setattr(state, "_cleared_taboo_mid_categories", positive_mid_categories)
        setattr(state, "_cleared_taboo_subcategories", positive_subcategories)
        _remove_categories_from_taboo(
            state,
            positive_mid_categories,
            positive_subcategories,
        )

    setattr(state, "rejected_mid_categories", rejected_mid_categories)
    setattr(state, "rejected_subcategories", rejected_subcategories)
    return rejected_mid_categories, rejected_subcategories


def _resolve_negative_category_references(user_text: str) -> Tuple[List[str], List[str]]:
    text = (user_text or "").strip()
    if not text or not _has_negative_category_signal(text):
        return [], []

    mid_categories, subcategories = _resolve_category_references_by_rule(
        text,
        expect_negative=True,
    )
    if mid_categories or subcategories:
        return mid_categories, subcategories

    return _resolve_negative_category_references_by_llm(text)


def _resolve_positive_category_references(user_text: str) -> Tuple[List[str], List[str]]:
    text = (user_text or "").strip()
    if not text or not _has_positive_category_signal(text):
        return [], []

    mid_categories, subcategories = _resolve_category_references_by_rule(
        text,
        expect_negative=False,
    )
    if mid_categories or subcategories:
        return mid_categories, subcategories

    return _resolve_positive_category_references_by_llm(text)


def _resolve_category_references_by_rule(
    text: str,
    expect_negative: bool,
) -> Tuple[List[str], List[str]]:
    mid_categories: List[str] = []
    subcategories: List[str] = []

    for subcategory in _find_subcategories_in_text(text):
        is_rejected = _is_category_rejected_expression(text, subcategory)
        if expect_negative and is_rejected:
            subcategories.append(subcategory)
        elif not expect_negative and not is_rejected and _is_category_positive_expression(text, subcategory):
            subcategories.append(subcategory)

    for mid_category in _find_mid_categories_in_text(text):
        is_rejected = _is_category_rejected_expression(text, mid_category)
        if expect_negative and is_rejected:
            mid_categories.append(mid_category)
        elif not expect_negative and not is_rejected and _is_category_positive_expression(text, mid_category):
            mid_categories.append(mid_category)

    return _normalize_category_list(mid_categories), _normalize_category_list(subcategories)


def _resolve_negative_category_references_by_llm(user_text: str) -> Tuple[List[str], List[str]]:
    prompt = _build_category_preference_prompt(user_text)
    try:
        result = call_json(
            prompt=prompt,
            system_prompt=_NEGATIVE_CATEGORY_SYSTEM_PROMPT,
            temperature=0.1,
        )
    except Exception as e:
        print(f"负向品类约束 LLM 判断失败: {e}")
        return [], []

    try:
        confidence = float(result.get("confidence", 0.0) or 0.0) if isinstance(result, dict) else 0.0
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < _CATEGORY_PREFERENCE_LLM_CONFIDENCE_THRESHOLD:
        return [], []

    return _validate_category_preference_result(
        result.get("rejected_mid_categories", []) if isinstance(result, dict) else [],
        result.get("rejected_subcategories", []) if isinstance(result, dict) else [],
    )


def _resolve_positive_category_references_by_llm(user_text: str) -> Tuple[List[str], List[str]]:
    prompt = _build_category_preference_prompt(user_text)
    try:
        result = call_json(
            prompt=prompt,
            system_prompt=_POSITIVE_CATEGORY_SYSTEM_PROMPT,
            temperature=0.1,
        )
    except Exception as e:
        print(f"正向品类改口 LLM 判断失败: {e}")
        return [], []

    try:
        confidence = float(result.get("confidence", 0.0) or 0.0) if isinstance(result, dict) else 0.0
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < _CATEGORY_PREFERENCE_LLM_CONFIDENCE_THRESHOLD:
        return [], []

    return _validate_category_preference_result(
        result.get("positive_mid_categories", []) if isinstance(result, dict) else [],
        result.get("positive_subcategories", []) if isinstance(result, dict) else [],
    )


def _resolve_explicit_category_reference_with_fallback(
    user_text: str,
    pending_categories: Optional[List[str]] = None,
    rejected_mid_categories: Optional[List[str]] = None,
    rejected_subcategories: Optional[List[str]] = None,
) -> Tuple[str, str]:
    explicit_subcategory, explicit_mid_category = _resolve_explicit_category_reference(user_text)
    if explicit_mid_category:
        explicit_name = explicit_subcategory or explicit_mid_category
        if _is_explicit_category_rejected(user_text, explicit_name):
            return "", ""
        return _validate_explicit_category_reference(
            explicit_mid_category,
            explicit_subcategory,
            pending_categories=pending_categories,
            rejected_mid_categories=rejected_mid_categories,
            rejected_subcategories=rejected_subcategories,
        )

    if not _should_try_explicit_category_llm(user_text, pending_categories):
        return "", ""

    return _resolve_explicit_category_reference_by_llm(
        user_text,
        pending_categories=pending_categories,
        rejected_mid_categories=rejected_mid_categories,
        rejected_subcategories=rejected_subcategories,
    )


def _should_try_explicit_category_llm(
    user_text: str,
    pending_categories: Optional[List[str]] = None,
) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    if _has_negative_category_signal(text) and not _has_positive_category_signal(text):
        return False
    if _normalize_category_list(pending_categories or []):
        return True
    return (
        _has_positive_category_signal(text)
        or _looks_like_category_switch(text)
        or _looks_like_category_switch_uncertain(text)
    )


def _resolve_explicit_category_reference_by_llm(
    user_text: str,
    pending_categories: Optional[List[str]] = None,
    rejected_mid_categories: Optional[List[str]] = None,
    rejected_subcategories: Optional[List[str]] = None,
) -> Tuple[str, str]:
    prompt = _build_explicit_category_reference_prompt(
        user_text,
        pending_categories=pending_categories,
        rejected_mid_categories=rejected_mid_categories,
        rejected_subcategories=rejected_subcategories,
    )
    try:
        result = call_json(
            prompt=prompt,
            system_prompt=_EXPLICIT_CATEGORY_REFERENCE_SYSTEM_PROMPT,
            temperature=0.1,
        )
    except Exception as e:
        print(f"显式品类兜底 LLM 判断失败: {e}")
        return "", ""

    if not isinstance(result, dict):
        return "", ""

    raw_matched = result.get("matched", False)
    if isinstance(raw_matched, str):
        raw_matched = raw_matched.strip().lower() in {"true", "1", "yes"}
    if not raw_matched:
        return "", ""

    try:
        confidence = float(result.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < _EXPLICIT_CATEGORY_LLM_CONFIDENCE_THRESHOLD:
        return "", ""

    return _validate_explicit_category_reference(
        str(result.get("target_mid_category", "") or "").strip(),
        str(result.get("target_subcategory", "") or "").strip(),
        pending_categories=pending_categories,
        rejected_mid_categories=rejected_mid_categories,
        rejected_subcategories=rejected_subcategories,
    )


def _normalize_mid_category_reference(category_name: str) -> str:
    category_name = (category_name or "").strip()
    if not category_name:
        return ""
    complete_mid_to_big_map = _get_complete_mid_to_big_category_map()
    if category_name in complete_mid_to_big_map:
        return category_name
    for mid_category, aliases in _PENDING_CATEGORY_ALIAS_MAP.items():
        if category_name == mid_category or category_name in aliases:
            return mid_category if mid_category in complete_mid_to_big_map else ""
    return ""


def _validate_explicit_category_reference(
    mid_category: str,
    subcategory: str,
    pending_categories: Optional[List[str]] = None,
    rejected_mid_categories: Optional[List[str]] = None,
    rejected_subcategories: Optional[List[str]] = None,
) -> Tuple[str, str]:
    allowed_mid_categories = set(_normalize_category_list(pending_categories or []))
    rejected_mid_set = set(_normalize_category_list(rejected_mid_categories or []))
    rejected_sub_set = set(_normalize_category_list(rejected_subcategories or []))

    subcategory = (subcategory or "").strip()
    mid_category = (mid_category or "").strip()
    complete_small_to_mid_map = _get_complete_small_to_mid_category_map()
    normalized_mid_category = _normalize_mid_category_reference(mid_category)
    if normalized_mid_category:
        mid_category = normalized_mid_category

    if subcategory:
        subcategory_as_mid = _normalize_mid_category_reference(subcategory)
        if subcategory_as_mid and subcategory not in complete_small_to_mid_map:
            if subcategory_as_mid in rejected_mid_set:
                return "", ""
            if allowed_mid_categories and subcategory_as_mid not in allowed_mid_categories:
                return "", ""
            return "", subcategory_as_mid
        derived_mid_category = complete_small_to_mid_map.get(subcategory, "")
        if not derived_mid_category:
            return "", ""
        mid_category = mid_category or derived_mid_category
        if mid_category != derived_mid_category:
            return "", ""
        if subcategory in rejected_sub_set or mid_category in rejected_mid_set:
            return "", ""
        if allowed_mid_categories and mid_category not in allowed_mid_categories:
            return "", ""
        return subcategory, mid_category

    if mid_category not in _get_complete_mid_to_big_category_map():
        return "", ""
    if mid_category in rejected_mid_set:
        return "", ""
    if allowed_mid_categories and mid_category not in allowed_mid_categories:
        return "", ""
    return "", mid_category


def _build_explicit_category_reference_prompt(
    user_text: str,
    pending_categories: Optional[List[str]] = None,
    rejected_mid_categories: Optional[List[str]] = None,
    rejected_subcategories: Optional[List[str]] = None,
) -> str:
    pending_categories = _normalize_category_list(pending_categories or [])
    rejected_mid_categories = _normalize_category_list(rejected_mid_categories or [])
    rejected_subcategories = _normalize_category_list(rejected_subcategories or [])
    mode = "pending_candidates" if pending_categories else "all_known_categories"
    pending_text = "、".join(pending_categories) if pending_categories else "(empty)"
    rejected_mid_text = "、".join(rejected_mid_categories) if rejected_mid_categories else "(empty)"
    rejected_sub_text = "、".join(rejected_subcategories) if rejected_subcategories else "(empty)"

    return (
        f"mode: {mode}\n\n"
        f"pending_candidate_mid_categories:\n{pending_text}\n\n"
        f"known_mid_categories:\n{_build_mid_category_candidate_text()}\n\n"
        f"known_subcategories:\n{_build_subcategory_candidate_text()}\n\n"
        f"rejected_mid_categories:\n{rejected_mid_text}\n\n"
        f"rejected_subcategories:\n{rejected_sub_text}\n\n"
        f"user_latest_message:\n{user_text}\n\n"
        "Resolve only an explicit category/subcategory reference from user_latest_message."
    )


def _resolve_choose_category_reference(
    user_text: str,
    pending_categories: List[str],
    rejected_mid_categories: Optional[List[str]] = None,
    rejected_subcategories: Optional[List[str]] = None,
) -> ChooseCategoryResolution:
    text = (user_text or "").strip()
    pending_categories = _normalize_category_list(pending_categories or [])
    if not text or not pending_categories:
        return ChooseCategoryResolution(source="choose_category_empty")

    explicit_subcategory, explicit_mid_category = _resolve_explicit_category_reference(text)
    explicit_name = explicit_subcategory or explicit_mid_category
    if (
        explicit_mid_category
        and not _is_explicit_category_rejected(text, explicit_name)
    ):
        resolution_type = (
            "pending_select"
            if explicit_mid_category in pending_categories
            else "global_switch"
        )
        validated_subcategory, validated_mid_category = _validate_explicit_category_reference(
            explicit_mid_category,
            explicit_subcategory,
            pending_categories=pending_categories if resolution_type == "pending_select" else None,
            rejected_mid_categories=rejected_mid_categories,
            rejected_subcategories=rejected_subcategories,
        )
        if validated_mid_category:
            return ChooseCategoryResolution(
                resolution_type=resolution_type,
                target_mid_category=validated_mid_category,
                target_subcategory=validated_subcategory,
                confidence=1.0,
                source=f"choose_category_{resolution_type}_rule",
                reason="规则命中用户显式提到的标准品类。",
            )

    if _looks_like_numeric_selection_attempt(text):
        return ChooseCategoryResolution(source="choose_category_numeric_skip")

    result = _resolve_choose_category_reference_by_llm(
        text,
        pending_categories=pending_categories,
        rejected_mid_categories=rejected_mid_categories,
        rejected_subcategories=rejected_subcategories,
    )
    return result


def _resolve_choose_category_reference_by_llm(
    user_text: str,
    pending_categories: List[str],
    rejected_mid_categories: Optional[List[str]] = None,
    rejected_subcategories: Optional[List[str]] = None,
) -> ChooseCategoryResolution:
    prompt = _build_choose_category_reference_prompt(
        user_text,
        pending_categories=pending_categories,
        rejected_mid_categories=rejected_mid_categories,
        rejected_subcategories=rejected_subcategories,
    )
    try:
        result = call_json(
            prompt=prompt,
            system_prompt=_CHOOSE_CATEGORY_REFERENCE_SYSTEM_PROMPT,
            temperature=0.1,
        )
    except Exception as e:
        print(f"候选品类选择 LLM 判断失败: {e}")
        return ChooseCategoryResolution(source="choose_category_llm_error")

    return _validate_choose_category_reference_result(
        result,
        user_text=user_text,
        pending_categories=pending_categories,
        rejected_mid_categories=rejected_mid_categories,
        rejected_subcategories=rejected_subcategories,
    )


def _validate_choose_category_reference_result(
    result: Any,
    user_text: str,
    pending_categories: List[str],
    rejected_mid_categories: Optional[List[str]] = None,
    rejected_subcategories: Optional[List[str]] = None,
) -> ChooseCategoryResolution:
    if not isinstance(result, dict):
        return ChooseCategoryResolution(source="choose_category_llm_invalid")

    raw_matched = result.get("matched", False)
    if isinstance(raw_matched, str):
        raw_matched = raw_matched.strip().lower() in {"true", "1", "yes"}

    resolution_type = str(result.get("resolution_type", "") or "").strip()
    if resolution_type not in {
        "pending_select",
        "global_switch",
        "reject_pending",
        "no_match",
    }:
        resolution_type = "no_match"

    try:
        confidence = float(result.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    reason = str(result.get("reason", "") or "").strip()
    if not raw_matched or resolution_type == "no_match":
        return ChooseCategoryResolution(
            resolution_type="no_match",
            confidence=confidence,
            source="choose_category_llm_no_match",
            reason=reason,
        )

    if confidence < _EXPLICIT_CATEGORY_LLM_CONFIDENCE_THRESHOLD:
        return ChooseCategoryResolution(
            resolution_type="no_match",
            confidence=confidence,
            source="choose_category_llm_low_confidence",
            reason=reason,
        )

    if resolution_type == "reject_pending":
        return ChooseCategoryResolution(
            resolution_type="reject_pending",
            confidence=confidence,
            source="choose_category_llm_reject_pending",
            reason=reason,
        )

    target_mid_category = str(result.get("target_mid_category", "") or "").strip()
    target_subcategory = str(result.get("target_subcategory", "") or "").strip()
    pending_categories = _normalize_category_list(pending_categories or [])

    if resolution_type == "pending_select":
        validated_subcategory, validated_mid_category = _validate_explicit_category_reference(
            target_mid_category,
            target_subcategory,
            pending_categories=pending_categories,
            rejected_mid_categories=rejected_mid_categories,
            rejected_subcategories=rejected_subcategories,
        )
        if not validated_mid_category:
            return ChooseCategoryResolution(
                resolution_type="no_match",
                confidence=confidence,
                source="choose_category_llm_invalid_pending_target",
                reason=reason,
            )
        return ChooseCategoryResolution(
            resolution_type="pending_select",
            target_mid_category=validated_mid_category,
            target_subcategory=validated_subcategory,
            confidence=confidence,
            source="choose_category_pending_llm",
            reason=reason,
        )

    validated_subcategory, validated_mid_category = _validate_explicit_category_reference(
        target_mid_category,
        target_subcategory,
        rejected_mid_categories=rejected_mid_categories,
        rejected_subcategories=rejected_subcategories,
    )
    if not validated_mid_category:
        return ChooseCategoryResolution(
            resolution_type="no_match",
            confidence=confidence,
            source="choose_category_llm_invalid_global_target",
            reason=reason,
        )
    if validated_mid_category in pending_categories:
        return ChooseCategoryResolution(
            resolution_type="pending_select",
            target_mid_category=validated_mid_category,
            target_subcategory=validated_subcategory,
            confidence=confidence,
            source="choose_category_pending_llm",
            reason=reason,
        )

    explicit_name = validated_subcategory or validated_mid_category
    if _is_explicit_category_rejected(user_text, explicit_name):
        return ChooseCategoryResolution(
            resolution_type="no_match",
            confidence=confidence,
            source="choose_category_llm_rejected_target",
            reason=reason,
        )
    return ChooseCategoryResolution(
        resolution_type="global_switch",
        target_mid_category=validated_mid_category,
        target_subcategory=validated_subcategory,
        confidence=confidence,
        source="choose_category_global_llm",
        reason=reason,
    )


def _build_choose_category_reference_prompt(
    user_text: str,
    pending_categories: List[str],
    rejected_mid_categories: Optional[List[str]] = None,
    rejected_subcategories: Optional[List[str]] = None,
) -> str:
    pending_categories = _normalize_category_list(pending_categories or [])
    rejected_mid_categories = _normalize_category_list(rejected_mid_categories or [])
    rejected_subcategories = _normalize_category_list(rejected_subcategories or [])
    pending_text = "、".join(pending_categories) if pending_categories else "(empty)"
    rejected_mid_text = "、".join(rejected_mid_categories) if rejected_mid_categories else "(empty)"
    rejected_sub_text = "、".join(rejected_subcategories) if rejected_subcategories else "(empty)"

    return (
        "mode: choose_category_with_pending_candidates\n\n"
        f"pending_candidate_mid_categories:\n{pending_text}\n\n"
        f"known_mid_categories:\n{_build_mid_category_candidate_text()}\n\n"
        f"known_subcategories:\n{_build_subcategory_candidate_text()}\n\n"
        f"rejected_mid_categories:\n{rejected_mid_text}\n\n"
        f"rejected_subcategories:\n{rejected_sub_text}\n\n"
        f"user_latest_message:\n{user_text}\n\n"
        "Resolve the user's category choice for this choose_category turn."
    )


def _build_category_preference_prompt(user_text: str) -> str:
    return (
        f"候选中类：\n{_build_mid_category_candidate_text()}\n\n"
        f"候选小类：\n{_build_subcategory_candidate_text()}\n\n"
        f"用户最新输入：\n{user_text}\n\n"
        "请只基于用户最新输入识别明确的品类约束。"
    )


def _validate_category_preference_result(
    mid_categories: Any,
    subcategories: Any,
) -> Tuple[List[str], List[str]]:
    valid_mid_categories = set(_get_complete_mid_to_big_category_map().keys())
    valid_subcategories = set(_get_complete_small_to_mid_category_map().keys())
    filtered_mid_categories = [
        item for item in _normalize_category_list(mid_categories)
        if item in valid_mid_categories
    ]
    filtered_subcategories = [
        item for item in _normalize_category_list(subcategories)
        if item in valid_subcategories
    ]
    return filtered_mid_categories, filtered_subcategories


def _find_subcategories_in_text(text: str) -> List[str]:
    matched_keys = [
        key for key in subcategory_keyword_map.keys()
        if key and key in text
    ]
    matched_keys.sort(key=len, reverse=True)
    subcategories: List[str] = []
    for key in matched_keys:
        subcategory = subcategory_keyword_map.get(key, "")
        if subcategory and subcategory not in subcategories:
            subcategories.append(subcategory)
    return subcategories


def _find_mid_categories_in_text(text: str) -> List[str]:
    mid_categories = [
        category for category in _get_complete_mid_to_big_category_map().keys()
        if category and category in text
    ]
    mid_categories.sort(key=len, reverse=True)
    return _normalize_category_list(mid_categories)


def _is_category_rejected_expression(text: str, category_name: str) -> bool:
    text = (text or "").strip()
    category_name = (category_name or "").strip()
    if not text or not category_name:
        return False

    escaped_category = re.escape(category_name)
    reject_patterns = [
        rf"(不要|不想要|不考虑|不推荐|别推荐|别送|不送|排除|避开).{{0,10}}{escaped_category}",
        rf"{escaped_category}.{{0,10}}(不要|不想要|不考虑|不推荐|别推荐|别送|不送|排除|避开|算了)",
    ]
    return any(re.search(pattern, text) for pattern in reject_patterns)


def _is_category_positive_expression(text: str, category_name: str) -> bool:
    text = (text or "").strip()
    category_name = (category_name or "").strip()
    if not text or not category_name:
        return False

    escaped_category = re.escape(category_name)
    positive_patterns = [
        rf"(想要|想看|看看|看下|推荐|要|送|买|换成|改成|还是|就).{{0,10}}{escaped_category}",
        rf"{escaped_category}.{{0,10}}(也行|可以|不错|推荐|看看|看下|就行|吧)",
    ]
    return any(re.search(pattern, text) for pattern in positive_patterns)


def _has_negative_category_signal(text: str) -> bool:
    return any(signal in (text or "") for signal in _NEGATIVE_CATEGORY_SIGNALS)


def _has_positive_category_signal(text: str) -> bool:
    return any(signal in (text or "") for signal in _POSITIVE_CATEGORY_SIGNALS)


def _remove_categories_from_taboo(
    state: GiftRecommendationState,
    mid_categories: List[str],
    subcategories: List[str],
) -> None:
    slot = state.filled_slots.get("taboo")
    if not slot or getattr(slot, "value", None) is None:
        return

    removal_terms = set(_normalize_category_list(subcategories))
    for mid_category in _normalize_category_list(mid_categories):
        removal_terms.add(mid_category)
        removal_terms.update(
            small for small, mid in _get_complete_small_to_mid_category_map().items()
            if mid == mid_category
        )

    if not removal_terms:
        return

    original_value = slot.value
    if isinstance(original_value, list):
        kept = [
            str(item).strip()
            for item in original_value
            if str(item).strip() and not _text_mentions_any_category(str(item), removal_terms)
        ]
        slot.value = kept or None
    else:
        parts = [
            part.strip()
            for part in re.split(r"[，,、;；\s]+", str(original_value))
            if part.strip()
        ]
        kept = [
            part for part in parts
            if not _text_mentions_any_category(part, removal_terms)
        ]
        slot.value = "，".join(kept) if kept else None

    slot.is_filled = slot.value is not None


def _text_mentions_any_category(text: str, categories: set) -> bool:
    return any(category and category in text for category in categories)


def _build_mid_category_candidate_text() -> str:
    return build_mid_category_candidate_text(_get_complete_mid_to_big_category_map(), max_items=500)


def _build_subcategory_candidate_text() -> str:
    return build_small_category_candidate_text(
        _get_complete_small_to_mid_category_map(),
        max_items=500,
    )


def _is_non_gift_service_intent(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    return any(keyword in text for keyword in _NON_GIFT_SERVICE_KEYWORDS)


def _should_route_out_of_gift_flow_by_llm(
    state: GiftRecommendationState,
    stage: str,
    user_text: str,
) -> bool:
    prompt = (
        f"当前送礼流程阶段：{stage}\n\n"
        f"最近对话历史：\n{_build_recent_history_text(state.chat_history)}\n\n"
        f"当前已选品类：{_get_current_mid_category(state) or '无'}\n\n"
        f"用户最新输入：\n{user_text}\n\n"
        "请判断用户最新输入是否已经超出送礼选品/推荐业务范围。"
    )

    try:
        result = call_json(
            prompt=prompt,
            system_prompt=_GIFT_BOUNDARY_SYSTEM_PROMPT,
            temperature=0.1,
        )
    except Exception as e:
        print(f"送礼业务边界 LLM 判断失败: {e}")
        return False

    raw_route_out = result.get("route_out", False) if isinstance(result, dict) else False
    if isinstance(raw_route_out, str):
        raw_route_out = raw_route_out.strip().lower() in {"true", "1", "yes"}

    try:
        confidence = float(result.get("confidence", 0.0) or 0.0) if isinstance(result, dict) else 0.0
    except (TypeError, ValueError):
        confidence = 0.0

    return bool(raw_route_out) and confidence >= _GIFT_BOUNDARY_LLM_CONFIDENCE_THRESHOLD


def _load_biz_snapshot(
    conversation_id: str,
    session: Dict[str, Any],
) -> Tuple[GiftRecommendationState, str, List[str], str]:
    state = session.get(_STATE_KEY)
    if isinstance(state, GiftRecommendationState):
        biz_state = copy.deepcopy(state)
    else:
        biz_state = GiftRecommendationState(
            user_id=str(session.get("user_id", "") or conversation_id),
            session_id=conversation_id,
            account_id=str(session.get("account_id", "") or ""),
        )
        biz_state.chat_history = _build_history_from_host_session(session)
    _sync_context_to_biz_state(biz_state, session)

    stage = str(session.get(_STAGE_KEY, "init") or "init")
    pending_categories = list(session.get(_PENDING_CATEGORIES_KEY, []) or [])
    pending_reason = str(session.get(_PENDING_REASON_KEY, "") or "")
    return biz_state, stage, pending_categories, pending_reason


def _reset_biz_state(
    conversation_id: str,
    stage: str = "init",
    session: Optional[Dict[str, Any]] = None,
) -> Tuple[GiftRecommendationState, str, List[str], str]:
    session = session or {}
    state = GiftRecommendationState(
        user_id=str(session.get("user_id", "") or conversation_id),
        session_id=conversation_id,
        account_id=str(session.get("account_id", "") or ""),
    )
    _sync_context_to_biz_state(state, session)
    return state, stage, [], ""


def _sync_context_to_biz_state(
    state: GiftRecommendationState,
    session: Dict[str, Any],
) -> None:
    state.account_id = str(session.get("account_id", "") or getattr(state, "account_id", "") or "")
    member_profile = session.get("member_profile", {})
    if isinstance(member_profile, dict):
        state.member_profile = copy.deepcopy(member_profile)
    query_context = session.get("query_context", {})
    if isinstance(query_context, dict):
        state.query_context = copy.deepcopy(query_context)


def _build_history_from_host_session(session: Dict[str, Any]) -> List[Dict[str, str]]:
    history: List[Dict[str, str]] = []
    for msg in session.get("llm_history", []):
        role = msg.get("role")
        content = str(msg.get("content", "") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        history.append({"role": role, "content": content})
    return history


def _append_history_once(state: GiftRecommendationState, role: str, content: str) -> None:
    content = (content or "").strip()
    if not content:
        return

    if state.chat_history:
        last_msg = state.chat_history[-1]
        if last_msg.get("role") == role and last_msg.get("content") == content:
            return

    state.chat_history.append({"role": role, "content": content})


def _empty_current_turn_slot_extraction_result() -> Dict[str, Any]:
    return {
        "slot_updates": {},
        "budget_update": {"mentioned": False},
        "raw_filled_slots": [],
    }


def _run_turn_understanding_parallel(
    state: GiftRecommendationState,
    stage: str,
    user_text: str,
    pending_categories: Optional[List[str]] = None,
) -> TurnUnderstandingResults:
    text = (user_text or "").strip()
    results = TurnUnderstandingResults(
        slot_extraction_result=_empty_current_turn_slot_extraction_result()
    )
    if not text:
        return results

    boundary_state = copy.deepcopy(state)
    slot_state = copy.deepcopy(state)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            submit_with_llm_trace(
                executor,
                detect_gift_task_boundary,
                boundary_state,
                stage,
                text,
                pending_categories=pending_categories,
                group="turn_understanding.boundary",
            ): "boundary",
            submit_with_llm_trace(
                executor,
                extract_current_turn_slot_updates,
                slot_state,
                text,
                group="turn_understanding.slots",
            ): "slots",
            submit_with_llm_trace(
                executor,
                _resolve_negative_category_references,
                text,
                group="turn_understanding.negative_preference",
            ): "negative",
            submit_with_llm_trace(
                executor,
                _resolve_positive_category_references,
                text,
                group="turn_understanding.positive_preference",
            ): "positive",
        }

        for future in as_completed(futures):
            task_name = futures[future]
            try:
                value = future.result()
            except Exception as exc:
                print(f"[turn-understanding-parallel-error] task={task_name} error={exc}")
                continue

            if task_name == "boundary" and isinstance(value, GiftTaskBoundaryDecision):
                results.boundary_decision = value
            elif task_name == "slots" and isinstance(value, dict):
                results.slot_extraction_result = value
            elif task_name == "negative":
                mid_categories, subcategories = _coerce_category_pair(value)
                results.negative_mid_categories = mid_categories
                results.negative_subcategories = subcategories
            elif task_name == "positive":
                mid_categories, subcategories = _coerce_category_pair(value)
                results.positive_mid_categories = mid_categories
                results.positive_subcategories = subcategories

    return results


def _run_product_category_resolution_parallel(
    stage: str,
    user_text: str,
) -> ProductCategoryResolutionResults:
    text = (user_text or "").strip()
    results = ProductCategoryResolutionResults()
    if not text:
        return results

    tasks: List[Tuple[str, Any]] = []
    if stage in ("init", "await_need", "need_more_info"):
        tasks.append(("direct_product", detect_direct_product_query))
    if not tasks:
        return results

    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = {
            submit_with_llm_trace(
                executor,
                func,
                text,
                group=f"product_category.{task_name}",
            ): task_name
            for task_name, func in tasks
        }
        for future in as_completed(futures):
            task_name = futures[future]
            try:
                value = future.result()
            except Exception as exc:
                print(f"[product-category-resolution-error] task={task_name} error={exc}")
                continue

            if task_name == "direct_product" and isinstance(value, DirectProductDetectionResult):
                results.direct_product_detection = value

    return results


def _coerce_category_pair(value: Any) -> Tuple[List[str], List[str]]:
    if not isinstance(value, tuple) or len(value) != 2:
        return [], []
    mid_categories, subcategories = value
    return (
        _normalize_category_list(mid_categories or []),
        _normalize_category_list(subcategories or []),
    )


def _init_turn_understanding(
    state: GiftRecommendationState,
    stage: str,
    user_text: str,
) -> None:
    if not (user_text or "").strip():
        state.turn_understanding = {}
        state.current_turn_slot_updates = {}
        return
    state.turn_understanding = {
        "stage_before": stage,
        "user_text": user_text,
        "intents": [],
        "slot_updates": {},
    }
    state.current_turn_slot_updates = {}


def _apply_current_turn_slot_updates(
    state: GiftRecommendationState,
    user_text: str,
) -> bool:
    current_text = (user_text or "").strip()
    if not current_text:
        return False
    extraction_result = extract_current_turn_slot_updates(state, current_text)
    return _apply_current_turn_slot_extraction_result(state, extraction_result)


def _apply_current_turn_slot_extraction_result(
    state: GiftRecommendationState,
    extraction_result: Dict[str, Any],
) -> bool:
    extraction_result = extraction_result or _empty_current_turn_slot_extraction_result()
    applied_result = apply_current_turn_slot_updates(state, extraction_result)
    state.current_turn_slot_updates = applied_result
    if not isinstance(getattr(state, "turn_understanding", None), dict):
        state.turn_understanding = {}
    state.turn_understanding["slot_updates"] = applied_result
    budget_update = applied_result.get("budget_update", {}) if isinstance(applied_result, dict) else {}
    if isinstance(budget_update, dict) and budget_update.get("mentioned"):
        _record_turn_intent(
            state,
            "budget_update",
            budget_min=budget_update.get("budget_min"),
            budget_max=budget_update.get("budget_max"),
            mode=budget_update.get("mode", ""),
            source=budget_update.get("source", "rule_budget"),
        )
    applied_slots = applied_result.get("applied", {}) if isinstance(applied_result, dict) else {}
    non_budget_slots = [
        slot_name
        for slot_name in applied_slots
        if slot_name not in {"budget_min", "budget_max"}
    ]
    if non_budget_slots:
        _record_turn_intent(
            state,
            "slot_update",
            slots=non_budget_slots,
            source="current_turn_slot_updates",
        )
    return True


def _record_turn_intent(
    state: GiftRecommendationState,
    intent_type: str,
    **payload: Any,
) -> None:
    if not isinstance(getattr(state, "turn_understanding", None), dict):
        state.turn_understanding = {"intents": []}
    intents = state.turn_understanding.setdefault("intents", [])
    if isinstance(intents, list):
        intents.append({"type": intent_type, **payload})


def _append_assistant_history(state: GiftRecommendationState, contents: List[str]) -> None:
    joined = "\n".join([c for c in contents if c]).strip()
    if joined:
        state.chat_history.append({"role": "assistant", "content": joined})


def _clear_product_recommendation_state(state: GiftRecommendationState) -> None:
    state.filtered_products = []
    state.final_product_cards = []
    state.candidate_products = []
    state.candidate_pool_summary = {}
    state.candidate_pool_reason = ""
    state.downgrade_retry_triggered = False
    state.downgrade_retry_reason = ""


def _set_task_boundary_decision(
    state: GiftRecommendationState,
    decision: Dict[str, Any],
) -> None:
    if not isinstance(decision, dict) or not decision:
        return
    try:
        setattr(state, "task_boundary_decision", copy.deepcopy(decision))
    except Exception:
        pass


def _capture_correct_current_task_slot_state(state: GiftRecommendationState) -> Dict[str, Any]:
    portable_slot_names = (
        "budget_min",
        "budget_max",
    )
    result: Dict[str, Any] = {}
    slots = getattr(state, "filled_slots", {}) or {}
    for slot_name in portable_slot_names:
        slot = slots.get(slot_name)
        if not slot:
            continue
        if getattr(slot, "value", None) is None:
            continue
        result[slot_name] = copy.deepcopy(slot)
    return result


def _restore_slot_state(
    state: GiftRecommendationState,
    slot_state: Dict[str, Any],
) -> None:
    if not isinstance(slot_state, dict) or not slot_state:
        return
    if not isinstance(getattr(state, "filled_slots", None), dict):
        state.filled_slots = {}
    for slot_name, slot in slot_state.items():
        if not slot_name or slot is None:
            continue
        state.filled_slots[slot_name] = copy.deepcopy(slot)


def _capture_category_selection_state(state: GiftRecommendationState) -> Dict[str, Any]:
    selected_category = getattr(state, "selected_category", None)
    if not selected_category:
        return {}
    return {
        "selected_category": copy.deepcopy(selected_category),
        "selected_subcategory": copy.deepcopy(getattr(state, "selected_subcategory", None)),
        "selected_mid_category": copy.deepcopy(getattr(state, "selected_mid_category", None)),
        "selected_big_category": copy.deepcopy(getattr(state, "selected_big_category", None)),
        "category_level": str(getattr(state, "category_level", "") or ""),
    }


def _restore_category_selection_state(
    state: GiftRecommendationState,
    category_state: Dict[str, Any],
) -> None:
    selected_category = category_state.get("selected_category")
    if not selected_category:
        return
    state.selected_category = copy.deepcopy(selected_category)
    state.selected_subcategory = copy.deepcopy(category_state.get("selected_subcategory"))
    state.selected_mid_category = copy.deepcopy(category_state.get("selected_mid_category"))
    state.selected_big_category = copy.deepcopy(category_state.get("selected_big_category"))
    state.category_level = str(category_state.get("category_level", "") or "")


def _looks_like_category_switch(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False

    switch_patterns = [
        r"(不要|不用|别).{0,8}(了|啦|吧|，|,|。).{0,8}(想|要|还是|换|改|送|买|找|推荐)",
        r"(不送|别送|不要送|不用送|不买|别买|不要买|不用买|不推荐|别推荐|不要推荐).{0,12}(了|啦|吧|，|,|。|$)",
        r"(换|改).{0,4}(成|为|送|买|推荐|看)",
        r"(还是|重新|再).{0,6}(送|买|推荐|看|找)",
        r"(想|要).{0,4}(换|改)",
    ]
    return any(re.search(pattern, text) for pattern in switch_patterns)


def _try_category_group_recommendation(
    state: GiftRecommendationState,
    user_text: str,
) -> Optional[Tuple[str, List[str], CategorySelectionResult]]:
    group_name, mid_categories = _detect_category_group(user_text)
    if not group_name or not mid_categories:
        return None

    products = _search_products_by_mid_categories(state, user_text, mid_categories)
    if not products:
        return None

    state.filtered_products = products
    state.candidate_products = products[:50]
    try:
        state.final_product_cards = format_recommendation_cards(products, max_items=10)
    except Exception:
        state.final_product_cards = []

    selection_result = CategorySelectionResult(
        result_type="direct_mid_category",
        selected_category_name=group_name,
        selection_reason=f"检测到用户明确提到“{group_name}”，已直接在相关中类中推荐商品。",
    )
    return "done", [_format_recommendation_text(state)], selection_result


def _detect_category_group(user_text: str) -> Tuple[str, List[str]]:
    text = (user_text or "").strip()
    if not text:
        return "", []

    alcohol_patterns = [
        r"(送|买|找|推荐|想送|想买|换成|改成|改送).{0,10}(酒类|酒)(?!精|杯)",
        r"(酒类|酒)(?!精|杯).{0,6}(礼物|礼品|送礼)",
    ]
    if any(re.search(pattern, text) for pattern in alcohol_patterns):
        return "酒类", list(_CATEGORY_GROUP_ALIASES["酒"])

    return "", []


def _search_products_by_mid_categories(
    state: GiftRecommendationState,
    user_text: str,
    mid_categories: List[str],
    limit: int = 10,
):
    budget_min = _coerce_slot_float(state, "budget_min")
    budget_max = _coerce_slot_float(state, "budget_max")
    taboo_keywords = _tokenize_light(str(_get_slot_value(state, "taboo") or ""))
    query_tokens = _tokenize_light(user_text)
    mid_category_set = set(mid_categories)
    rejected_product_id_set = set(
        _normalize_product_id_list(getattr(state, "rejected_product_ids", []) or [])
    )

    scored = []
    for product in _load_products_from_csv():
        if str(getattr(product, "sku_id", "") or "").strip() in rejected_product_id_set:
            continue
        mid_category = str(getattr(product, "mid_category", "") or "")
        if mid_category not in mid_category_set:
            continue
        if budget_min is not None and product.price < budget_min:
            continue
        if budget_max is not None and product.price > budget_max:
            continue

        search_text = product_search_text(product)
        normalized_search_text = search_text.lower()
        if taboo_keywords and any(keyword in normalized_search_text for keyword in taboo_keywords):
            continue

        score = 0
        for index, category in enumerate(mid_categories):
            if mid_category == category:
                score += max(0, len(mid_categories) - index)
                break
        for token in query_tokens:
            if token in {"送", "买", "找", "推荐", "想送", "想买", "不要", "不用", "还是"}:
                continue
            if token and token in normalized_search_text:
                score += 2
        scored.append((score, product.price, product))

    scored.sort(key=lambda item: (-item[0], item[1]))

    # Round-robin: 每个中类轮流取，确保品类多样性
    by_category: Dict[str, List[Any]] = {}
    for _, __, product in scored:
        mid = str(getattr(product, "mid_category", "") or "")
        if mid not in by_category:
            by_category[mid] = []
        by_category[mid].append(product)

    result: List[Any] = []
    while len(result) < limit:
        added_any = False
        for cat in mid_categories:
            if cat in by_category and by_category[cat]:
                result.append(by_category[cat].pop(0))
                added_any = True
                if len(result) >= limit:
                    break
        if not added_any:
            break
    return result


def _get_slot_value(state: GiftRecommendationState, slot_name: str) -> Any:
    slot = state.filled_slots.get(slot_name)
    if not slot:
        return None
    return getattr(slot, "value", None)


def _coerce_slot_float(state: GiftRecommendationState, slot_name: str) -> Optional[float]:
    value = _get_slot_value(state, slot_name)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tokenize_light(text: str) -> List[str]:
    tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{1,}", str(text or "").lower())
    return [token for token in tokens if token]


def _build_recent_history_text(history: List[Dict[str, str]], limit: int = 6) -> str:
    if not history:
        return "(无)"

    recent_history = history[-limit:]
    lines: List[str] = []
    for msg in recent_history:
        role = str(msg.get("role", "") or "").strip() or "unknown"
        content = str(msg.get("content", "") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(无)"


def _should_switch_category(
    state: GiftRecommendationState,
    stage: str,
    user_text: str,
) -> Tuple[bool, str, str]:
    rejected_mid_categories = getattr(state, "rejected_mid_categories", []) or []
    rejected_subcategories = getattr(state, "rejected_subcategories", []) or []
    explicit_subcategory, explicit_mid_category = _resolve_explicit_category_reference_with_fallback(
        user_text,
        rejected_mid_categories=rejected_mid_categories,
        rejected_subcategories=rejected_subcategories,
    )
    current_mid_category = _get_current_mid_category(state)
    current_subcategory = _get_current_subcategory(state)
    explicit_name = explicit_subcategory or explicit_mid_category
    if explicit_mid_category and not _is_explicit_category_rejected(user_text, explicit_name):
        if explicit_subcategory:
            if explicit_subcategory != current_subcategory:
                return True, explicit_mid_category, explicit_subcategory
            return False, "", ""
        if explicit_mid_category != current_mid_category:
            return True, explicit_mid_category, explicit_subcategory
        return False, "", ""

    if _looks_like_category_switch(user_text):
        return True, "", ""

    should_switch, target_category = _should_switch_category_by_llm(
        state,
        stage,
        user_text,
    )
    if not should_switch:
        return False, "", ""

    target_subcategory, target_mid_category = _resolve_explicit_category_reference_with_fallback(
        target_category,
        rejected_mid_categories=rejected_mid_categories,
        rejected_subcategories=rejected_subcategories,
    )
    if target_mid_category:
        if target_subcategory and target_subcategory != current_subcategory:
            return True, target_mid_category, target_subcategory
        if not target_subcategory and target_mid_category != current_mid_category:
            return True, target_mid_category, target_subcategory
        return False, "", ""
    if target_category:
        return False, "", ""
    return True, "", ""


def _looks_like_category_switch_uncertain(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    weak_signals = (
        "换",
        "更换",
        "改",
        "看看",
        "看下",
        "想看",
        "推荐",
        "别的",
        "其他",
        "不合适",
        "不要这个",
    )
    return any(signal in text for signal in weak_signals)


def _should_switch_category_by_llm(
    state: GiftRecommendationState,
    stage: str,
    user_text: str,
) -> Tuple[bool, str]:
    current_mid_category = _get_current_mid_category(state)
    candidate_categories = _build_category_switch_candidate_text()
    prompt = (
        f"当前流程阶段：\n{stage}\n\n"
        f"当前已选品类：\n{current_mid_category or '无'}\n\n"
        f"候选品类名称：\n{candidate_categories}\n\n"
        f"最近对话历史：\n{_build_recent_history_text(getattr(state, 'chat_history', []) or [])}\n\n"
        f"用户最新输入：\n{user_text}\n\n"
        "请判断用户是否想切换到另一个商品品类。"
    )

    try:
        result = call_json(
            prompt=prompt,
            system_prompt=_CATEGORY_SWITCH_INTENT_SYSTEM_PROMPT,
            temperature=0.1,
        )
    except Exception as e:
        print(f"品类切换意图 LLM 判断失败: {e}")
        return False, ""

    raw_should_switch = result.get("should_switch_category", False) if isinstance(result, dict) else False
    if isinstance(raw_should_switch, str):
        raw_should_switch = raw_should_switch.strip().lower() in {"true", "1", "yes"}

    try:
        confidence = float(result.get("confidence", 0.0) or 0.0) if isinstance(result, dict) else 0.0
    except (TypeError, ValueError):
        confidence = 0.0

    target_category = str(result.get("target_category", "") or "").strip() if isinstance(result, dict) else ""
    if not raw_should_switch or confidence < 0.75:
        return False, ""

    return True, target_category


def _build_category_switch_candidate_text() -> str:
    categories = list(_get_complete_mid_to_big_category_map().keys())
    aliases = list(subcategory_keyword_map.keys())
    merged: List[str] = []
    seen = set()
    for category in categories + aliases:
        if not category or category in seen:
            continue
        merged.append(category)
        seen.add(category)
    return "、".join(merged)


def _build_need_prompt_legacy() -> str:
    return (
        "欢迎进入智能选品模块，请问您的送礼需求是什么，"
        "可以告诉小Q您的送礼对象关系、性别、年龄以及送礼预算、偏好等信息。"
    )


def _build_need_prompt() -> str:
    try:
        prompt = call_text(
            prompt=(
                "请为送礼推荐流程生成一句入口引导话术，语气亲切自然。"
                "需要引导用户说明送礼对象关系、性别、年龄、预算和偏好。"
                "主语使用小Q"
            ),
            system_prompt=_NEED_PROMPT_SYSTEM_PROMPT,
            temperature=0.7,
        ).strip()
    except Exception as e:
        print(f"入口话术生成失败: {e}")
        return _DEFAULT_NEED_PROMPT

    if not prompt:
        return _DEFAULT_NEED_PROMPT

    prompt = re.sub(r"^```(?:text)?\s*|\s*```$", "", prompt).strip()
    prompt = prompt.strip("\"'“”‘’")

    if len(prompt) > 120:
        return _DEFAULT_NEED_PROMPT

    return prompt or _DEFAULT_NEED_PROMPT


def _build_category_choice_message(
    categories: List[str],
    selection_reason: str = "",
    prefix: str = "根据您的需求，这里为您推荐以下品类：",
    sample_products: Optional[List[Any]] = None,
) -> str:
    del selection_reason  # Internal reasoning only; do not expose raw model rationale.
    lines = [prefix]
    for i, cat_id in enumerate(categories, 1):
        line = f"{i}. {cat_id}"
        if sample_products and i <= len(sample_products):
            p = sample_products[i - 1]
            p_name = getattr(p, "sku_name", None) or getattr(p, "name", "")
            p_price = getattr(p, "price", 0)
            line += f"  ——  {p_name}  ¥{p_price}"
        lines.append(line)
    lines.append("请选择品类编号，也可以直接回复品类名称。")
    return "\n".join(lines)


def _resolve_pending_category_selection(
    user_text: str,
    pending_categories: List[str],
) -> Tuple[str, str]:
    selected_category, match_type, _ = _resolve_pending_category_selection_with_remainder(
        user_text,
        pending_categories,
    )
    return selected_category, match_type


def _resolve_pending_category_selection_with_remainder(
    user_text: str,
    pending_categories: List[str],
) -> Tuple[str, str, str]:
    normalized_text = (user_text or "").strip()
    if not normalized_text or not pending_categories:
        return "", "", ""

    if normalized_text.isdigit():
        selected_index = int(normalized_text) - 1
        if 0 <= selected_index < len(pending_categories):
            return pending_categories[selected_index], "index", ""
        return "", "index", ""

    index_match = _find_pending_category_index_match(
        normalized_text,
        len(pending_categories),
    )
    if index_match is not None:
        selected_index, match_type, start, end = index_match
        return (
            pending_categories[selected_index],
            match_type,
            _remove_selection_reference(normalized_text, start, end),
        )

    matched_category, start, end = _match_pending_category_by_name_with_span(
        normalized_text,
        pending_categories,
    )
    if matched_category:
        return matched_category, "name", _remove_selection_reference(normalized_text, start, end)

    matched_category, start, end = _match_pending_category_by_alias_with_span(
        normalized_text,
        pending_categories,
    )
    if matched_category:
        return matched_category, "alias", _remove_selection_reference(normalized_text, start, end)

    return "", "", ""


def _find_pending_category_index_match(
    text: str,
    pending_count: int,
) -> Optional[Tuple[int, str, int, int]]:
    if pending_count <= 0:
        return None

    numeric_patterns = [
        (r"(?:我选|选择|选|要|看|就|先看|先要|想看|想要)\s*第?\s*([1-9])\s*(?:个|项|类|种|号)?", "index_mixed"),
        (r"第\s*([1-9])\s*(?:个|项|类|种|号)?", "ordinal_mixed"),
        (r"(?<!\d)([1-9])\s*(?:个|项|类|种|号)(?!\d)", "ordinal_mixed"),
        (r"(?<!\d)([1-9])(?!\d)", "index_mixed"),
    ]
    for pattern, match_type in numeric_patterns:
        for match in re.finditer(pattern, text):
            selected_index = int(match.group(1)) - 1
            if not 0 <= selected_index < pending_count:
                continue
            if not _is_valid_pending_selection_number_context(text, match.start(1), match.end(1)):
                continue
            return selected_index, match_type, match.start(), match.end()

    chinese_number_map = {"一": 0, "二": 1, "两": 1, "三": 2}
    chinese_patterns = [
        (r"(?:我选|选择|选|要|看|就|先看|先要|想看|想要)\s*第?\s*([一二两三])\s*(?:个|项|类|种|号)?", "ordinal_mixed"),
        (r"第\s*([一二两三])\s*(?:个|项|类|种|号)?", "ordinal_mixed"),
        (r"([一二两三])\s*(?:个|项|类|种|号)", "ordinal_mixed"),
    ]
    for pattern, match_type in chinese_patterns:
        for match in re.finditer(pattern, text):
            selected_index = chinese_number_map.get(match.group(1), -1)
            if 0 <= selected_index < pending_count:
                return selected_index, match_type, match.start(), match.end()

    return None


def _is_valid_pending_selection_number_context(text: str, start: int, end: int) -> bool:
    before = _nearest_non_space_char(text, start - 1, step=-1)
    after = _nearest_non_space_char(text, end, step=1)
    if before in {"-", "~", "～", "到", "至"} or after in {"-", "~", "～", "到", "至"}:
        return False
    if after in {"元", "块", "百", "千", "万", "k", "K", "w", "W", "折"}:
        return False
    if text[end:end + 2].lower() in {"ml", "cm", "kg"}:
        return False
    return True


def _nearest_non_space_char(text: str, index: int, step: int) -> str:
    while 0 <= index < len(text):
        char = text[index]
        if not char.isspace():
            return char
        index += step
    return ""


def _remove_selection_reference(text: str, start: int, end: int) -> str:
    remainder = f"{text[:start]}{text[end:]}".strip()
    remainder = re.sub(r"^(我选|选择|选|要|看|就|先看|先要|想看|想要)\s*", "", remainder)
    remainder = re.sub(r"^[\s,，。.;；:：、\-~～]+", "", remainder)
    remainder = re.sub(r"[\s,，。.;；:：、\-~～]+$", "", remainder)
    return remainder.strip()


def _parse_ordinal_selection_index(user_text: str) -> Optional[int]:
    text = (user_text or "").strip()
    if not text:
        return None

    ordinal_patterns = [
        r"第\s*([1-9]\d*)\s*(个|项|类|种)?",
        r"([1-9]\d*)\s*(个|项|类|种|号)",
    ]
    for pattern in ordinal_patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1)) - 1

    chinese_number_map = {
        "一": 0,
        "二": 1,
        "两": 1,
        "三": 2,
        "四": 3,
        "五": 4,
        "六": 5,
        "七": 6,
        "八": 7,
        "九": 8,
        "十": 9,
    }

    chinese_ordinal_patterns = [
        r"第\s*([一二两三四五六七八九十])\s*(个|项|类|种)?",
        r"([一二两三四五六七八九十])\s*(个|项|类|种|号)",
    ]
    for pattern in chinese_ordinal_patterns:
        match = re.search(pattern, text)
        if match:
            return chinese_number_map.get(match.group(1))

    return None


def _match_pending_category_by_name(user_text: str, pending_categories: List[str]) -> str:
    matched_category, _, _ = _match_pending_category_by_name_with_span(
        user_text,
        pending_categories,
    )
    return matched_category


def _match_pending_category_by_name_with_span(
    user_text: str,
    pending_categories: List[str],
) -> Tuple[str, int, int]:
    text = (user_text or "").strip()
    if not text:
        return "", 0, 0

    for category in pending_categories:
        if category:
            start = text.find(category)
            if start >= 0:
                return category, start, start + len(category)

    exact_text = text.replace(" ", "")
    for category in pending_categories:
        normalized_category = category.replace(" ", "")
        if normalized_category and exact_text == normalized_category:
            return category, 0, len(text)

    return "", 0, 0


def _match_pending_category_by_alias_with_span(
    user_text: str,
    pending_categories: List[str],
) -> Tuple[str, int, int]:
    text = (user_text or "").strip()
    if not text:
        return "", 0, 0

    matches: List[Tuple[int, int, str, str]] = []
    for category in pending_categories or []:
        aliases = _PENDING_CATEGORY_ALIAS_MAP.get(category, [])
        for alias in aliases:
            if not alias:
                continue
            start = text.find(alias)
            if start < 0:
                continue
            if _is_explicit_category_rejected(text, alias) or _is_explicit_category_rejected(text, category):
                continue
            matches.append((len(alias), start, category, alias))

    if not matches:
        return "", 0, 0

    matches.sort(key=lambda item: (-item[0], item[1], pending_categories.index(item[2])))
    best_length, best_start, best_category, best_alias = matches[0]
    same_best = [
        item for item in matches
        if item[0] == best_length and item[1] == best_start and item[3] == best_alias
    ]
    if len({item[2] for item in same_best}) > 1:
        return "", 0, 0
    return best_category, best_start, best_start + len(best_alias)


def _looks_like_numeric_selection_attempt(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False

    if text.isdigit():
        return True

    attempt_patterns = [
        r"第\s*[1-9一二两三四五六七八九十]",
        r"[1-9]\d*\s*(个|项|类|种|号)",
        r"[一二两三四五六七八九十]\s*(个|项|类|种|号)",
    ]
    return any(re.search(pattern, text) for pattern in attempt_patterns)


def _normalize_category_list(categories: Any) -> List[str]:
    if not isinstance(categories, list):
        return []

    normalized: List[str] = []
    seen = set()
    for category in categories:
        if not isinstance(category, str):
            continue
        category = category.strip()
        if not category or category in seen:
            continue
        normalized.append(category)
        seen.add(category)
    return normalized


def _merge_category_lists(*category_lists: List[str]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for categories in category_lists:
        for category in _normalize_category_list(categories):
            if category in seen:
                continue
            merged.append(category)
            seen.add(category)
    return merged


def _remove_categories(categories: List[str], categories_to_remove: List[str]) -> List[str]:
    remove_set = set(_normalize_category_list(categories_to_remove))
    if not remove_set:
        return _normalize_category_list(categories)
    return [
        category
        for category in _normalize_category_list(categories)
        if category not in remove_set
    ]


def _normalize_product_id_list(product_ids: Any) -> List[str]:
    if not isinstance(product_ids, list):
        return []

    normalized: List[str] = []
    seen = set()
    for product_id in product_ids:
        product_id = str(product_id or "").strip()
        if not product_id or product_id in seen:
            continue
        normalized.append(product_id)
        seen.add(product_id)
    return normalized


def _merge_product_id_lists(*product_id_lists: List[str]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for product_ids in product_id_lists:
        for product_id in _normalize_product_id_list(product_ids):
            if product_id in seen:
                continue
            merged.append(product_id)
            seen.add(product_id)
    if len(merged) > _MAX_TRACKED_PRODUCT_IDS:
        return merged[-_MAX_TRACKED_PRODUCT_IDS:]
    return merged


def _product_ids_from_products(products: List[Any]) -> List[str]:
    result: List[str] = []
    for product in products or []:
        product_id = str(getattr(product, "sku_id", "") or "").strip()
        if product_id:
            result.append(product_id)
    return _normalize_product_id_list(result)


def _reset_product_tracking(state: GiftRecommendationState) -> None:
    state.seen_product_ids = []
    state.rejected_product_ids = []
    state.last_recommended_product_ids = []
    state.last_recommended_products_snapshot = []


def _reject_replacement_products(
    state: GiftRecommendationState,
    explicit_product_ids: Optional[List[str]] = None,
) -> None:
    product_ids = _merge_product_id_lists(
        _normalize_product_id_list(getattr(state, "last_recommended_product_ids", []) or []),
        _product_ids_from_products(getattr(state, "filtered_products", []) or []),
        _normalize_product_id_list(explicit_product_ids or []),
    )
    if not product_ids:
        return
    state.rejected_product_ids = _merge_product_id_lists(
        getattr(state, "rejected_product_ids", []) or [],
        product_ids,
    )
    state.seen_product_ids = _merge_product_id_lists(
        getattr(state, "seen_product_ids", []) or [],
        product_ids,
    )
    state.filtered_products = []
    state.final_product_cards = []
    state.candidate_products = []
    setattr(state, "force_full_catalog_on_next_filter", True)


def _record_recommended_products(
    state: GiftRecommendationState,
    products: List[Any],
) -> None:
    product_ids = _product_ids_from_products(products)
    state.last_recommended_product_ids = product_ids
    snapshot: List[Dict[str, Any]] = []
    for product in products or []:
        product_id = str(getattr(product, "sku_id", "") or "").strip()
        try:
            price = float(getattr(product, "price", 0) or 0)
        except (TypeError, ValueError):
            price = 0.0
        if not product_id or price <= 0:
            continue
        snapshot.append(
            {
                "product_id": product_id,
                "name": str(getattr(product, "name", "") or ""),
                "price": price,
            }
        )
    state.last_recommended_products_snapshot = snapshot
    if product_ids:
        state.seen_product_ids = _merge_product_id_lists(
            getattr(state, "seen_product_ids", []) or [],
            product_ids,
        )


def _filter_rejected_products(
    state: GiftRecommendationState,
    products: List[Any],
) -> List[Any]:
    rejected_product_ids = set(
        _normalize_product_id_list(getattr(state, "rejected_product_ids", []) or [])
    )
    if not rejected_product_ids:
        return list(products or [])
    return [
        product
        for product in (products or [])
        if str(getattr(product, "sku_id", "") or "").strip() not in rejected_product_ids
    ]


def _is_product_replacement_request(user_text: str) -> bool:
    return _has_replacement_intent(user_text) and not _is_category_level_replacement_request(user_text)


def _should_replace_pending_categories(
    stage: str,
    pending_categories: List[str],
    state: GiftRecommendationState,
    user_text: str,
) -> bool:
    if stage != "choose_category" or not pending_categories:
        return False
    if getattr(state, "selected_category", None):
        return False
    return _has_pending_category_replacement_intent(user_text)


def _should_replace_products_in_current_category(
    state: GiftRecommendationState,
    user_text: str,
) -> bool:
    if not _is_product_replacement_request(user_text):
        return False
    if _has_explicit_new_category_request(state, user_text):
        return False
    return bool(
        _get_current_mid_category(state)
        or _get_current_subcategory(state)
        or getattr(state, "filtered_products", None)
        or getattr(state, "last_recommended_product_ids", None)
    )


def _has_pending_category_replacement_intent(user_text: str) -> bool:
    return _has_replacement_intent(user_text)


def _has_replacement_intent(user_text: str) -> bool:
    text = (user_text or "").strip().lower()
    if not text:
        return False
    compact = re.sub(r"\s+", "", text)
    signals = (
        "replace",
        "refresh",
        "another one",
        "other products",
        "other options",
        "better one",
        "more suitable",
        "换一批",
        "换批",
        "换一个",
        "换个",
        "换几个",
        "换几款",
        "换一组",
        "换一下",
        "换掉",
        "更换",
        "替换",
        "重新推荐",
        "重新推",
        "重新给",
        "再推荐",
        "再推几个",
        "再推几款",
        "再看看",
        "再看几个",
        "看看其他",
        "看看别的",
        "看下其他",
        "看下别的",
        "还有别的",
        "还有其他",
        "还有没有别的",
        "还有没有其他",
        "有没有别的",
        "有没有其他",
        "有没有更合适",
        "有没有更适合",
        "有没有更匹配",
        "有没有更好",
        "有没有类似",
        "有没有相似",
        "有类似推荐",
        "有相似推荐",
        "类似推荐",
        "相似推荐",
        "类似的推荐",
        "相似的推荐",
        "类似的",
        "相似的",
        "差不多的",
        "同类型",
        "同类推荐",
        "更合适的",
        "更适合的",
        "更匹配的",
        "更好的",
        "更优",
        "更优选",
        "更推荐",
        "别的推荐",
        "其他推荐",
        "其他选择",
        "别的选择",
        "不要这个",
        "这个不要",
        "这个不行",
        "这些不行",
        "这个不合适",
        "这些不合适",
        "这个不太合适",
        "这些不太合适",
        "这几个不合适",
        "这几个不太合适",
        "不喜欢这个",
        "不喜欢这些",
        "这个一般",
        "这些一般",
        "这几个一般",
        "不太满意",
        "不够合适",
        "不够匹配",
    )
    return any(signal in compact for signal in signals)


def _is_category_level_replacement_request(user_text: str) -> bool:
    compact = re.sub(r"\s+", "", user_text or "")
    category_level_terms = (
        "品类",
        "类目",
        "类别",
        "方向",
        "路线",
    )
    replacement_terms = (
        "换",
        "更换",
        "替换",
        "重新",
        "其他",
        "别的",
        "不合适",
        "不喜欢",
    )
    return any(term in compact for term in category_level_terms) and any(
        term in compact for term in replacement_terms
    )


def _has_explicit_new_category_request(
    state: GiftRecommendationState,
    user_text: str,
) -> bool:
    explicit_subcategory, explicit_mid_category = _resolve_explicit_category_reference(user_text)
    explicit_name = explicit_subcategory or explicit_mid_category
    if not explicit_mid_category or _is_explicit_category_rejected(user_text, explicit_name):
        return False
    current_mid_category = _get_current_mid_category(state)
    current_subcategory = _get_current_subcategory(state)
    if explicit_subcategory:
        return explicit_subcategory != current_subcategory
    return explicit_mid_category != current_mid_category


def _extract_product_ids_from_query_extends(query_extends: Any) -> List[str]:
    product_ids: List[str] = []
    id_keys = {"productid", "product_id", "skuid", "sku_id"}

    def visit(value: Any, key_name: str = "") -> None:
        key_lower = key_name.lower()
        if key_lower in id_keys:
            if isinstance(value, list):
                product_ids.extend(str(item or "").strip() for item in value)
            else:
                product_ids.append(str(value or "").strip())
            return
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                visit(child_value, str(child_key))
            return
        if isinstance(value, list):
            for item in value:
                visit(item)

    visit(query_extends)
    return _normalize_product_id_list(product_ids)


def _query_extends_has_replacement_action(query_extends: Any) -> bool:
    action_signals = (
        "replace",
        "change",
        "switch",
        "refresh",
        "reject",
        "dislike",
        "remove",
        "换",
        "更换",
        "替换",
        "不喜欢",
        "不合适",
    )

    def visit(value: Any, key_name: str = "") -> bool:
        key_lower = key_name.lower()
        if any(signal in key_lower for signal in action_signals):
            return True
        if isinstance(value, dict):
            return any(visit(child_value, str(child_key)) for child_key, child_value in value.items())
        if isinstance(value, list):
            return any(visit(item) for item in value)
        value_text = str(value or "").strip().lower()
        return any(signal in value_text for signal in action_signals)

    return visit(query_extends)


def _get_complete_small_to_mid_category_map() -> Dict[str, str]:
    return _catalog_complete_small_to_mid_category_map(SMALL_TO_MID_CATEGORY_MAP)


def _get_complete_mid_to_big_category_map() -> Dict[str, str]:
    return _catalog_complete_mid_to_big_category_map(MID_CATEGORY_TO_BIG_CATEGORY_MAP)


def _get_current_mid_category(state: GiftRecommendationState) -> str:
    selected_mid_category = str(getattr(state, "selected_mid_category", "") or "")
    if selected_mid_category:
        return selected_mid_category

    selected_category = getattr(state, "selected_category", None)
    category_name = str(getattr(selected_category, "category_name", "") or "")
    if category_name in _get_complete_mid_to_big_category_map():
        return category_name
    return _get_complete_small_to_mid_category_map().get(category_name, "")


def _get_current_subcategory(state: GiftRecommendationState) -> str:
    selected_subcategory = str(getattr(state, "selected_subcategory", "") or "")
    if selected_subcategory:
        return selected_subcategory

    selected_category = getattr(state, "selected_category", None)
    category_name = str(getattr(selected_category, "category_name", "") or "")
    if category_name in _get_complete_small_to_mid_category_map():
        return category_name
    return ""


def _resolve_explicit_category_reference(user_text: str) -> Tuple[str, str]:
    text = (user_text or "").strip()
    if not text:
        return "", ""

    matched_subcategory_keys = [
        keyword for keyword in subcategory_keyword_map.keys()
        if keyword and keyword in text
    ]
    if matched_subcategory_keys:
        matched_subcategory_keys.sort(key=len, reverse=True)
        subcategory = subcategory_keyword_map[matched_subcategory_keys[0]]
        mid_category = _get_complete_small_to_mid_category_map().get(subcategory, "")
        if mid_category:
            return subcategory, mid_category

    matched_mid_categories = [
        mid_category for mid_category in _get_complete_mid_to_big_category_map().keys()
        if mid_category and mid_category in text
    ]
    if matched_mid_categories:
        matched_mid_categories.sort(key=len, reverse=True)
        return "", matched_mid_categories[0]

    return "", ""


def _has_explicit_category_selection_intent(user_text: str) -> bool:
    explicit_subcategory, explicit_mid_category = _resolve_explicit_category_reference(user_text)
    if not explicit_mid_category:
        return False
    explicit_name = explicit_subcategory or explicit_mid_category
    return not _is_explicit_category_rejected(user_text, explicit_name)


def _is_explicit_category_rejected(user_text: str, category_name: str) -> bool:
    text = (user_text or "").strip()
    category_name = (category_name or "").strip()
    if not text or not category_name:
        return False

    escaped_category = re.escape(category_name)
    reject_patterns = [
        rf"{escaped_category}.{{0,8}}(不合适|不太合适|不喜欢|不要|不想要|不看|别看|换掉|算了)",
        rf"(不要|不想要|不看|别看).{{0,8}}{escaped_category}",
    ]
    return any(re.search(pattern, text) for pattern in reject_patterns)


def _has_reject_pending_categories_intent(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    return any(keyword in text for keyword in _REJECT_PENDING_CATEGORY_KEYWORDS)


def _run_category_flow(
    state: GiftRecommendationState,
    user_text: str,
    excluded_mid_categories: Optional[List[str]] = None,
    excluded_subcategories: Optional[List[str]] = None,
    slots_already_extracted: bool = False,
) -> Tuple[str, List[str], CategorySelectionResult]:
    if not slots_already_extracted:
        feature_extraction(state, user_text)
    _reapply_current_turn_taboo_clearance(state)
    category_group_result = _try_category_group_recommendation(state, user_text)
    if category_group_result is not None:
        return category_group_result

    selection_result = category_selection(
        state,
        excluded_mid_categories=excluded_mid_categories,
        excluded_subcategories=excluded_subcategories,
    )

    if selection_result.result_type == "need_more_info":
        return (
            "need_more_info",
            ["暂时还无法确定品类，请再补充一下收礼对象、预算、场景或偏好。"],
            selection_result,
        )

    if selection_result.result_type == "recommend_list":
        if _should_enter_detail_strategy_from_recommend_list(user_text):
            selected_category = selection_result.recommended_categories[0]
            apply_mid_category_selection(
                state,
                selected_category,
                selection_reason=selection_result.selection_reason,
                description=f"用户表达动态追问/直推意愿，自动选择最匹配中类：{selected_category}",
            )
            return (*_ask_for_details(state), selection_result)

        _ensure_category_sample_products(
            state,
            selection_result.recommended_categories,
            rejected_subcategories=excluded_subcategories,
        )
        return (
            "choose_category",
            [
                _build_category_choice_message(
                    selection_result.recommended_categories,
                    selection_reason=selection_result.selection_reason,
                    sample_products=state.filtered_products,
                )
            ],
            selection_result,
        )

    if selection_result.result_type in ("direct_subcategory", "direct_mid_category"):
        if _should_enter_detail_strategy_before_direct_product(user_text):
            return (*_ask_for_details(state), selection_result)

        product_filtering(state, user_text)
        if state.filtered_products:
            return "done", [_format_recommendation_text(state)], selection_result

    stage, contents = _ask_for_details(state)
    return stage, contents, selection_result


def _reapply_current_turn_taboo_clearance(state: GiftRecommendationState) -> None:
    positive_mid_categories = getattr(state, "_cleared_taboo_mid_categories", []) or []
    positive_subcategories = getattr(state, "_cleared_taboo_subcategories", []) or []
    if positive_mid_categories or positive_subcategories:
        _remove_categories_from_taboo(
            state,
            positive_mid_categories,
            positive_subcategories,
        )


def _should_enter_detail_strategy_from_recommend_list(user_text: str) -> bool:
    text = user_text or ""
    return _has_direct_recommend_intent(text)


def _should_enter_detail_strategy_before_direct_product(user_text: str) -> bool:
    text = user_text or ""
    return True


def _has_brief_only_intent(text: str) -> bool:
    return any(keyword in text for keyword in _BRIEF_ONLY_KEYWORDS)


def _has_direct_recommend_intent(text: str) -> bool:
    compact = (text or "").replace("，", "").replace(",", "").replace("。", "").strip()
    return (
        any(keyword in text for keyword in _DIRECT_RECOMMEND_KEYWORDS)
        or compact in {"随便", "都行", "无所谓", "都可以"}
    )


def _has_high_detail_intent(text: str) -> bool:
    signal_count = sum(1 for keyword in _HIGH_DETAIL_KEYWORDS if keyword in (text or ""))
    return signal_count >= 3 or (len(text or "") >= 20 and signal_count >= 2)


def _pending_from_selection(selection_result: Optional[CategorySelectionResult]) -> Tuple[List[str], str]:
    if selection_result and selection_result.result_type == "recommend_list":
        return list(selection_result.recommended_categories), selection_result.selection_reason
    return [], ""


def _ask_for_details(
    state: GiftRecommendationState,
    detail_answer: str = "",
) -> Tuple[str, List[str]]:
    detail_plan = prepare_detailed_dimensions(state)
    if not detail_plan:
        detailed_dimensions(state)
        return "detail_answer", ["补充更多送礼细节能帮助小Q推荐更精准。"]

    detail_payload = detail_plan.get("payload", {}) or {}
    should_direct_recommend = bool(detail_payload.get("should_direct_recommend"))
    filter_detail_answer = (
        _build_direct_recommend_detail_answer(state, detail_answer)
        if should_direct_recommend
        else _build_initial_recommend_detail_answer(state, detail_answer)
    )
    should_parallelize = (
        not detail_plan.get("reuse_existing")
        and not should_direct_recommend
        and bool(detail_plan.get("slots_to_ask"))
    )

    if should_parallelize:
        follow_up_state = copy.deepcopy(state)
        with ThreadPoolExecutor(max_workers=2) as executor:
            follow_up_future = submit_with_llm_trace(
                executor,
                generate_detailed_dimensions_message,
                follow_up_state,
                detail_plan,
                group="detail_and_product_filter.follow_up",
            )
            filtering_future = submit_with_llm_trace(
                executor,
                product_filtering,
                state,
                filter_detail_answer,
                group="detail_and_product_filter.product_filtering",
            )
            combined_message = follow_up_future.result()
            filtering_future.result()
    else:
        combined_message = generate_detailed_dimensions_message(state, detail_plan)
        product_filtering(state, filter_detail_answer)

    apply_detailed_dimensions_plan(state, detail_plan, combined_message)
    state.detailed_dimensions.pop("post_recommend_follow_up", None)
    if state.detailed_dimensions.get("should_direct_recommend"):
        direct_message = state.detailed_dimensions.get("combined_message", "")
        if not state.filtered_products:
            fallback_message = _build_direct_recommend_empty_result_prompt(state)
            contents = [content for content in [direct_message, fallback_message] if content]
            return "detail_answer", contents

        recommendation_text = _format_recommendation_text(state)
        contents = [content for content in [direct_message, recommendation_text] if content]
        return "done", contents

    follow_up = state.detailed_dimensions.get("combined_message", "")
    if state.filtered_products:
        if follow_up.strip() and state.detailed_dimensions.get("slots_asked"):
            state.detailed_dimensions["post_recommend_follow_up"] = follow_up.strip()
        recommendation_text = _format_recommendation_text(state)
        return "done", [recommendation_text]

    message = follow_up.strip() or "补充更多送礼细节能帮助小Q推荐更精准。"
    return "detail_answer", [message]


def _build_direct_recommend_detail_answer(
    state: GiftRecommendationState,
    detail_answer: str = "",
) -> str:
    user_messages = [
        str(message.get("content", "")).strip()
        for message in getattr(state, "chat_history", [])[-6:]
        if message.get("role") == "user" and message.get("content")
    ]
    selected_category = getattr(getattr(state, "selected_category", None), "category_name", "")
    parts = []
    if selected_category:
        parts.append(f"用户要求直接推荐，当前品类：{selected_category}")
    current_detail = (detail_answer or "").strip()
    if current_detail:
        parts.append(f"本轮补充需求：{current_detail}")
    if user_messages:
        parts.append("最近用户需求：" + "；".join(user_messages[-3:]))
    return "。".join(parts)


def _build_initial_recommend_detail_answer(
    state: GiftRecommendationState,
    detail_answer: str = "",
) -> str:
    user_messages = [
        str(message.get("content", "")).strip()
        for message in getattr(state, "chat_history", [])[-6:]
        if message.get("role") == "user" and message.get("content")
    ]
    selected_category = getattr(getattr(state, "selected_category", None), "category_name", "")
    parts = []
    if selected_category:
        parts.append(f"当前已选品类：{selected_category}")
    current_detail = (detail_answer or "").strip()
    if current_detail:
        parts.append(f"本轮补充需求：{current_detail}")
    if user_messages:
        parts.append("最近用户需求：" + "；".join(user_messages[-3:]))
    return "。".join(parts)


def _build_direct_recommend_empty_result_prompt(state: GiftRecommendationState) -> str:
    slots_asked = state.detailed_dimensions.get("slots_asked", [])
    if "budget_max" in slots_asked or "budget_min" in slots_asked:
        return "我先按当前信息筛了一下，暂时没有特别合适的。您补充一个大概预算，我马上缩小范围。"
    if "taboo" in slots_asked:
        return "我先按当前信息筛了一下，暂时没有特别合适的。您补充一下有没有禁忌、过敏或需要避开的类型，我再帮您筛。"
    if "recipient_preferences" in slots_asked:
        return "我先按当前信息筛了一下，暂时没有特别合适的。您补充一个偏好方向，我马上重新筛。"
    return "我先按当前信息筛了一下，暂时没有特别合适的。您补充一个预算或偏好，我马上重新筛。"


def _format_recommendation_text(state: GiftRecommendationState) -> str:
    if not state.filtered_products:
        return "已根据您的需求完成筛选，但暂未找到合适商品。您可以补充更多偏好或预算信息。"

    recommendations = format_recommendations(state.filtered_products)
    if recommendations.strip():
        if getattr(state, "downgrade_retry_triggered", False) and state.downgrade_retry_reason:
            return state.downgrade_retry_reason + "\n\n" + recommendations
        return _build_customer_recommendation_intro(state)
    return "已根据您的需求完成筛选，但暂未找到合适商品。您可以补充更多偏好或预算信息。"


def _build_customer_recommendation_intro(state: GiftRecommendationState) -> str:
    if _needs_cautious_recommendation_copy(state):
        return "我先按您提到的需求筛了几款，建议下单前再确认成分、适用说明和禁忌信息。"

    budget_text = _build_budget_copy(state)
    if budget_text:
        return f"我先按{budget_text}和当前偏好筛了几款，您可以先看看。"

    return "我先按当前需求筛了几款，整体更偏实用和送礼稳妥，您可以先看看。"


def _needs_cautious_recommendation_copy(state: GiftRecommendationState) -> bool:
    selected_category = getattr(state, "selected_category", None)
    category_text = " ".join(
        str(value or "")
        for value in [
            getattr(selected_category, "category_id", ""),
            getattr(selected_category, "category_name", ""),
            getattr(state, "selected_mid_category", ""),
            getattr(state, "selected_subcategory", ""),
            getattr(state, "selected_big_category", ""),
        ]
    )
    recent_user_text = " ".join(
        str(message.get("content", ""))
        for message in getattr(state, "chat_history", [])[-6:]
        if message.get("role") == "user"
    )
    text = category_text + " " + recent_user_text
    cautious_keywords = (
        "护肤",
        "营养",
        "保健",
        "滋补",
        "食品",
        "孕妇",
        "儿童",
        "母婴",
        "敏感肌",
        "过敏",
        "禁忌",
    )
    return any(keyword in text for keyword in cautious_keywords)


def _build_budget_copy(state: GiftRecommendationState) -> str:
    budget_min = _get_slot_value(state, "budget_min")
    budget_max = _get_slot_value(state, "budget_max")

    if budget_min is not None and budget_max is not None:
        return f"{_format_budget_value(budget_min)}-{_format_budget_value(budget_max)}元预算"
    if budget_max is not None:
        return f"{_format_budget_value(budget_max)}元以内预算"
    if budget_min is not None:
        return f"{_format_budget_value(budget_min)}元以上预算"
    return ""


def _get_slot_value(state: GiftRecommendationState, slot_name: str) -> Optional[Any]:
    slot = getattr(state, "filled_slots", {}).get(slot_name)
    if not slot or not getattr(slot, "is_filled", False):
        return None
    return getattr(slot, "value", None)


def _format_budget_value(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _filter_by_gender(products: List[Any], gender: str) -> List[Any]:
    """根据收礼人性别过滤不匹配的商品，如女士收礼人排除男士护理类商品。"""
    if not products or not gender:
        return products
    gender_lower = gender.strip().lower()
    if "女" in gender_lower:
        exclude = ("男士", "男用", "男款")
        return [p for p in products if not any(
            kw in product_search_text(p) for kw in exclude
        )]
    if "男" in gender_lower:
        exclude = ("女士", "女用", "女款", "女性")
        return [p for p in products if not any(
            kw in product_search_text(p) for kw in exclude
        )]
    return products


def _ensure_category_sample_products(
    state: GiftRecommendationState,
    mid_categories: List[str],
    rejected_subcategories: Optional[List[str]] = None,
) -> None:
    """为每个候选中类各选一个代表性商品，帮助用户在品类间做选择。"""
    catalog = _load_products_from_csv()
    if not catalog or not mid_categories:
        return

    budget_min = _coerce_slot_float(state, "budget_min")
    budget_max = _coerce_slot_float(state, "budget_max")
    recipient_gender_raw = _get_slot_value(state, "recipient_gender")
    recipient_gender = str(recipient_gender_raw).strip() if recipient_gender_raw else ""
    result: List[Any] = []
    rejected_subcategory_set = set(_normalize_category_list(rejected_subcategories or []))
    rejected_product_id_set = set(
        _normalize_product_id_list(getattr(state, "rejected_product_ids", []) or [])
    )
    taboo_keywords = _tokenize_light(str(_get_slot_value(state, "taboo") or ""))
    for cat in mid_categories:
        if len(result) >= 3:
            break
        cat_products = [
            p for p in catalog
            if str(getattr(p, "mid_category", "") or "") == cat
            and str(getattr(p, "sku_id", "") or "").strip() not in rejected_product_id_set
            and str(getattr(p, "small_category", "") or "") not in rejected_subcategory_set
            and not (
                taboo_keywords
                and any(keyword in product_search_text(p).lower() for keyword in taboo_keywords)
            )
        ]
        before_gender = len(cat_products)
        if recipient_gender:
            cat_products = _filter_by_gender(cat_products, recipient_gender)
        if not cat_products:
            continue
        result.append(_pick_category_sample_product(cat_products, budget_min, budget_max))
    state.filtered_products = result


def _pick_category_sample_product(
    products: List[Any],
    budget_min: Optional[float],
    budget_max: Optional[float],
) -> Any:
    target_price = _get_budget_target_price(budget_min, budget_max)

    if budget_min is not None or budget_max is not None:
        within_budget = [
            product for product in products
            if (budget_min is None or product.price >= budget_min)
            and (budget_max is None or product.price <= budget_max)
        ]
        if within_budget:
            return _pick_closest_to_target(within_budget, target_price)

        if target_price is not None:
            return _pick_closest_to_target(products, target_price)

    sorted_products = sorted(products, key=lambda product: product.price)
    return sorted_products[len(sorted_products) // 2]


def _get_budget_target_price(
    budget_min: Optional[float],
    budget_max: Optional[float],
) -> Optional[float]:
    if budget_min is not None and budget_max is not None:
        return (budget_min + budget_max) / 2
    if budget_max is not None:
        return budget_max
    if budget_min is not None:
        return budget_min
    return None


def _pick_closest_to_target(products: List[Any], target_price: Optional[float]) -> Any:
    if target_price is None:
        sorted_products = sorted(products, key=lambda product: product.price)
        return sorted_products[len(sorted_products) // 2]
    return min(products, key=lambda product: (abs(product.price - target_price), product.price))


def _has_current_turn_slot_update(state: GiftRecommendationState) -> bool:
    updates = getattr(state, "current_turn_slot_updates", None)
    if isinstance(updates, dict):
        if updates.get("has_actual_slot_updates"):
            return True
        applied = updates.get("applied")
        if isinstance(applied, dict) and bool(applied):
            return True
        budget_update = updates.get("budget_update")
        if isinstance(budget_update, dict) and budget_update.get("mentioned"):
            return True

    turn_understanding = getattr(state, "turn_understanding", None)
    intents = turn_understanding.get("intents", []) if isinstance(turn_understanding, dict) else []
    if isinstance(intents, list):
        return any(
            isinstance(intent, dict)
            and intent.get("type") in {"slot_update", "budget_update"}
            for intent in intents
        )
    return False


def _prefer_unexposed_products_for_slot_update(
    state: GiftRecommendationState,
    products: List[Any],
) -> List[Any]:
    if not _has_current_turn_slot_update(state):
        return list(products or [])
    avoid_ids = {
        str(product_id or "").strip()
        for product_id in (getattr(state, "last_recommended_product_ids", []) or [])
        if str(product_id or "").strip()
    }
    if not avoid_ids:
        return list(products or [])

    fresh_products: List[Any] = []
    exposed_products: List[Any] = []
    for product in products or []:
        product_id = str(getattr(product, "sku_id", "") or "").strip()
        if product_id and product_id in avoid_ids:
            exposed_products.append(product)
        else:
            fresh_products.append(product)
    return fresh_products + exposed_products


def _append_local_fill_products(
    state: GiftRecommendationState,
    products: List[Any],
    used_product_ids: set,
    target_price: Optional[float],
    budget_min: Optional[float],
    budget_max: Optional[float],
    user_text: str,
) -> None:
    if len(state.filtered_products) >= 3 or not products:
        return

    filtered = _filter_rejected_products(state, products)
    filtered = _apply_explicit_hard_constraints(filtered, user_text or "")
    rejected_subcategory_set = set(
        _normalize_category_list(getattr(state, "rejected_subcategories", []) or [])
    )
    if rejected_subcategory_set:
        filtered = [
            product for product in filtered
            if str(getattr(product, "small_category", "") or "") not in rejected_subcategory_set
        ]

    taboo_keywords = _tokenize_light(str(_get_slot_value(state, "taboo") or ""))
    if taboo_keywords:
        filtered = [
            product for product in filtered
            if not any(keyword in product_search_text(product).lower() for keyword in taboo_keywords)
        ]

    recipient_gender_raw = _get_slot_value(state, "recipient_gender")
    recipient_gender = str(recipient_gender_raw).strip() if recipient_gender_raw else ""
    if recipient_gender:
        filtered = _filter_by_gender(filtered, recipient_gender)

    sorted_products = sorted(
        filtered,
        key=lambda product: (
            0 if (
                (budget_min is None or product.price >= budget_min)
                and (budget_max is None or product.price <= budget_max)
            ) else 1,
            abs(product.price - target_price) if target_price is not None else 0,
            product.price,
        ),
    )
    for product in _prefer_unexposed_products_for_slot_update(state, sorted_products):
        product_id = str(getattr(product, "sku_id", "") or "").strip()
        if not product_id or product_id in used_product_ids:
            continue
        state.filtered_products.append(product)
        used_product_ids.add(product_id)
        if len(state.filtered_products) >= 3:
            return


def _fill_three_products_locally(
    state: GiftRecommendationState,
    user_text: str,
) -> None:
    record_product_filter_local_fill(state)
    budget_min = _coerce_slot_float(state, "budget_min")
    budget_max = _coerce_slot_float(state, "budget_max")
    target_price = _get_budget_target_price(budget_min, budget_max)
    state.filtered_products = _filter_rejected_products(state, state.filtered_products)
    used_product_ids = set(_product_ids_from_products(state.filtered_products))

    candidate_products = list(getattr(state, "candidate_products", []) or [])
    _append_local_fill_products(
        state,
        candidate_products,
        used_product_ids,
        target_price,
        budget_min,
        budget_max,
        user_text,
    )
    if len(state.filtered_products) >= 3:
        return

    catalog = _load_products_from_csv()
    if not catalog:
        return

    current_subcategory = _get_current_subcategory(state)
    current_mid_category = _get_current_mid_category(state)

    if current_subcategory:
        same_small = [
            product for product in catalog
            if str(getattr(product, "small_category", "") or "") == current_subcategory
            and (
                not current_mid_category
                or str(getattr(product, "mid_category", "") or "") == current_mid_category
            )
        ]
        _append_local_fill_products(
            state,
            same_small,
            used_product_ids,
            target_price,
            budget_min,
            budget_max,
            user_text,
        )

    if len(state.filtered_products) >= 3:
        return

    if current_mid_category:
        same_mid = [
            product for product in catalog
            if str(getattr(product, "mid_category", "") or "") == current_mid_category
        ]
        _append_local_fill_products(
            state,
            same_mid,
            used_product_ids,
            target_price,
            budget_min,
            budget_max,
            user_text,
        )


def _ensure_three_products(
    state: GiftRecommendationState,
    user_text: str,
) -> None:
    """确保 state.filtered_products 至少有 3 个推荐商品；不足时只做本地补足。"""
    state.filtered_products = _prefer_unexposed_products_for_slot_update(
        state,
        _filter_rejected_products(state, state.filtered_products),
    )
    if len(state.filtered_products) >= 3:
        state.filtered_products = state.filtered_products[:3]
        return

    _fill_three_products_locally(state, user_text or "")
    state.filtered_products = _prefer_unexposed_products_for_slot_update(
        state,
        _filter_rejected_products(state, state.filtered_products),
    )
    if len(state.filtered_products) > 3:
        state.filtered_products = state.filtered_products[:3]


def _build_recommendation_blocks(
    state: GiftRecommendationState,
    contents: List[str],
) -> List[Dict[str, str]]:
    post_follow_up = str(
        state.detailed_dimensions.get("post_recommend_follow_up", "") or ""
    ).strip()
    blocks = _build_text_blocks(contents)
    product_cards = format_recommendation_cards(state.filtered_products)
    if product_cards:
        _record_recommended_products(state, state.filtered_products[:len(product_cards)])
        blocks.append(_make_json_block({"type": "pro-recommend", "data": product_cards}))

        if post_follow_up:
            blocks.extend(_build_text_blocks([post_follow_up]))

        top_name = product_cards[0].get("productName", "这款商品")
        question_1 = random.choice(SAFE_QUESTION_POOL).format(name=top_name)
        blocks.append(
            _make_json_block(
                {
                    "type": "add-questions",
                    "title": "您可能还想问",
                    "data": [
                        {"title": question_1},
                        {"title": "还有其他推荐吗？"},
                    ],
                }
            )
        )
    return blocks


def _build_text_blocks(contents: List[str]) -> List[Dict[str, str]]:
    return [{"type": "text", "content": content} for content in contents if content]


def _make_json_block(payload: Dict[str, Any]) -> Dict[str, str]:
    return {
        "type": "json",
        "content": f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```",
    }
