import copy
import csv
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from .category_catalog import (
    get_complete_mid_to_big_category_map,
    get_complete_small_to_mid_category_map,
    get_mid_to_small_category_map,
)
from .models import GiftRecommendationState, ProductCandidate
from .llm_client import call_json
from ..llm_trace import submit_with_llm_trace

MIN_BUDGET_MATCHED_RESULTS = 3
SOFT_BUDGET_KEYWORD_WEIGHT = 10.0
SOFT_BUDGET_IN_RANGE_BONUS = 5.0
SOFT_BUDGET_PRICE_WEIGHT = 2.0
PRODUCT_FILTER_LLM_STATS_ATTR = "_product_filter_llm_stats"
PRODUCT_FILTER_CACHE_ATTR = "_product_filter_llm_cache"
KEYWORD_SLOT_NAMES = [
    "recipient_preferences",
    "occasion",
    "recipient_relation",
    "recipient_gender",
    "recipient_age",
]


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


def _current_turn_exposure_avoid_ids(state: GiftRecommendationState) -> set:
    if not _has_current_turn_slot_update(state):
        return set()
    return {
        str(product_id or "").strip()
        for product_id in (getattr(state, "last_recommended_product_ids", []) or [])
        if str(product_id or "").strip()
    }


def _prefer_unexposed_products_for_slot_update(
    state: GiftRecommendationState,
    products: List[ProductCandidate],
) -> List[ProductCandidate]:
    avoid_ids = _current_turn_exposure_avoid_ids(state)
    if not avoid_ids or not products:
        return list(products or [])

    fresh_products: List[ProductCandidate] = []
    exposed_products: List[ProductCandidate] = []
    for product in products:
        sku_id = str(getattr(product, "sku_id", "") or "").strip()
        if sku_id and sku_id in avoid_ids:
            exposed_products.append(product)
        else:
            fresh_products.append(product)
    return fresh_products + exposed_products


# ---------------------------
# LLM keyword extraction prompt
# ---------------------------
KEYWORD_EXTRACTION_PROMPT = """
你是电商送礼助手。请从用户文本中抽取"用于商品检索/匹配"的关键词或短语。

要求：
- 只输出 JSON（调用方使用 response_format=json_object）
- 输出格式固定为：{"keywords": ["...","..."]}
- keywords 为字符串数组，数量 5~20 个（尽量给出有区分度的词/短语）
- 优先抽取：品类/用途/场景/风格/品牌/材质/功效/人群特征/价格区间/颜色/尺寸/兴趣爱好/禁忌等
- 过滤无意义泛词：喜欢、想要、送礼、东西、礼物、不错、适合、一下、有没有 等
- 保留可匹配短语（2~12 字符优先），例如：香薰蜡烛、敏感肌、露营、通勤、机械键盘、保温杯、轻奢风、无香精、护手霜
""".strip()

KEYWORD_SYNONYM_EXTRACTION_PROMPT = """
你是电商送礼助手。请从用户文本中抽取"用于商品检索/匹配"的关键词或短语，并为每个关键词生成0-3个最相关的同义词或近义词。

要求：
- 只输出 JSON（调用方使用 response_format=json_object）
- 输出格式固定为：{"keywords": ["...","..."], "synonyms": {"keyword1": ["synonym1", "synonym2"], "keyword2": []}}
- keywords 为字符串数组，数量 5~20 个（尽量给出有区分度的词/短语）
- 优先抽取：品类/用途/场景/风格/品牌/材质/功效/人群特征/价格区间/颜色/尺寸/兴趣爱好/禁忌等
- 过滤无意义泛词：喜欢、想要、送礼、东西、礼物、不错、适合、一下、有没有 等
- 保留可匹配短语（2~12 字符优先），例如：香薰蜡烛、敏感肌、露营、通勤、机械键盘、保温杯、轻奢风、无香精、护手霜
- 每个关键词生成0-3个同义词，没有合适同义词的可为空数组
- 同义词应保持语义相近且适合商品检索场景
- 避免生成过于宽泛或无关的同义词

示例：
输入文本：送妈妈敏感肌能用的护肤品，预算500，温和一点
输出：{"keywords": ["妈妈", "敏感肌", "护肤品", "500元", "温和"], "synonyms": {"敏感肌": ["敏感皮肤", "易过敏肌肤"], "温和": ["舒缓", "低刺激"], "护肤品": ["面部护肤"]}}
""".strip()


# ---------------------------
# LLM synonym generation prompt
# ---------------------------
SYNONYM_GENERATION_PROMPT = """
你是电商语义理解专家。请为每个关键词生成0-3个最相关的同义词或近义词。

要求：
- 只输出 JSON（调用方使用 response_format=json_object）
- 输出格式固定为：{"synonyms": {"keyword1": ["synonym1", "synonym2"], "keyword2": ["synonym3"]}}
- 每个关键词生成0-3个同义词，没有合适同义词的可为空数组
- 同义词应保持语义相近且适合商品检索场景
- 避免生成过于宽泛或无关的同义词

示例：
输入关键词：["香薰蜡烛", "敏感肌", "露营"]
输出：{"synonyms": {"香薰蜡烛": ["香氛蜡烛", "芳香蜡烛"], "敏感肌": ["敏感皮肤", "易过敏肌肤"], "露营": ["野营", "户外露营"]}}
""".strip()

# ---------------------------
# LLM rerank prompt
# ---------------------------
PRODUCT_RERANK_PROMPT = """
你是一个送礼选品助手，需要从候选商品中选出最适合用户需求的商品，并进行重新排序。

你会收到：
1. 用户历史对话
2. 用户已填写的需求槽位
3. 用户最新补充信息
4. 当前季节与季节适配排序规则
5. 候选商品列表（最多10个）

请综合考虑以下因素进行排序：
- 是否符合用户当前明确需求
- 是否符合送礼场景
- 是否符合收礼对象特征（年龄、性别、关系等）
- 是否符合偏好、禁忌、风格、用途、功效等要求
- 如果存在当前季节槽位，必须检查商品是否适合当前季节；明显符合当前季节的商品应优先靠前
- 明显反季节商品不要排入前三，除非用户明确指定该商品类型、收礼人所在地/使用场景支持该商品，或候选中没有更合适的当季商品
- 是否更适合作为礼物（体面、契合场景、实用性）
- 在信息不完整时，优先选择更稳妥、更通用但仍然相关的商品

要求：
- 只能在给定候选商品中排序，不能新增商品
- 返回最终排序后的 sku_id 列表
- 可同时给出简短理由；理由需简要说明是否考虑了当前季节，若反季节商品进入前三必须说明原因
- 只返回 JSON，不要输出额外内容

返回格式：
{
  "ranked_sku_ids": ["sku_1", "sku_2", "sku_3"],
  "reason": "排序综合考虑了用户场景、偏好和礼赠适配度。"
}
""".strip()


# ---------------------------
# 小类 -> 中类
# ---------------------------
SMALL_TO_MID_CATEGORY_MAP = {
    "餐具": "品质生活",
    "燕窝": "滋补贵细",
    "保温杯/焖烧罐": "品质生活",
    "男士护肤套装": "男士护肤",
    "白葡萄酒": "葡萄酒",
    "拉杆箱": "功能箱包",
    "蛋白粉（运动类）": "运动营养",
    "儿童其他护肤": "儿童护肤",
    "戒指": "黄金珠宝",
    "脸部彩妆套装": "面部彩妆",
    "其它面部护肤": "面部护肤",
    "身体护理套装": "身体护理",
    "代餐奶昔": "体重管理",
    "早教益智": "玩具",
    "沐浴露": "身体护理",
    "男士面部护理": "男士护肤",
    "茶具": "品质生活",
    "粉饼": "面部彩妆",
    "国产腕表": "腕表",
    "巧克力": "休闲食品",
    "手部护理": "身体护理",
    "洁面": "面部护肤",
    "时尚配饰套装": "时尚配饰",
    "礼袋": "礼盒礼袋",
    "牙刷": "口腔护理",
    "洗发沐浴": "儿童洗护用品",
    "地板清洁剂": "家庭清洁",
    "男士双肩包": "男包",
    "黄金": "黄金珠宝",
    "女士腰带": "服配",
    "卫生巾": "女性护理",
    "女士裤装（含中性）": "女装（含中性）",
    "定妆喷雾/水": "面部彩妆",
    "耳饰": "黄金珠宝",
    "维生素": "维生素/矿物质",
    "美容仪": "个护电器",
    "电动牙刷": "个护电器",
    "家纺": "品质生活",
    "花胶/鱼胶": "滋补贵细",
    "化妆棉": "美妆工具",
    "白酒": "国酒",
    "防晒": "面部护肤",
    "男士腰包/胸包": "男包",
    "散粉/蜜粉": "面部彩妆",
    "耳机耳麦": "影音娱乐",
    "食用油/调味油": "粮油调味速食",
    "手镯/手链": "黄金珠宝",
    "假睫毛": "美妆工具",
    "儿童家纺": "儿童家纺",
    "坚果": "休闲食品",
    "方便速食/速冻食品": "粮油调味速食",
    "阿胶": "滋补贵细",
    "眼线": "面部彩妆",
    "眼膜": "面部护肤",
    "遥控/电动/模型玩具": "玩具",
    "女士单鞋": "女鞋",
    "牙膏": "口腔护理",
    "男士洁面": "男士护肤",
    "口喷": "口腔护理",
    "男士单鞋": "男鞋",
    "眉笔/眉粉": "面部彩妆",
    "益生菌": "其他营养健康",
    "麦片": "咖啡冲饮",
    "唇笔/唇线笔": "面部彩妆",
    "笔": "文具",
    "吊坠": "黄金珠宝",
    "气垫": "面部彩妆",
    "纤体塑形": "体重管理",
    "女士双肩包（含中性双肩包）": "女包（含中性）",
    "拼接积木（乐高/木质）": "玩具",
    "糕点（非月饼）": "休闲食品",
    "妆前乳/隔离": "面部彩妆",
    "饼干": "休闲食品",
    "车载用品": "旅行用品",
    "唇彩/唇蜜/唇釉": "面部彩妆",
    "手机": "手机通讯",
    "发饰": "服配",
    "遮瑕": "面部彩妆",
    "女士外套（含中性）": "女装（含中性）",
    "野山参": "参茸制品",
    "乳液面霜": "面部护肤",
    "太阳镜": "眼镜",
    "起泡酒": "葡萄酒",
    "剃须护理": "男士护肤",
    "氨糖/软骨素": "骨骼健康",
    "威士忌/Whiskey": "洋酒",
    "卷/直发器": "个护电器",
    "其他玩具（户外玩具/黏土/水上玩具）": "玩具",
    "女士单肩包/斜挎包（含中性单肩包）": "女包（含中性）",
    "沐浴香皂": "身体护理",
    "瑞士腕表": "腕表",
    "男士外套": "男装",
    "日常护理": "宠物医疗保健",
    "洗衣液": "家庭清洁",
    "BB霜/CC霜": "面部彩妆",
    "其他体重管理": "体重管理",
    "口罩": "身体护理",
    "润肤油": "身体护理",
    "玩具": "玩具",
    "腮红": "面部彩妆",
    "白茶": "茗茶",
    "男士单肩包/斜挎包": "男包",
    "大闸蟹": "海鲜水产",
    "女士T恤": "女装（含中性）",
    "香水套装": "香水香氛",
    "儿童面霜": "儿童护肤",
    "面部护理套装": "面部护肤",
    "其它身体护理": "身体护理",
    "围巾/披肩/丝巾": "服配",
    "摆件/挂饰": "品质生活",
    "驱蚊驱虫": "家庭清洁",
    "剃/脱毛器": "个护电器",
    "乌龙茶": "茗茶",
    "儿童餐具": "婴儿喂养用品",
    "粉底液/霜": "面部彩妆",
    "护发": "美发护发",
    "眼霜/眼部精华": "面部护肤",
    "修容": "面部彩妆",
    "胸针": "服配",
    "爽肤水/化妆水": "面部护肤",
    "市场推广商品": "推广商品",
    "电吹风": "个护电器",
    "猫/狗保健品": "宠物医疗保健",
    "男士休闲鞋": "男鞋",
    "儿童太阳镜": "眼镜",
    "精华": "面部护肤",
    "其他美妆工具": "美妆工具",
    "洗手液": "家庭清洁",
    "欧美腕表": "腕表",
    "眼影": "面部彩妆",
    "袜子": "服配",
    "家居香氛": "香水香氛",
    "咖啡机": "厨房小电",
    "酒杯": "品质生活",
    "洗发": "美发护发",
    "其他健康理疗": "健康理疗",
    "香水": "香水香氛",
    "奶瓶奶嘴": "婴儿喂养用品",
    "高光": "面部彩妆",
    "女士手提包（仅可手提不可肩挎）": "女包（含中性）",
    "日韩腕表": "腕表",
    "眼罩": "旅行用品",
    "化妆刷": "美妆工具",
    "音响": "影音娱乐",
    "膳食纤维素": "体重管理",
    "蜂蜜/蜂类制品": "其他营养健康",
    "辅酶Q10": "其他营养健康",
    "光学眼镜": "眼镜",
    "梳妆用品": "品质生活",
    "红葡萄酒": "葡萄酒",
    "按摩仪器": "健康理疗",
    "其他滋补品": "其他滋补品",
    "油污净": "家庭清洁",
    "项链": "黄金珠宝",
    "睡裙": "内衣",
    "面膜": "面部护肤",
    "睫毛夹": "美妆工具",
    "粉扑": "美妆工具",
    "润肤乳": "身体护理",
    "口红": "面部彩妆",
    "唇膜/唇部精华": "面部护肤",
    "润唇膏": "面部护肤",
    "毛绒玩具": "玩具",
    "女士休闲鞋": "女鞋",
    "其他参茸制品": "参茸制品",
    "生活日用": "品质生活",
    "帽子/手套": "服配",
    "私处护理": "女性护理",
    "儿童沐浴": "儿童洗护用品",
    "睫毛膏/睫毛液": "面部彩妆",
    "卸妆": "面部彩妆",
    "其他滋补贵细": "滋补贵细",
}


COMPLETE_SMALL_TO_MID_CATEGORY_MAP = get_complete_small_to_mid_category_map(SMALL_TO_MID_CATEGORY_MAP)
COMPLETE_MID_TO_BIG_CATEGORY_MAP = get_complete_mid_to_big_category_map()
MID_TO_SMALL_CATEGORY_MAP: Dict[str, List[str]] = get_mid_to_small_category_map(
    COMPLETE_SMALL_TO_MID_CATEGORY_MAP
)


def _get_product_filter_cache(state: GiftRecommendationState) -> Dict[str, Dict[str, object]]:
    cache = getattr(state, PRODUCT_FILTER_CACHE_ATTR, None)
    if not isinstance(cache, dict):
        cache = {
            "keyword_synonym": {},
            "small_category_infer": {},
            "rerank": {},
        }
        setattr(state, PRODUCT_FILTER_CACHE_ATTR, cache)
    for namespace in ("keyword_synonym", "small_category_infer", "rerank"):
        if not isinstance(cache.get(namespace), dict):
            cache[namespace] = {}
    return cache


def _get_product_filter_llm_stats(state: GiftRecommendationState) -> Dict[str, int]:
    stats = getattr(state, PRODUCT_FILTER_LLM_STATS_ATTR, None)
    if not isinstance(stats, dict):
        stats = {
            "keyword_synonym": 0,
            "small_category_infer": 0,
            "rerank": 0,
            "cache_hit": 0,
            "local_fill": 0,
        }
        setattr(state, PRODUCT_FILTER_LLM_STATS_ATTR, stats)
    for key in ("keyword_synonym", "small_category_infer", "rerank", "cache_hit", "local_fill"):
        if not isinstance(stats.get(key), int):
            stats[key] = 0
    return stats


def _record_product_filter_llm_call(state: GiftRecommendationState, call_type: str) -> None:
    stats = _get_product_filter_llm_stats(state)
    stats[call_type] = int(stats.get(call_type, 0)) + 1
    total = stats.get("keyword_synonym", 0) + stats.get("small_category_infer", 0) + stats.get("rerank", 0)
    print(f"[LLM调用计数] type={call_type}, total={total}, stats={stats}")


def _record_product_filter_cache_hit(state: GiftRecommendationState, cache_type: str) -> None:
    stats = _get_product_filter_llm_stats(state)
    stats["cache_hit"] = int(stats.get("cache_hit", 0)) + 1
    print(f"[LLM缓存命中] type={cache_type}, stats={stats}")


def record_product_filter_local_fill(state: GiftRecommendationState) -> None:
    stats = _get_product_filter_llm_stats(state)
    stats["local_fill"] = int(stats.get("local_fill", 0)) + 1
    print(f"[本地补足触发] stats={stats}")


def _clone_state_for_parallel_filter_task(
    state: GiftRecommendationState,
) -> GiftRecommendationState:
    task_state = copy.copy(state)
    setattr(
        task_state,
        PRODUCT_FILTER_CACHE_ATTR,
        copy.deepcopy(_get_product_filter_cache(state)),
    )
    setattr(
        task_state,
        PRODUCT_FILTER_LLM_STATS_ATTR,
        {
            "keyword_synonym": 0,
            "small_category_infer": 0,
            "rerank": 0,
            "cache_hit": 0,
            "local_fill": 0,
        },
    )
    return task_state


def _merge_parallel_filter_task_state(
    state: GiftRecommendationState,
    task_state: GiftRecommendationState,
) -> None:
    parent_cache = _get_product_filter_cache(state)
    task_cache = _get_product_filter_cache(task_state)
    for namespace in ("keyword_synonym", "small_category_infer", "rerank"):
        parent_cache[namespace].update(task_cache.get(namespace, {}))

    parent_stats = _get_product_filter_llm_stats(state)
    task_stats = _get_product_filter_llm_stats(task_state)
    for key in ("keyword_synonym", "small_category_infer", "rerank", "cache_hit", "local_fill"):
        parent_stats[key] = int(parent_stats.get(key, 0)) + int(task_stats.get(key, 0))

    if getattr(task_state, "hard_constraint_no_match", False):
        state.hard_constraint_no_match = True


def _build_slot_cache_key(state: GiftRecommendationState, slot_names: Optional[List[str]] = None) -> str:
    names = slot_names or KEYWORD_SLOT_NAMES
    parts: List[str] = []
    for slot_name in names:
        slot = getattr(state, "filled_slots", {}).get(slot_name)
        value = getattr(slot, "value", None) if slot else None
        if value is not None:
            parts.append(f"{slot_name}={value}")
    return "|".join(parts)


def _build_all_slot_cache_key(state: GiftRecommendationState) -> str:
    filled_slots = getattr(state, "filled_slots", {}) or {}
    parts: List[str] = []
    for slot_name in sorted(filled_slots.keys()):
        slot = filled_slots.get(slot_name)
        value = getattr(slot, "value", None) if slot else None
        if value is not None:
            parts.append(f"{slot_name}={value}")
    return "|".join(parts)


# ---------------------------
# LLM small-category inference prompt
# ---------------------------
SMALL_CATEGORY_INFERENCE_PROMPT = """
你是电商品类推断专家。请根据用户补充信息，在给定的候选小品类中选择最匹配的一个。

要求：
- 只能从给定候选小品类中选择
- 如果无法判断，则返回 null
- 只返回 JSON

返回格式：
{
  "inferred_small_category": "口红",
  "confidence": 0.9,
  "reason": "用户提到口红色号和唇妆需求"
}

如果无法判断：
{
  "inferred_small_category": null,
  "confidence": 0.0,
  "reason": "无法确定具体小品类"
}
""".strip()


MULTI_SMALL_CATEGORY_INFERENCE_PROMPT = """
你是电商送礼场景的小类推断专家。

你的任务是：根据用户送礼上下文、已填槽位、当前选中的商品中类，以及该中类下真实存在的小类列表，推断用户最可能需要的小类商品。

重要规则：
1. 只能从“候选小类”中选择，不能编造小类。
2. 最多选择 3 个小类，按匹配度从高到低排序。
3. 如果用户明确提到某个小类或其别称，优先选择该小类。
4. 如果用户只是选择了中类，但没有明确小类，要结合送礼关系、性别、场景、偏好、预算、禁忌判断。
5. 如果是送人场景，优先选择更适合作为礼物的小类。
6. 不要选择明显偏离用户场景的小类。
7. 如果无法可靠判断，返回空数组，并设置 fallback_to_mid_category=true。
8. confidence 范围是 0 到 1；低于 0.55 的小类不要返回。
9. 必须严格返回 JSON，不要输出 Markdown，不要输出解释性正文。
10. 如果提供了当前季节和季节小类选择规则，必须先判断候选小类是否符合当前季节。
11. 若存在符合当前季节的小类，只能从这些小类中选择，且可以少于 3 个；不要为了凑满 3 个而选择明显反季节小类。
12. 若你判断当前中类下没有任何符合当前季节的小类，则忽略季节限制，按原有送礼匹配规则选择最多 3 个小类。
13. reason 需要简要说明该小类是否符合当前季节；如果因为没有当季小类而回退原规则，也需要在 reason 中说明。

返回格式：
{
  "selected_small_categories": [
    {
      "small_category": "候选小类名称",
      "confidence": 0.0,
      "reason": "一句话说明为什么匹配"
    }
  ],
  "fallback_to_mid_category": false
}
""".strip()


def product_filtering(state: GiftRecommendationState, detail_answer: str) -> GiftRecommendationState:
    """
    选品逻辑：
    1) 先做 taboo / 拒绝类目约束，再做预算过滤
    2) 类目硬过滤：
       - 先尝试小品类（lvl_03）
       - 若无结果，再尝试中品类（lvl_02）
       - 若仍无结果，则不按类目过滤
    3) 预算内同品类商品不足 3 个时，在同品类候选中按价格接近预算做软补足
    4) 再做关键词打分排序
    5) 取前10个候选，交给 LLM 做 rerank
    6) 若仍无结果且有选中品类，则降级重试：只保留品类，放弃预算和关键词限制
    """
    candidate_pool = list(getattr(state, "candidate_products", []) or [])
    force_full_catalog = bool(getattr(state, "force_full_catalog_on_next_filter", False))
    catalog = _load_products_from_csv() if force_full_catalog or not candidate_pool else candidate_pool
    if force_full_catalog:
        setattr(state, "force_full_catalog_on_next_filter", False)

    budget_min = state.filled_slots.get("budget_min").value if "budget_min" in state.filled_slots else None
    budget_max = state.filled_slots.get("budget_max").value if "budget_max" in state.filled_slots else None

    selected_small_category, selected_mid_category = _resolve_selected_categories(state, detail_answer)

    print(f"当前选中小品类: {selected_small_category}, 当前选中中品类: {selected_mid_category}")

    taboo_keywords = _extract_taboo_keywords(state)
    rejected_subcategories = _extract_rejected_subcategories(state)
    excluded_product_ids = _extract_excluded_product_ids(state)
    constraint_filtered_catalog = _apply_product_exclusions(
        catalog,
        taboo_keywords=taboo_keywords,
        rejected_subcategories=rejected_subcategories,
        excluded_product_ids=excluded_product_ids,
    )
    if not getattr(state, "hard_constraint_no_match", False):
        state.hard_constraint_no_match = False
    soft_budget_ranking_applied = False

    # 1) 基础过滤：先保留禁忌/拒绝类目等硬约束，再应用预算硬过滤
    print(f"[调试] 预算过滤前: 总商品数={len(catalog)}, budget_min={budget_min}, budget_max={budget_max}")
    base_filtered = _apply_budget_filter(
        constraint_filtered_catalog,
        budget_min=budget_min,
        budget_max=budget_max,
    )
    print(f"[调试] 预算过滤后: 剩余商品数={len(base_filtered)}")

    inferred_small_categories: List[str] = []
    should_infer_small_categories = (
        not selected_small_category
        and selected_mid_category
        and getattr(state, "category_level", "") == "mid_category"
    )
    if should_infer_small_categories:
        user_keywords, inferred_small_categories = _run_filter_understanding_parallel(
            state=state,
            detail_answer=detail_answer,
            selected_mid_category=selected_mid_category,
            constraint_filtered_catalog=constraint_filtered_catalog,
            base_filtered=base_filtered,
            budget_min=budget_min,
            budget_max=budget_max,
        )
        if inferred_small_categories:
            quota_products = _select_products_by_small_category_quota(
                state=state,
                detail_answer=detail_answer,
                base_products=base_filtered,
                fallback_catalog=constraint_filtered_catalog,
                selected_mid_category=selected_mid_category,
                selected_small_categories=inferred_small_categories,
                user_keywords=user_keywords,
            )
            if quota_products:
                state.filtered_products = quota_products
                state.candidate_products = quota_products[:50]
                setattr(state, "inferred_small_categories", inferred_small_categories)
                try:
                    state.final_product_cards = _build_product_cards(quota_products)
                except Exception as e:
                    print(f"构建商品卡片失败: {e}")
                    state.final_product_cards = []
                return state
    else:
        user_keywords = _extract_user_keywords(state, detail_answer)

    # 2) 类目过滤：先小类，失败再中类，再失败不过滤
    category_filtered = _apply_category_filter_with_fallback(
        base_filtered,
        selected_small_category=selected_small_category,
        selected_mid_category=selected_mid_category,
    )

    print(f"[调试] 类目过滤后: 剩余商品数={len(category_filtered)}")

    budget_matched_count = len(category_filtered)
    category_filtered_without_budget = _apply_category_filter_with_fallback(
        constraint_filtered_catalog,
        selected_small_category=selected_small_category,
        selected_mid_category=selected_mid_category,
    )

    hard_filtered = _apply_explicit_hard_constraints(category_filtered, detail_answer)
    hard_filtered_without_budget = _apply_explicit_hard_constraints(
        category_filtered_without_budget,
        detail_answer,
    )
    if category_filtered and not hard_filtered and not hard_filtered_without_budget:
        print("[硬约束无结果] 候选商品均不满足用户明确硬约束")
        state.hard_constraint_no_match = True
        state.filtered_products = []
        state.final_product_cards = []
        return state
    category_filtered = hard_filtered

    if _should_apply_soft_budget_ranking(
        budget_matched_count=budget_matched_count,
        unbudgeted_count=len(hard_filtered_without_budget),
        budget_min=budget_min,
        budget_max=budget_max,
    ):
        print(
            "[软预算排序触发] 预算范围内商品不足，"
            f"预算内={budget_matched_count}, 同品类候选={len(hard_filtered_without_budget)}"
        )
        category_filtered = _rank_by_keyword_and_soft_budget(
            hard_filtered_without_budget,
            user_keywords,
            budget_min=budget_min,
            budget_max=budget_max,
        )
        soft_budget_ranking_applied = True
        budget_info = _format_budget_info(budget_min, budget_max)
        state.downgrade_retry_triggered = True
        state.downgrade_retry_reason = (
            f"{budget_info}范围内可选商品较少，"
            "以下已为您补充价格接近预算的商品供参考："
        ) if budget_info else (
            "当前预算范围内可选商品较少，以下已为您补充价格接近预算的商品供参考："
        )

    # 如果预算限制导致类目过滤返回 0 结果，且无法软预算补足，则触发降级重试
    elif not category_filtered and (selected_small_category or selected_mid_category):
        print("[降级重试触发] 预算限制导致类目过滤无结果，立即放弃预算限制，仅按品类重新搜索")
        category_filtered = hard_filtered_without_budget
        print(f"[降级重试结果] 不限预算搜索得到 {len(category_filtered)} 条商品")
        category_name = selected_small_category or selected_mid_category or ""
        budget_info = _format_budget_info(budget_min, budget_max)
        state.downgrade_retry_triggered = True
        state.downgrade_retry_reason = (
            f"{budget_info}范围内暂未找到「{category_name}」品类的商品，"
            f"以下为您放宽预算后匹配到的「{category_name}」商品："
        ) if budget_info else (
            f"在您的预算范围内暂未找到「{category_name}」品类的商品，"
            f"以下为您放宽预算后匹配到的「{category_name}」商品："
        )

    # 3) 关键词计分 + 排序
    ranked = (
        category_filtered
        if soft_budget_ranking_applied
        else _rank_by_keyword_score(category_filtered, user_keywords)
    )

    # 4) 取前10个，让 LLM rerank
    top_candidates = ranked[:10]
    reranked = _rerank_products_with_llm(state, detail_answer, top_candidates)

    final_products = [] if getattr(state, "hard_constraint_no_match", False) else (reranked if reranked else ranked)
    final_products = _prefer_unexposed_products_for_slot_update(state, final_products)

    state.filtered_products = final_products
    if final_products:
        state.candidate_products = final_products[:50]

    # 新增：输出前端需要的商品结构
    try:
        state.final_product_cards = _build_product_cards(final_products)
    except Exception as e:
        print(f"构建商品卡片失败: {e}")
        state.final_product_cards = []

    return state


def _run_filter_understanding_parallel(
    state: GiftRecommendationState,
    detail_answer: str,
    selected_mid_category: str,
    constraint_filtered_catalog: List[ProductCandidate],
    base_filtered: List[ProductCandidate],
    budget_min: Optional[float],
    budget_max: Optional[float],
) -> Tuple[List[str], List[str]]:
    keyword_state = _clone_state_for_parallel_filter_task(state)
    small_category_state = _clone_state_for_parallel_filter_task(state)
    user_keywords: List[str] = []
    inferred_small_categories: List[str] = []

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            submit_with_llm_trace(
                executor,
                _extract_user_keywords,
                keyword_state,
                detail_answer,
                group="product_filter.understanding",
            ): ("keywords", keyword_state),
            submit_with_llm_trace(
                executor,
                _infer_small_categories_from_mid,
                small_category_state,
                detail_answer,
                selected_mid_category,
                constraint_filtered_catalog,
                budget_products=base_filtered,
                budget_min=budget_min,
                budget_max=budget_max,
                group="product_filter.understanding",
            ): ("small_categories", small_category_state),
        }

        for future in as_completed(futures):
            task_name, task_state = futures[future]
            try:
                value = future.result()
            except Exception as exc:
                print(f"[product-filter-understanding-error] task={task_name} error={exc}")
                value = []
            _merge_parallel_filter_task_state(state, task_state)
            if task_name == "keywords":
                user_keywords = list(value or [])
            else:
                inferred_small_categories = list(value or [])

    return user_keywords, inferred_small_categories


def _apply_explicit_hard_constraints(
    products: List[ProductCandidate],
    detail_answer: str,
) -> List[ProductCandidate]:
    text = detail_answer or ""
    hard_signal = any(keyword in text for keyword in ("必须", "硬性", "一定要", "只能"))
    if not hard_signal:
        return products

    required_groups: List[Tuple[str, ...]] = []
    if "28寸" in text or "28 寸" in text:
        required_groups.append(("28寸", "28 寸"))
    if "铝框" in text or "铝合金" in text:
        required_groups.append(("铝框", "铝合金", "铝制", "铝边框"))
    if "德国品牌" in text or "德国" in text or "德系" in text:
        required_groups.append(("德国", "德系", "germany", "german", "travelite"))

    if not required_groups:
        return products

    filtered: List[ProductCandidate] = []
    for product in products:
        haystack = product_search_text(product).lower()
        if all(any(option.lower() in haystack for option in group) for group in required_groups):
            filtered.append(product)
    return filtered


def format_recommendations(products: List[ProductCandidate], max_items: int = 3) -> str:
    del max_items
    if not products:
        return "抱歉，暂时没有找到符合条件的商品。"

    return "我先按当前需求筛了几款，您可以先看看。"


def format_recommendation_cards(products: List[ProductCandidate], max_items: int = 3) -> List[dict]:
    if not products:
        return []
    return _build_product_cards(products[:max_items])


def _resolve_selected_categories(state: GiftRecommendationState, detail_answer: str) -> Tuple[Optional[str], Optional[str]]:
    """
    根据当前 state 解析出：
    - selected_small_category
    - selected_mid_category

    规则：
    - 如果当前就是小类，直接拿小类；再映射出其中类
    - 如果当前是中类，直接拿中类；可再尝试结合 detail_answer 推断一个更细的小类
    """
    if not state.selected_category:
        return None, None

    category_level = getattr(state, "category_level", "")
    category_name = state.selected_category.category_name

    selected_small_category: Optional[str] = None
    selected_mid_category: Optional[str] = None

    if category_level == "subcategory":
        selected_small_category = category_name
        selected_mid_category = COMPLETE_SMALL_TO_MID_CATEGORY_MAP.get(category_name)

    elif category_level == "mid_category":
        selected_mid_category = category_name

    else:
        selected_mid_category = getattr(state, "selected_mid_category", None)

    return selected_small_category, selected_mid_category


def _apply_category_filter_with_fallback(
    products: List[ProductCandidate],
    selected_small_category: Optional[str],
    selected_mid_category: Optional[str],
) -> List[ProductCandidate]:
    """
    类目过滤降级策略：
    1. 先按小品类过滤（lvl_03）
    2. 若无结果，再按中品类过滤（lvl_02）
    3. 若仍无结果，则不过滤
    """
    if selected_small_category:
        small_filtered = [
            p for p in products
            if _normalize_text(getattr(p, "small_category", "") or "") == _normalize_text(selected_small_category)
            and (
                not selected_mid_category
                or _normalize_text(getattr(p, "mid_category", "") or "") == _normalize_text(selected_mid_category)
            )
        ]
        if small_filtered:
            print(f"按小品类过滤成功，命中 {len(small_filtered)} 条")
            return small_filtered
        print("按小品类过滤无结果，降级尝试中品类过滤")

    if selected_mid_category:
        mid_filtered = [
            p for p in products
            if _normalize_text(getattr(p, "mid_category", "") or "") == _normalize_text(selected_mid_category)
        ]
        if mid_filtered:
            print(f"按中品类过滤成功，命中 {len(mid_filtered)} 条")
            return mid_filtered
        print("按中品类过滤也无结果")

    if selected_small_category or selected_mid_category:
        return []

    return products


def _rank_by_keyword_score(products: List[ProductCandidate], keywords: List[str]) -> List[ProductCandidate]:
    """
    按关键词命中数（score）降序排序；score 相同按价格升序。
    """
    scored: List[Tuple[int, float, ProductCandidate]] = []
    for p in products:
        s = _keyword_score(p, keywords)
        scored.append((s, p.price, p))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [p for _, _, p in scored]


def _rank_by_keyword_and_soft_budget(
    products: List[ProductCandidate],
    keywords: List[str],
    budget_min: Optional[float],
    budget_max: Optional[float],
) -> List[ProductCandidate]:
    target_price, budget_width = _get_soft_budget_target_and_width(budget_min, budget_max)
    if target_price is None or budget_width is None:
        return _rank_by_keyword_score(products, keywords)

    scored: List[Tuple[float, float, ProductCandidate]] = []
    for product in products:
        keyword_score = _keyword_score(product, keywords)
        price_penalty = abs(product.price - target_price) / budget_width
        in_range_bonus = SOFT_BUDGET_IN_RANGE_BONUS if _is_price_in_budget(
            product.price,
            budget_min,
            budget_max,
        ) else 0.0
        final_score = (
            keyword_score * SOFT_BUDGET_KEYWORD_WEIGHT
            + in_range_bonus
            - price_penalty * SOFT_BUDGET_PRICE_WEIGHT
        )
        scored.append((final_score, product.price, product))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [product for _, _, product in scored]


def _apply_budget_filter(
    products: List[ProductCandidate],
    budget_min: Optional[float],
    budget_max: Optional[float],
) -> List[ProductCandidate]:
    if budget_min is None and budget_max is None:
        return list(products)
    return [
        product for product in products
        if _is_price_in_budget(product.price, budget_min, budget_max)
    ]


def _is_price_in_budget(
    price: float,
    budget_min: Optional[float],
    budget_max: Optional[float],
) -> bool:
    if budget_min is not None and price < float(budget_min):
        return False
    if budget_max is not None and price > float(budget_max):
        return False
    return True


def _should_apply_soft_budget_ranking(
    budget_matched_count: int,
    unbudgeted_count: int,
    budget_min: Optional[float],
    budget_max: Optional[float],
) -> bool:
    if budget_min is None and budget_max is None:
        return False
    if _is_exact_budget(budget_min, budget_max):
        return False
    return (
        budget_matched_count < MIN_BUDGET_MATCHED_RESULTS
        and unbudgeted_count >= MIN_BUDGET_MATCHED_RESULTS
    )


def _is_exact_budget(
    budget_min: Optional[float],
    budget_max: Optional[float],
) -> bool:
    if budget_min is None or budget_max is None:
        return False
    return float(budget_min) == float(budget_max)


def _get_soft_budget_target_and_width(
    budget_min: Optional[float],
    budget_max: Optional[float],
) -> Tuple[Optional[float], Optional[float]]:
    if budget_min is not None and budget_max is not None:
        target_price = (float(budget_min) + float(budget_max)) / 2
        explicit_width = abs(float(budget_max) - float(budget_min))
    elif budget_max is not None:
        target_price = float(budget_max)
        explicit_width = 0.0
    elif budget_min is not None:
        target_price = float(budget_min)
        explicit_width = 0.0
    else:
        return None, None

    budget_width = max(explicit_width, abs(target_price) * 0.2, 1.0)
    return target_price, budget_width


def _format_budget_info(
    budget_min: Optional[float],
    budget_max: Optional[float],
) -> str:
    if budget_min is not None and budget_max is not None:
        return f"您的预算 {_format_price_str(float(budget_min))}-{_format_price_str(float(budget_max))} 元"
    if budget_min is not None:
        return f"您的预算 {_format_price_str(float(budget_min))} 元起"
    if budget_max is not None:
        return f"您的预算 {_format_price_str(float(budget_max))} 元以内"
    return ""


def _get_slot_value(state: GiftRecommendationState, slot_name: str):
    slot = getattr(state, "filled_slots", {}).get(slot_name)
    if not slot:
        return None
    return getattr(slot, "value", None)


def _build_season_rerank_guidance(current_season: str) -> str:
    season = str(current_season or "").strip()
    common_rule = (
        "当前季节匹配度是重要排序因素，仅次于用户明确硬性要求、禁忌和预算。"
        "不要仅因为礼赠体面或价格更高，就把明显反季节商品排入前三。"
        "若用户明确指定反季节品类，或收礼人所在地/使用场景能解释反季节需求，则可以按用户需求优先。"
    )
    season_rules = {
        "春季": (
            "春季优先考虑轻薄外套、衬衫、针织、春游出行、换季护肤、舒缓保湿等商品；"
            "降低厚重冬装、强保暖商品、强防暑降温商品的排序。"
        ),
        "夏季": (
            "夏季优先考虑轻薄、清爽、透气、防晒、降温、补水、夏日出行等商品；"
            "服饰鞋包中优先 T恤、短袖、衬衫、防晒衣、薄外套、凉鞋、遮阳帽等。"
            "明显秋冬属性商品如羽绒服、厚毛衣、保暖内衣、围巾、手套、雪地靴等，"
            "除非用户明确指定，否则不得排入前三。"
        ),
        "秋季": (
            "秋季优先考虑薄外套、针织、衬衫、换季护肤、保湿、滋补、秋日出行等商品；"
            "降低极端夏季降温商品和厚重冬季保暖商品的排序。"
        ),
        "冬季": (
            "冬季优先考虑羽绒服、保暖内衣、围巾、手套、厚外套、滋补、保湿修护、冬季护理等商品；"
            "明显夏季属性商品如短袖、凉鞋、强防暑降温商品，除非用户明确指定，否则不得排入前三。"
        ),
    }
    if season in season_rules:
        return f"{common_rule}{season_rules[season]}"
    return (
        "当前季节未知或无法识别时，不启用具体季节偏好；仍需避免把明显不符合用户使用场景的商品排到前面。"
    )


def _build_season_small_category_guidance(current_season: str) -> str:
    season = str(current_season or "").strip()
    common_rule = (
        "当前季节是小类选择的重要约束。"
        "请先判断候选小类中是否存在符合当前季节的小类。"
        "如果存在当季小类，只能选择当季小类，且可以少于3个，不要为了凑满3个选择反季节小类。"
        "如果没有任何当季小类，则忽略季节限制，按原有送礼匹配规则选择最多3个小类。"
    )
    season_rules = {
        "春季": (
            "春季优先选择轻薄、换季、出行、舒适、保湿舒缓、滋补调养相关小类；"
            "避免厚重保暖、极端防寒、强防暑降温等明显不适合春季的小类。"
        ),
        "夏季": (
            "夏季优先选择轻薄、透气、清爽、防晒、降温、补水、夏日出行相关小类。"
            "服饰鞋包中优先 T恤、短袖、衬衫、POLO、薄款裤装、凉鞋、遮阳帽等。"
            "明显秋冬属性小类如羽绒服、厚外套、男士外套、女士外套、毛衣、保暖内衣、围巾、手套、雪地靴等，"
            "不要作为夏季当季小类选择；不要因为这些小类下可能存在防晒衣、薄外套等商品而保留该小类。"
        ),
        "秋季": (
            "秋季优先选择薄外套、针织、衬衫、长裤、换季护肤、保湿、滋补、秋日出行相关小类；"
            "避免极端夏季降温和厚重冬季保暖小类。"
        ),
        "冬季": (
            "冬季优先选择保暖、厚外套、羽绒服、围巾、手套、保湿修护、滋补调养相关小类；"
            "明显夏季属性小类如短袖、凉鞋、防暑降温等，不要作为冬季当季小类选择。"
        ),
    }
    if season in season_rules:
        return f"{common_rule}{season_rules[season]}"
    return "当前季节未知或无法识别时，不启用具体季节小类限制，按原有送礼匹配规则选择小类。"


def _coerce_optional_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _keyword_score(product: ProductCandidate, keywords: List[str]) -> int:
    """
    每命中 1 个关键词 +1 分（去重计分）。
    """
    if not keywords:
        return 0
    text = _normalize_text(product_search_text(product))
    unique_keywords = set(keywords)
    return sum(1 for kw in unique_keywords if kw and kw in text)


def _rerank_products_with_llm(
    state: GiftRecommendationState,
    detail_answer: str,
    products: List[ProductCandidate],
) -> List[ProductCandidate]:
    """
    取前10个候选商品交给大模型重新排序。
    如果失败，则返回原顺序。
    """
    if len(products) <= 1:
        return products

    cache = _get_product_filter_cache(state)
    sku_key = ",".join(str(getattr(product, "sku_id", "") or "") for product in products)
    cache_key = f"detail={detail_answer or ''}|slots={_build_all_slot_cache_key(state)}|skus={sku_key}"
    cached_sku_ids = cache["rerank"].get(cache_key)
    if isinstance(cached_sku_ids, list):
        product_map = {product.sku_id: product for product in products}
        cached_products = [
            product_map[sku_id]
            for sku_id in cached_sku_ids
            if isinstance(sku_id, str) and sku_id in product_map
        ]
        if cached_products:
            used_sku_ids = {product.sku_id for product in cached_products}
            cached_products.extend([product for product in products if product.sku_id not in used_sku_ids])
            _record_product_filter_cache_hit(state, "rerank")
            return cached_products

    prompt = _build_rerank_prompt(state, detail_answer, products)

    try:
        _record_product_filter_llm_call(state, "rerank")
        result = call_json(
            prompt=prompt,
            system_prompt=PRODUCT_RERANK_PROMPT,
            temperature=0.1,
        )

        ranked_sku_ids = result.get("ranked_sku_ids", []) if isinstance(result, dict) else []
        reason = result.get("reason", "") if isinstance(result, dict) else ""

        print(f"LLM rerank 结果: {ranked_sku_ids}, 理由: {reason}")

        if (not ranked_sku_ids or not isinstance(ranked_sku_ids, list)) and _looks_like_hard_constraint_no_match(detail_answer, reason):
            state.hard_constraint_no_match = True
            return []

        if not ranked_sku_ids or not isinstance(ranked_sku_ids, list):
            return products

        product_map = {p.sku_id: p for p in products}

        reranked_products: List[ProductCandidate] = []
        used_sku_ids = set()

        for sku_id in ranked_sku_ids:
            if sku_id in product_map and sku_id not in used_sku_ids:
                reranked_products.append(product_map[sku_id])
                used_sku_ids.add(sku_id)

        for p in products:
            if p.sku_id not in used_sku_ids:
                reranked_products.append(p)

        cache["rerank"][cache_key] = [product.sku_id for product in reranked_products]
        return reranked_products

    except Exception as e:
        print(f"LLM rerank 失败: {e}")
        return products


def _looks_like_hard_constraint_no_match(detail_answer: str, rerank_reason: str) -> bool:
    text = f"{detail_answer or ''}\n{rerank_reason or ''}"
    hard_signal = any(keyword in text for keyword in ("必须", "硬性", "一定要", "只能", "不可", "今天送到", "当日达"))
    no_match_signal = any(keyword in text for keyword in ("不符合", "无一商品", "无一符合", "无法推荐", "均不满足", "没有满足", "无结果"))
    return hard_signal and no_match_signal


def _build_rerank_prompt(
    state: GiftRecommendationState,
    detail_answer: str,
    products: List[ProductCandidate],
) -> str:
    chat_logs = _build_chat_logs_text(state)
    slot_text = _build_slot_summary_text(state)
    current_season = str(_get_slot_value(state, "current_season") or "").strip() or "未知"
    season_guidance = _build_season_rerank_guidance(current_season)
    product_text = _build_candidate_products_text(products)

    return f"""请基于以下信息对候选商品重新排序。

【用户历史对话】
{chat_logs}

【用户已填写槽位】
{slot_text}

【当前季节】
{current_season}

【季节适配排序规则】
{season_guidance}

【用户最新补充信息】
{detail_answer or "无"}

【候选商品列表】
{product_text}
"""


def _build_chat_logs_text(state: GiftRecommendationState) -> str:
    if not getattr(state, "chat_history", None):
        return "无"

    lines: List[str] = []
    for msg in state.chat_history[-10:]:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content:
            continue
        role_text = "用户" if role == "user" else "助手"
        lines.append(f"{role_text}: {content}")

    return "\n".join(lines) if lines else "无"


def _build_slot_summary_text(state: GiftRecommendationState) -> str:
    if not getattr(state, "filled_slots", None):
        return "无"

    lines: List[str] = []
    for slot_name, slot in state.filled_slots.items():
        value = getattr(slot, "value", None)
        if value is None or value == "":
            continue
        display_name = getattr(slot, "display_name", slot_name)
        lines.append(f"- {display_name}: {value}")

    return "\n".join(lines) if lines else "无"


def _build_candidate_products_text(products: List[ProductCandidate]) -> str:
    lines: List[str] = []

    for index, p in enumerate(products, start=1):
        lines.append(
            f"{index}. "
            f"sku_id={p.sku_id}; "
            f"name={p.name}; "
            f"price={p.price}; "
            f"brand={p.brand or '未知'}; "
            f"category={p.category or ''}; "
            f"mid_category={getattr(p, 'mid_category', '') or ''}; "
            f"small_category={getattr(p, 'small_category', '') or ''}; "
            f"description={p.description or ''}"
        )

    return "\n".join(lines) if lines else "无"


@lru_cache(maxsize=1)
def _load_products_from_csv() -> List[ProductCandidate]:
    csv_path = _resolve_catalog_path()
    print(csv_path)
    if not csv_path:
        return []

    products: List[ProductCandidate] = []
    with open(csv_path, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            sku_id = (row.get("sku_id") or row.get("asset_id") or "").strip()
            name = (row.get("sku_name") or row.get("goods_short_name") or row.get("goods_name") or "").strip()

            category_raw = (
                row.get("lefox_category_name_lvl_01")
                or row.get("category_name_lvl_01")
                or row.get("category_name")
                or ""
            ).strip()

            mid_category_raw = (
                row.get("lefox_category_name_lvl_02")
                or row.get("category_name_lvl_02")
                or ""
            ).strip()

            small_category_raw = (
                row.get("lefox_category_name_lvl_03")
                or row.get("category_name_lvl_03")
                or ""
            ).strip()

            price = _parse_price(row.get("fixed_sku_price"))
            brand = (row.get("brand_name") or row.get("brand_english_name") or "").strip() or None
            description = _build_description(row)

            if not sku_id or not name or price is None:
                continue

            category = _map_category(mid_category_raw or category_raw)
            subcategory = mid_category_raw if mid_category_raw else None

            product = ProductCandidate(
                sku_id=sku_id,
                name=name,
                category=category,
                subcategory=subcategory,
                price=price,
                brand=brand,
                description=description,
            )

            try:
                setattr(product, "mid_category", mid_category_raw or None)
                setattr(product, "small_category", small_category_raw or None)

                # 🔍 调试日志：检查白酒商品的mid_category是否正确加载
                # if "白酒" in name or "酱香" in name or "浓香" in name or "茅台" in name or "郎酒" in name:
                #     print(f"[白酒商品加载] sku_id={sku_id}, name={name}, mid_category_raw={mid_category_raw}, 最终mid_category={getattr(product, 'mid_category')}")

                # 新增：保留前端返回需要的原始字段
                raw_pic = row.get("pic_list") or row.get("show_pic") or row.get("pic_url") or row.get("sku_pic") or ""
                setattr(product, "show_pic", _parse_first_pic(raw_pic))
                setattr(product, "company_id", (row.get("company_id") or "").strip() or None)
                setattr(product, "fixed_sku_price", row.get("fixed_sku_price"))
                setattr(product, "sku_name", name)
            except Exception:
                pass

            products.append(product)

    return products


def _map_category(category_raw: str) -> str:
    """
    将商品数据库中的中品类映射到大品类。
    """
    if not category_raw:
        return ""
    if category_raw in COMPLETE_MID_TO_BIG_CATEGORY_MAP:
        return COMPLETE_MID_TO_BIG_CATEGORY_MAP[category_raw]

    category_mapping ={
    "面部护肤": "护肤",
    "男士护肤": "护肤",
    "儿童护肤": "护肤",
    "面部彩妆": "美妆",
    "美妆工具": "美妆",
    "香水香氛": "香氛",
    "美发护发": "美发护发",
    "口腔护理": "口腔护理",
    "身体护理": "个护清洁",
    "女性护理": "女性护理",
    "个护电器": "个护清洁",
    "儿童洗护用品": "个护清洁",
    "家庭清洁": "家庭清洁",
    "纸品清洗": "家庭清洁",
    "女装（含中性）": "女装（含中性）",
    "男装": "男装",
    "内衣": "内衣",
    "儿童服饰": "服装（男女/内衣/童装）",
    "女鞋": "鞋靴",
    "男鞋": "鞋靴",
    "功能箱包": "功能箱包",
    "男包": "男包",
    "女包（含中性）": "女包（含中性）",
    "旅行用品": "旅行用品",
    "时尚配饰": "时尚配饰",
    "服配": "服配",
    "腕表": "腕表",
    "眼镜": "配饰（钟表/眼镜/珠宝）",
    "黄金珠宝": "配饰（钟表/眼镜/珠宝）",
    "婴儿喂养用品": "母婴",
    "儿童家纺": "母婴",
    "玩具": "母婴",
    "文具": "文具",
    "品质生活": "品质生活",
    "厨房小电": "家居与厨房",
    "粮油调味速食": "粮油调味速食",
    "海鲜水产": "食品与冲饮（非酒）",
    "休闲食品": "休闲食品",
    "咖啡冲饮": "冲调与乳品茶",
    "茗茶": "冲调与乳品茶",
    "葡萄酒": "葡萄酒",
    "洋酒": "烈酒与白酒",
    "国酒": "烈酒与白酒",
    "手机通讯": "数码影音",
    "影音娱乐": "数码影音",
    "体重管理": "营养保健（滋补/维矿/功能健康）",
    "调节三高": "营养保健（滋补/维矿/功能健康）",
    "骨骼健康": "营养保健（滋补/维矿/功能健康）",
    "维生素/矿物质": "营养保健（滋补/维矿/功能健康）",
    "运动营养": "营养保健（滋补/维矿/功能健康）",
    "其他滋补品": "营养保健（滋补/维矿/功能健康）",
    "滋补贵细": "营养保健（滋补/维矿/功能健康）",
    "参茸制品": "营养保健（滋补/维矿/功能健康）",
    "其他营养健康": "营养保健（滋补/维矿/功能健康）",
    "健康理疗": "营养保健（滋补/维矿/功能健康）",
    "宠物医疗保健": "宠物",
    "宠物玩具": "宠物",
    "礼盒礼袋": "礼赠/营销",
    "推广商品": "礼赠/营销",
}

    return category_mapping.get(category_raw, category_raw)


def _infer_small_categories_from_mid(
    state: GiftRecommendationState,
    detail_answer: str,
    mid_category: str,
    products: List[ProductCandidate],
    budget_products: Optional[List[ProductCandidate]] = None,
    budget_min: Optional[float] = None,
    budget_max: Optional[float] = None,
    max_categories: int = 3,
) -> List[str]:
    if not detail_answer or not mid_category:
        return []

    candidate_small_categories = MID_TO_SMALL_CATEGORY_MAP.get(mid_category, [])
    rejected_subcategories = set(_extract_rejected_subcategories(state))
    if rejected_subcategories:
        candidate_small_categories = [
            category for category in candidate_small_categories
            if category not in rejected_subcategories
        ]
    if not candidate_small_categories:
        return []

    budget_available_small_categories = _get_budget_available_small_categories(
        budget_products or [],
        mid_category,
        candidate_small_categories,
    )
    prompt_small_categories = budget_available_small_categories or candidate_small_categories
    if len(candidate_small_categories) == 1:
        return candidate_small_categories[:1]
    if len(prompt_small_categories) == 1:
        return prompt_small_categories[:1]

    current_season = str(_get_slot_value(state, "current_season") or "").strip() or "未知"
    cache = _get_product_filter_cache(state)
    cache_key = (
        f"mid={mid_category}|detail={detail_answer or ''}|"
        f"rejected={json.dumps(sorted(rejected_subcategories), ensure_ascii=False)}|"
        f"season={current_season}"
    )
    cached = cache["small_category_infer"].get(cache_key)
    if isinstance(cached, list):
        selected_cached = [
            category
            for category in cached
            if isinstance(category, str) and category in candidate_small_categories
        ][:max_categories]
        _record_product_filter_cache_hit(state, "small_category_infer")
        return selected_cached

    stats_text = _build_small_category_stats_text(
        products,
        mid_category,
        prompt_small_categories,
        budget_products=budget_products or [],
    )
    budget_constraint_text = _build_small_category_budget_constraint_text(
        budget_min,
        budget_max,
        bool(budget_available_small_categories),
    )
    season_guidance = _build_season_small_category_guidance(current_season)
    prompt = f"""当前中类：
{mid_category}

候选小类：
{json.dumps(prompt_small_categories, ensure_ascii=False)}

候选小类商品统计：
{stats_text}

预算小类约束：
{budget_constraint_text}

用户最近对话：
{_build_chat_logs_text(state)}

已填送礼槽位：
{_build_slot_summary_text(state)}

当前季节：
{current_season}

季节小类选择规则：
{season_guidance}

用户当前需求信息：
{detail_answer or "无"}

业务补充规则：
- 用户说“送女朋友/女生/生日礼物”，香水香氛中优先考虑“香水”和“香水套装”。
- 用户说“家里用、卧室、香薰、扩香、蜡烛”，优先考虑“家居香氛”。
- 用户说“普通款、基础款、简单点”，优先选择覆盖面广、商品数较多的小类。
- 用户没有预算时，不要因为价格区间高就排除小类。
- 用户有预算且候选小类中存在预算内商品时，预算是硬约束，只能选择预算内商品数大于 0 的小类。
- 不要选择预算内商品数为 0 的小类；只有所有候选小类预算内都没有商品时，才允许忽略预算做降级选择。
- 如果候选小类只有 1 个，直接返回该小类。
- 如果候选小类只有 2 个且都合理，可以返回 2 个。
- 如果存在符合当前季节的小类，只返回符合季节的小类，数量可以少于 3 个。
- 如果当前中类下没有符合当前季节的小类，则按原有送礼匹配规则选择最多 3 个小类。

请基于以上信息，从候选小类中选择最多 {max_categories} 个最可能的小类。"""

    try:
        _record_product_filter_llm_call(state, "small_category_infer")
        result = call_json(
            prompt=prompt,
            system_prompt=MULTI_SMALL_CATEGORY_INFERENCE_PROMPT,
            temperature=0.1,
        )
    except Exception as e:
        print(f"多小类推断失败: {e}")
        if budget_available_small_categories:
            return _rank_small_categories_by_budget_availability(
                budget_products or [],
                mid_category,
                budget_available_small_categories,
                budget_min=budget_min,
                budget_max=budget_max,
                max_categories=max_categories,
            )
        return []

    items = result.get("selected_small_categories", []) if isinstance(result, dict) else []
    if not isinstance(items, list):
        if budget_available_small_categories:
            return _rank_small_categories_by_budget_availability(
                budget_products or [],
                mid_category,
                budget_available_small_categories,
                budget_min=budget_min,
                budget_max=budget_max,
                max_categories=max_categories,
            )
        return []

    selected: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        small_category = item.get("small_category")
        try:
            confidence = float(item.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if (
            isinstance(small_category, str)
            and small_category in prompt_small_categories
            and small_category not in selected
            and confidence >= 0.55
        ):
            selected.append(small_category)
        if len(selected) >= max_categories:
            break

    if not selected and budget_available_small_categories:
        selected = _rank_small_categories_by_budget_availability(
            budget_products or [],
            mid_category,
            budget_available_small_categories,
            budget_min=budget_min,
            budget_max=budget_max,
            max_categories=max_categories,
        )

    print(f"多小类推断结果: {selected}")
    cache["small_category_infer"][cache_key] = selected
    return selected


def _build_small_category_stats_text(
    products: List[ProductCandidate],
    mid_category: str,
    candidate_small_categories: List[str],
    budget_products: Optional[List[ProductCandidate]] = None,
) -> str:
    lines: List[str] = []
    normalized_mid = _normalize_text(mid_category)
    budget_products = budget_products or []
    for small_category in candidate_small_categories:
        normalized_small = _normalize_text(small_category)
        matched = [
            product for product in products
            if (
                _normalize_text(getattr(product, "mid_category", "") or "") == normalized_mid
                and _normalize_text(getattr(product, "small_category", "") or "") == normalized_small
            )
        ]
        budget_matched = [
            product for product in budget_products
            if (
                _normalize_text(getattr(product, "mid_category", "") or "") == normalized_mid
                and _normalize_text(getattr(product, "small_category", "") or "") == normalized_small
            )
        ]
        if not matched:
            lines.append(f"- {small_category}: 商品数 0")
            continue

        prices = [product.price for product in matched if product.price is not None]
        min_price = min(prices) if prices else 0
        max_price = max(prices) if prices else 0
        budget_prices = [product.price for product in budget_matched if product.price is not None]
        budget_min_price = min(budget_prices) if budget_prices else 0
        budget_max_price = max(budget_prices) if budget_prices else 0
        representatives = [
            getattr(product, "sku_name", None) or product.name
            for product in matched[:3]
            if getattr(product, "sku_name", None) or product.name
        ]
        budget_representatives = [
            getattr(product, "sku_name", None) or product.name
            for product in budget_matched[:3]
            if getattr(product, "sku_name", None) or product.name
        ]
        lines.append(
            (
                "- {small_category}: 商品数 {count}，价格区间 {min_price:.0f}-{max_price:.0f}，"
                "预算内商品数 {budget_count}，预算内价格区间 {budget_min_price:.0f}-{budget_max_price:.0f}，"
                "代表商品：{representatives}，预算内代表商品：{budget_representatives}"
            ).format(
                small_category=small_category,
                count=len(matched),
                min_price=min_price,
                max_price=max_price,
                budget_count=len(budget_matched),
                budget_min_price=budget_min_price,
                budget_max_price=budget_max_price,
                representatives="、".join(representatives) if representatives else "无",
                budget_representatives="、".join(budget_representatives) if budget_representatives else "无",
            )
        )
    return "\n".join(lines) if lines else "无"


def _build_small_category_budget_constraint_text(
    budget_min: Optional[float],
    budget_max: Optional[float],
    has_budget_available_small_categories: bool,
) -> str:
    budget_info = _format_budget_info(budget_min, budget_max)
    if not budget_info:
        return "用户未提供预算，不启用预算小类硬约束。"
    if has_budget_available_small_categories:
        return (
            f"{budget_info}。当前中类下存在预算内有商品的小类，"
            "只能从候选小类列表中选择，不要选择预算内商品数为 0 的小类。"
        )
    return (
        f"{budget_info}。当前中类下所有候选小类预算内商品数均为 0，"
        "可以按送礼适配度选择小类，后续会进入预算放宽降级。"
    )


def _get_budget_available_small_categories(
    budget_products: List[ProductCandidate],
    mid_category: str,
    candidate_small_categories: List[str],
) -> List[str]:
    available = []
    normalized_mid = _normalize_text(mid_category)
    for small_category in candidate_small_categories:
        normalized_small = _normalize_text(small_category)
        if any(
            _normalize_text(getattr(product, "mid_category", "") or "") == normalized_mid
            and _normalize_text(getattr(product, "small_category", "") or "") == normalized_small
            for product in budget_products
        ):
            available.append(small_category)
    return available


def _rank_small_categories_by_budget_availability(
    budget_products: List[ProductCandidate],
    mid_category: str,
    candidate_small_categories: List[str],
    budget_min: Optional[float],
    budget_max: Optional[float],
    max_categories: int = 3,
) -> List[str]:
    target_price, _ = _get_soft_budget_target_and_width(budget_min, budget_max)
    normalized_mid = _normalize_text(mid_category)
    ranked: List[Tuple[int, float, str]] = []
    for small_category in candidate_small_categories:
        normalized_small = _normalize_text(small_category)
        matched = [
            product for product in budget_products
            if (
                _normalize_text(getattr(product, "mid_category", "") or "") == normalized_mid
                and _normalize_text(getattr(product, "small_category", "") or "") == normalized_small
            )
        ]
        if not matched:
            continue
        if target_price is None:
            price_distance = min(float(product.price or 0) for product in matched)
        else:
            price_distance = min(abs(float(product.price or 0) - target_price) for product in matched)
        ranked.append((len(matched), price_distance, small_category))

    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [small_category for _, _, small_category in ranked[:max_categories]]


def _select_products_by_small_category_quota(
    state: GiftRecommendationState,
    detail_answer: str,
    base_products: List[ProductCandidate],
    fallback_catalog: List[ProductCandidate],
    selected_mid_category: str,
    selected_small_categories: List[str],
    user_keywords: List[str],
    total_limit: int = 3,
) -> List[ProductCandidate]:
    quota = _allocate_small_category_quota(selected_small_categories, total_limit=total_limit)
    selected_products: List[ProductCandidate] = []
    used_sku_ids = set()
    small_category_counts: Dict[str, int] = {}
    rerank_inputs: List[Tuple[str, int, List[ProductCandidate]]] = []
    for small_category, limit in quota:
        candidates = _filter_products_by_small_category(
            base_products,
            selected_mid_category,
            small_category,
        )
        candidates = _apply_explicit_hard_constraints(candidates, detail_answer)
        ranked = _rank_by_keyword_score(candidates, user_keywords)
        rerank_inputs.append((small_category, limit, ranked))

    reranked_by_index: Dict[int, List[ProductCandidate]] = {}
    rerank_futures = {}
    runnable_jobs = [
        (index, small_category, limit, ranked)
        for index, (small_category, limit, ranked) in enumerate(rerank_inputs)
        if len(ranked[:10]) > 1
    ]
    for index, (_, _, ranked) in enumerate(rerank_inputs):
        if len(ranked[:10]) <= 1:
            reranked_by_index[index] = ranked[:10]

    if runnable_jobs:
        with ThreadPoolExecutor(max_workers=min(3, len(runnable_jobs))) as executor:
            for index, small_category, _limit, ranked in runnable_jobs:
                task_state = _clone_state_for_parallel_filter_task(state)
                future = submit_with_llm_trace(
                    executor,
                    _rerank_products_with_llm,
                    task_state,
                    detail_answer,
                    ranked[:10],
                    group="product_filter.small_category_rerank",
                )
                rerank_futures[future] = (index, small_category, ranked, task_state)

            for future in as_completed(rerank_futures):
                index, small_category, ranked, task_state = rerank_futures[future]
                try:
                    reranked_by_index[index] = list(future.result() or ranked)
                except Exception as exc:
                    print(
                        "[small-category-rerank-error] "
                        f"category={small_category} error={exc}"
                    )
                    reranked_by_index[index] = ranked
                _merge_parallel_filter_task_state(state, task_state)

    for index, (small_category, limit, ranked) in enumerate(rerank_inputs):
        reranked = reranked_by_index.get(index, ranked)
        ordered_products = _prefer_unexposed_products_for_slot_update(
            state,
            reranked if reranked else ranked,
        )
        for product in ordered_products:
            if product.sku_id in used_sku_ids:
                continue
            selected_products.append(product)
            used_sku_ids.add(product.sku_id)
            small_category_counts[small_category] = small_category_counts.get(small_category, 0) + 1
            if small_category_counts[small_category] >= limit:
                break

    if len(selected_products) >= total_limit:
        return selected_products[:total_limit]

    budget_min = _coerce_optional_float(_get_slot_value(state, "budget_min"))
    budget_max = _coerce_optional_float(_get_slot_value(state, "budget_max"))
    mid_budget_candidates = [
        product for product in base_products
        if _normalize_text(getattr(product, "mid_category", "") or "") == _normalize_text(selected_mid_category)
    ]
    mid_budget_candidates = _apply_explicit_hard_constraints(mid_budget_candidates, detail_answer)
    fill_ranked = _rank_by_keyword_score(mid_budget_candidates, user_keywords)
    for product in _prefer_unexposed_products_for_slot_update(state, fill_ranked):
        if product.sku_id in used_sku_ids:
            continue
        selected_products.append(product)
        used_sku_ids.add(product.sku_id)
        if len(selected_products) >= total_limit:
            return selected_products[:total_limit]

    has_budget = budget_min is not None or budget_max is not None
    if not has_budget or len(mid_budget_candidates) >= total_limit:
        if selected_products:
            return selected_products[:total_limit]

    fallback_candidates = _collect_small_category_fallback_candidates(
        fallback_catalog,
        selected_mid_category,
        selected_small_categories,
        detail_answer,
    )
    mid_fallback_candidates = [
        product for product in fallback_catalog
        if _normalize_text(getattr(product, "mid_category", "") or "") == _normalize_text(selected_mid_category)
    ]
    mid_fallback_candidates = _apply_explicit_hard_constraints(mid_fallback_candidates, detail_answer)
    fallback_candidates = _merge_unique_products(fallback_candidates, mid_fallback_candidates)
    relaxed_start_count = len(selected_products)

    if fallback_candidates:
        soft_ranked = _rank_by_keyword_and_soft_budget(
            fallback_candidates,
            user_keywords,
            budget_min=budget_min,
            budget_max=budget_max,
        )
        for product in _prefer_unexposed_products_for_slot_update(state, soft_ranked):
            if product.sku_id in used_sku_ids:
                continue
            selected_products.append(product)
            used_sku_ids.add(product.sku_id)
            if len(selected_products) >= total_limit:
                break

    if len(selected_products) > relaxed_start_count:
        budget_info = _format_budget_info(budget_min, budget_max)
        state.downgrade_retry_triggered = True
        if len(mid_budget_candidates) == 0:
            state.downgrade_retry_reason = (
                f"{budget_info}范围内暂未找到当前中类的商品，"
                "以下已为您放宽预算后推荐相近选择："
            ) if budget_info else (
                "当前预算范围内暂未找到当前中类的商品，以下已为您放宽预算后推荐相近选择："
            )
        else:
            state.downgrade_retry_reason = (
                f"{budget_info}范围内可选商品不足 3 款，"
                "以下已为您补充价格接近预算的商品供参考："
            ) if budget_info else (
                "当前预算范围内可选商品不足 3 款，以下已为您补充价格接近预算的商品供参考："
            )
        return selected_products[:total_limit]

    if selected_products:
        return selected_products[:total_limit]

    # Budget/taboo filtering may remove every inferred small-category candidate.
    # Fall back once to the full catalog under the same inferred small categories.
    for small_category, limit in quota:
        candidates = _filter_products_by_small_category(
            fallback_catalog,
            selected_mid_category,
            small_category,
        )
        ranked = _rank_by_keyword_score(candidates, user_keywords)
        count = 0
        for product in _prefer_unexposed_products_for_slot_update(state, ranked):
            if product.sku_id in used_sku_ids:
                continue
            selected_products.append(product)
            used_sku_ids.add(product.sku_id)
            count += 1
            if count >= limit:
                break
        if len(selected_products) >= total_limit:
            break

    if selected_products:
        state.downgrade_retry_triggered = True
        state.downgrade_retry_reason = (
            "按当前预算或限制暂未找到完全匹配的小类商品，"
            "以下先为您放宽条件推荐相近选择："
        )
    return selected_products[:total_limit]


def _merge_unique_products(
    primary_products: List[ProductCandidate],
    secondary_products: List[ProductCandidate],
) -> List[ProductCandidate]:
    merged: List[ProductCandidate] = []
    used_sku_ids = set()
    for product in list(primary_products or []) + list(secondary_products or []):
        if product.sku_id in used_sku_ids:
            continue
        merged.append(product)
        used_sku_ids.add(product.sku_id)
    return merged


def _collect_small_category_fallback_candidates(
    products: List[ProductCandidate],
    selected_mid_category: str,
    selected_small_categories: List[str],
    detail_answer: str,
) -> List[ProductCandidate]:
    collected: List[ProductCandidate] = []
    used_sku_ids = set()
    for small_category in selected_small_categories:
        candidates = _filter_products_by_small_category(
            products,
            selected_mid_category,
            small_category,
        )
        candidates = _apply_explicit_hard_constraints(candidates, detail_answer)
        for product in candidates:
            if product.sku_id in used_sku_ids:
                continue
            collected.append(product)
            used_sku_ids.add(product.sku_id)
    return collected


def _allocate_small_category_quota(
    selected_small_categories: List[str],
    total_limit: int = 3,
) -> List[Tuple[str, int]]:
    normalized = []
    for small_category in selected_small_categories:
        if small_category and small_category not in normalized:
            normalized.append(small_category)
    if not normalized:
        return []
    if len(normalized) == 1:
        return [(normalized[0], total_limit)]
    if len(normalized) == 2:
        return [(normalized[0], max(1, total_limit - 1)), (normalized[1], 1)]
    return [(small_category, 1) for small_category in normalized[:total_limit]]


def _filter_products_by_small_category(
    products: List[ProductCandidate],
    selected_mid_category: str,
    selected_small_category: str,
) -> List[ProductCandidate]:
    normalized_mid = _normalize_text(selected_mid_category)
    normalized_small = _normalize_text(selected_small_category)
    return [
        product for product in products
        if (
            _normalize_text(getattr(product, "mid_category", "") or "") == normalized_mid
            and _normalize_text(getattr(product, "small_category", "") or "") == normalized_small
        )
    ]


def _infer_small_category_from_mid(detail_answer: str, mid_category: str) -> Optional[str]:
    """
    当当前选中的是中品类时，尝试根据补充信息进一步推断小品类。
    """
    if not detail_answer or not mid_category:
        return None

    candidate_small_categories = MID_TO_SMALL_CATEGORY_MAP.get(mid_category, [])
    if not candidate_small_categories:
        return None

    prompt = f"""当前中品类：{mid_category}
候选小品类：{candidate_small_categories}
用户补充信息：{detail_answer}

请在候选小品类中推断最匹配的小品类。"""

    try:
        result = call_json(
            prompt=prompt,
            system_prompt=SMALL_CATEGORY_INFERENCE_PROMPT,
            temperature=0.1,
        )

        inferred_small_category = result.get("inferred_small_category") if isinstance(result, dict) else None
        confidence = float(result.get("confidence", 0.0) or 0.0) if isinstance(result, dict) else 0.0
        reason = result.get("reason", "") if isinstance(result, dict) else ""

        print(f"小品类推断结果: {inferred_small_category}, 置信度: {confidence}, 理由: {reason}")

        if inferred_small_category in candidate_small_categories and confidence >= 0.7:
            return inferred_small_category
        return None
    except Exception as e:
        print(f"小品类推断失败: {e}")
        return None


def _resolve_catalog_path() -> Optional[str]:
    env_path = os.getenv("GIFT_CATALOG_CSV_PATH", "").strip()
    candidates = [
        env_path,
        os.path.join(os.path.dirname(__file__), "dim_pub_sku_20260513_115554.csv"),
        os.path.join(os.path.dirname(__file__), "gift_catalog.csv"),
        os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "..",
                "..",
                "present_v3",
                "present",
                "商品数据_2026.1.12.csv",
            )
        ),
    ]
    for path in candidates:
        if not path:
            continue
        normalized = os.path.abspath(path)
        if os.path.exists(normalized):
            return normalized
    return None


def _parse_price(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    cleaned = str(value).strip().replace(",", "")
    if not cleaned or cleaned.upper() == "NULL":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _build_description(row: dict) -> str:
    parts = [
        row.get("lefox_category_name_lvl_01"),
        row.get("lefox_category_name_lvl_02"),
        row.get("lefox_category_name_lvl_03"),
        row.get("category_name_lvl_01"),
        row.get("category_name_lvl_02"),
        row.get("category_name_lvl_03"),
        row.get("category_name"),
        row.get("desc_skin"),
        row.get("desc_effect"),
        row.get("desc_detail"),
        row.get("sku_specs"),
        row.get("tags"),
    ]
    cleaned_parts = [
        str(p).strip()
        for p in parts
        if p and str(p).strip() and str(p).strip().upper() != "NULL"
    ]
    return " | ".join(cleaned_parts)


def product_search_text(product: ProductCandidate) -> str:
    return " ".join(
        [
            product.name,
            product.description,
            product.brand or "",
            getattr(product, "mid_category", "") or "",
            getattr(product, "small_category", "") or "",
        ]
    )


# ---------------------------
# USER KEYWORDS (LLM-based)
# ---------------------------
def _generate_synonyms(
    keywords: List[str],
    state: Optional[GiftRecommendationState] = None,
) -> Dict[str, List[str]]:
    """
    为关键词列表生成同义词映射
    """
    if not keywords:
        return {}
    
    try:
        if state is not None:
            _record_product_filter_llm_call(state, "keyword_synonym")
        result = call_json(
            prompt=f"关键词列表：{keywords}",
            system_prompt=SYNONYM_GENERATION_PROMPT,
            temperature=0.1,
        )
        
        synonyms = result.get("synonyms", {})
        if isinstance(synonyms, dict):
            return synonyms
    except Exception as e:
        print(f"同义词生成失败: {e}")
    
    return {}


def _clean_keyword_list(keywords: object) -> List[str]:
    if not isinstance(keywords, list):
        return []

    cleaned: List[str] = []
    seen = set()
    for keyword in keywords:
        if keyword is None:
            continue
        normalized = str(keyword).strip().lower()
        if not normalized or normalized in seen:
            continue
        if len(normalized) > 40:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned


def _merge_keywords_and_synonyms(
    keywords: List[str],
    synonyms_mapping: object,
) -> List[str]:
    all_keywords = set(keywords)
    if isinstance(synonyms_mapping, dict):
        for keyword, synonyms in synonyms_mapping.items():
            normalized_keyword = str(keyword or "").strip().lower()
            if normalized_keyword not in all_keywords or not isinstance(synonyms, list):
                continue
            for synonym in synonyms:
                if synonym is None:
                    continue
                normalized = str(synonym).strip().lower()
                if normalized and len(normalized) <= 40:
                    all_keywords.add(normalized)
    return list(all_keywords)


def _build_keyword_cache_key(state: GiftRecommendationState, merged_text: str) -> str:
    return f"text={merged_text}|slots={_build_slot_cache_key(state)}"


def _extract_user_keywords(state: GiftRecommendationState, detail_answer: str) -> List[str]:
    texts: List[str] = []
    if detail_answer:
        texts.append(detail_answer)

    for slot_name in KEYWORD_SLOT_NAMES:
        slot = state.filled_slots.get(slot_name)
        if slot and slot.value is not None:
            texts.append(str(slot.value))

    merged = " ".join(texts).strip()
    if not merged:
        return []

    cache = _get_product_filter_cache(state)
    cache_key = _build_keyword_cache_key(state, merged)
    cached = cache["keyword_synonym"].get(cache_key)
    if isinstance(cached, list):
        _record_product_filter_cache_hit(state, "keyword_synonym")
        return list(cached)

    try:
        _record_product_filter_llm_call(state, "keyword_synonym")
        result = call_json(
            prompt=f"文本：\n{merged}\n\n请按要求输出关键词和同义词 JSON。",
            system_prompt=KEYWORD_SYNONYM_EXTRACTION_PROMPT,
            temperature=0.1,
        )
        cleaned = _clean_keyword_list(result.get("keywords", []) if isinstance(result, dict) else [])
        if cleaned:
            synonyms_mapping = result.get("synonyms", {}) if isinstance(result, dict) else {}
            all_keywords = _merge_keywords_and_synonyms(cleaned, synonyms_mapping)
            print(f"原始关键词: {cleaned}")
            print(f"同义词映射: {synonyms_mapping}")
            print(f"最终关键词集合: {all_keywords}")
            cache["keyword_synonym"][cache_key] = all_keywords
            return all_keywords
    except Exception as e:
        print(f"关键词同义词合并抽取失败: {e}")

    try:
        _record_product_filter_llm_call(state, "keyword_synonym")
        result = call_json(
            prompt=f"文本：\n{merged}\n\n请按要求输出关键词 JSON。",
            system_prompt=KEYWORD_EXTRACTION_PROMPT,
            temperature=0.1,
        )
        cleaned = _clean_keyword_list(result.get("keywords", []) if isinstance(result, dict) else [])

        if cleaned:
            synonyms_mapping = _generate_synonyms(cleaned, state)
            all_keywords = _merge_keywords_and_synonyms(cleaned, synonyms_mapping)
            print(f"原始关键词: {cleaned}")
            print(f"同义词映射: {synonyms_mapping}")
            print(f"最终关键词集合: {all_keywords}")
            cache["keyword_synonym"][cache_key] = all_keywords
            return all_keywords
    except Exception as e:
        print(f"关键词抽取兜底失败: {e}")
        pass

    fallback_keywords = _tokenize_text(merged)
    cache["keyword_synonym"][cache_key] = fallback_keywords
    return fallback_keywords


def _extract_taboo_keywords(state: GiftRecommendationState) -> List[str]:
    slot = state.filled_slots.get("taboo")
    if not slot or slot.value is None:
        return []
    return _tokenize_text(str(slot.value))


def _extract_rejected_subcategories(state: GiftRecommendationState) -> List[str]:
    raw_value = getattr(state, "rejected_subcategories", []) or []
    if not isinstance(raw_value, list):
        return []

    result: List[str] = []
    seen = set()
    for item in raw_value:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if not item or item in seen:
            continue
        result.append(item)
        seen.add(item)
    return result


def _extract_excluded_product_ids(state: GiftRecommendationState) -> List[str]:
    return _normalize_product_id_list(getattr(state, "rejected_product_ids", []) or [])


def _normalize_product_id_list(product_ids: object) -> List[str]:
    if not isinstance(product_ids, list):
        return []

    result: List[str] = []
    seen = set()
    for product_id in product_ids:
        normalized = str(product_id or "").strip()
        if not normalized or normalized in seen:
            continue
        result.append(normalized)
        seen.add(normalized)
    return result


def _apply_product_exclusions(
    products: List[ProductCandidate],
    taboo_keywords: List[str],
    rejected_subcategories: List[str],
    excluded_product_ids: Optional[List[str]] = None,
) -> List[ProductCandidate]:
    excluded_product_id_set = set(_normalize_product_id_list(excluded_product_ids or []))
    if not taboo_keywords and not rejected_subcategories and not excluded_product_id_set:
        return list(products)

    filtered: List[ProductCandidate] = []
    for product in products:
        if str(getattr(product, "sku_id", "") or "").strip() in excluded_product_id_set:
            continue
        if taboo_keywords and _match_keywords(taboo_keywords, product_search_text(product)):
            continue
        if _is_rejected_subcategory_product(product, rejected_subcategories):
            continue
        filtered.append(product)
    return filtered


def _is_rejected_subcategory_product(
    product: ProductCandidate,
    rejected_subcategories: List[str],
) -> bool:
    if not rejected_subcategories:
        return False

    product_small_category = _normalize_text(getattr(product, "small_category", "") or "")
    if any(product_small_category == _normalize_text(category) for category in rejected_subcategories):
        return True

    search_text = _normalize_text(product_search_text(product))
    return any(_normalize_text(category) in search_text for category in rejected_subcategories)


def _tokenize_text(text: str) -> List[str]:
    tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", text)
    return [token.lower() for token in tokens if token.strip()]


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _match_keywords(keywords: List[str], text: str) -> bool:
    if not keywords:
        return False
    normalized_text = _normalize_text(text)
    return any(keyword in normalized_text for keyword in keywords)


def _build_product_cards(products: List[ProductCandidate]) -> List[dict]:
    return [_convert_product_to_card(p) for p in products]


def _convert_product_to_card(product: ProductCandidate) -> dict:
    price_value = getattr(product, "fixed_sku_price", None)
    parsed_price = _parse_price(price_value)

    if parsed_price is None:
        parsed_price = getattr(product, "price", 0)

    return {
        "productId": str(product.sku_id),
        "productPic": getattr(product, "show_pic", None) or "",
        "productName": getattr(product, "sku_name", None) or product.name,
        "payPrice": _format_price_str(parsed_price),
        "purchaseType": "1",
        "merchantId": getattr(product, "company_id", None) or "",
        "discount": [],
        "showStrategy": False,
        "displayReason": _build_display_reason(product),
    }


def _build_display_reason(product: ProductCandidate) -> str:
    text = " ".join(
        str(value or "")
        for value in [
            getattr(product, "name", ""),
            getattr(product, "sku_name", ""),
            getattr(product, "category", ""),
            getattr(product, "subcategory", ""),
            getattr(product, "mid_category", ""),
            getattr(product, "small_category", ""),
            getattr(product, "description", ""),
        ]
    )

    if any(keyword in text for keyword in ("通勤", "旅行", "出行", "斜挎", "手提", "双肩")):
        return "适合日常使用"
    if any(keyword in text for keyword in ("保湿", "修护", "舒敏", "敏感肌")):
        return "偏基础护理"
    if any(keyword in text for keyword in ("香水", "香氛", "香薰")):
        return "送礼氛围感强"
    if any(keyword in text for keyword in ("礼盒", "套装", "组合")):
        return "礼赠形式完整"
    if any(keyword in text for keyword in ("茶", "咖啡", "酒", "巧克力", "坚果")):
        return "适合作为心意礼"
    return "符合当前筛选方向"


def _format_price_str(price: Optional[float]) -> str:
    if price is None:
        return ""
    if float(price).is_integer():
        return str(int(price))
    return f"{price:.2f}".rstrip("0").rstrip(".")

def _parse_first_pic(pic_value: Optional[str]) -> Optional[str]:
    """从 pic_list 字符串中解析出第一张图片的 URL"""
    if not pic_value:
        return None
    
    pic_str = str(pic_value).strip()
    if not pic_str or pic_str.upper() == "NULL":
        return None
    
    # 尝试解析 JSON list
    try:
        parsed = json.loads(pic_str)
        if isinstance(parsed, list) and len(parsed) > 0:
            first = str(parsed[0]).strip()
            return first if first else None
    except (json.JSONDecodeError, ValueError):
        pass
    
    # 尝试按逗号分隔
    if "," in pic_str:
        first = pic_str.split(",")[0].strip()
        return first if first else None
    
    # 单个 URL
    return pic_str if pic_str else None
