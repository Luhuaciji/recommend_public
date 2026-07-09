from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .category_selection import (
    MID_CATEGORY_TO_BIG_CATEGORY_MAP,
    SMALL_TO_MID_CATEGORY_MAP,
    apply_mid_category_selection,
    apply_subcategory_selection,
    subcategory_keyword_map,
)
from .category_catalog import (
    get_complete_mid_to_big_category_map,
    get_complete_small_to_mid_category_map,
)
from .feature_extraction import feature_extraction
from .llm_client import call_json
from .models import GiftRecommendationState, ProductCandidate
from .product_filtering import _load_products_from_csv, product_search_text


DIRECT_SEARCH_CONFIDENCE_THRESHOLD = 0.75
DIRECT_SEARCH_MAX_RECALL = 8
DIRECT_SEARCH_MAX_RESULTS = 10
COMPLETE_SMALL_TO_MID_CATEGORY_MAP = get_complete_small_to_mid_category_map(
    SMALL_TO_MID_CATEGORY_MAP,
)
COMPLETE_MID_TO_BIG_CATEGORY_MAP = get_complete_mid_to_big_category_map(
    MID_CATEGORY_TO_BIG_CATEGORY_MAP,
)


@dataclass
class DirectProductDetectionResult:
    matched: bool = False
    product_query: str = ""
    category_hint: str = ""
    confidence: float = 0.0
    reason: str = ""
    user_text: str = ""
    recall_candidates: List[ProductCandidate] = field(default_factory=list)

CATALOG_LEVEL_REQUEST_ALIASES = {
    "相机": ("照相机", "智能摄像/运动相机"),
    "摄像机": ("照相机", "智能摄像/运动相机"),
    "运动相机": ("智能摄像/运动相机",),
}
CATALOG_LEVEL_REQUEST_SIGNALS = (
    "推荐",
    "入门级",
    "适合",
    "买个",
    "买一个",
    "送个",
    "送一个",
    "想给",
    "看看",
)

DIRECT_PRODUCT_DETECTION_SYSTEM_PROMPT = """你是送礼流程入口的商品名识别器。
你的任务是判断：用户这句话是否已经明显在找“具体商品”，而不是泛泛的品类需求。

判定为 true 的典型情况：
1. 明确说出品牌+系列/款名/型号，例如“YSL自由至上香水”“茵芙莎自律美肌液5”
2. 明确说出比较完整的商品名，例如“萌猫戏雪保温杯”“金燕耳80g双罐装礼盒”
3. 明确说出品牌+品类（不论品类大小），例如“珀莱雅的面膜”“迪奥的香水”“兰蔻的粉底液”“戴森的吹风机”
4. 明确说出具体的商品小类（即使无品牌也可以直接搜商品），例如"防晒霜""精华液""洗面奶""保温杯""机械键盘""燕窝礼盒"

判定为 false 的典型情况：
1. 品类太宽泛模糊，无法直接搜商品，例如”护肤品””美妆””数码产品””送个礼物””买点喝的”
2. 只是说送礼需求，没有具体商品指向，例如”给妈妈买个礼物””预算500左右偏保健”
3. 只是说偏好、预算、对象、禁忌、场景
4. 只说品牌名但完全没有说买什么类型的商品，例如”想买YSL的””看看兰蔻”
5. 用户用”或者””或””还是””要么”等并列多个品类供选择，例如”香水或者口红””精华液还是面霜””送手表或皮带”——这类应走品类推荐流程而非直接搜商品。注意：如果并列的是具体品牌+商品名（如”YSL自由至上或者黑鸦片”），则仍可判 true

请结合召回候选一起判断，但不要因为“能召回到商品”就误判为 true。
如果不够确定，优先返回 false。

只返回 JSON，格式如下：
{
  "is_explicit_product": true,
  "product_query": "YSL自由至上香水",
  "category_hint": "香水",
  "confidence": 0.0,
  "reason": "一句话说明"
}

category_hint 字段说明：
- 用 1-3 个词补全用户隐含的商品品类/类别，用于后续品类过滤
- 例如："一瓶茅台" → "白酒/国酒"、"茅台酒" → "白酒/国酒"、"迪奥的香水" → "香水"、"珀莱雅的面膜" → "面膜"
- 只填该商品最直接对应的品类关键词，便于在商品库的 mid_category / small_category 字段做包含匹配
- 不确定时留空字符串即可
"""

_QUERY_NOISE_PATTERNS = [
    r"我想给[^，。！？\s]{0,8}(买|选|找)",
    r"帮我给[^，。！？\s]{0,8}(买|选|找)",
    r"给[^，。！？\s]{0,8}(买|选|找)",
    r"我想",
    r"帮我",
    r"想买",
    r"想找",
    r"想送",
    r"买个",
    r"买一?个",
    r"送个",
    r"送一?个",
    r"推荐一?下",
    r"推荐",
    r"看看",
    r"有没有",
]


def detect_direct_product_query(user_text: str) -> DirectProductDetectionResult:
    text = (user_text or "").strip()
    if not text:
        return DirectProductDetectionResult(user_text=text)
    if _looks_like_catalog_level_request(text):
        return DirectProductDetectionResult(user_text=text)

    recall_candidates = _recall_candidate_products(text, limit=DIRECT_SEARCH_MAX_RECALL)
    if not recall_candidates:
        return DirectProductDetectionResult(user_text=text)

    decision = _detect_explicit_product_query(text, recall_candidates)
    if not decision:
        return DirectProductDetectionResult(
            user_text=text,
            recall_candidates=recall_candidates,
        )

    try:
        confidence = float(decision.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    if (
        not bool(decision.get("is_explicit_product", False))
        or confidence < DIRECT_SEARCH_CONFIDENCE_THRESHOLD
    ):
        return DirectProductDetectionResult(
            confidence=confidence,
            reason=str(decision.get("reason", "") or "").strip(),
            user_text=text,
            recall_candidates=recall_candidates,
        )

    product_query = str(decision.get("product_query", "") or "").strip() or text
    category_hint = str(decision.get("category_hint", "") or "").strip()
    return DirectProductDetectionResult(
        matched=True,
        product_query=product_query,
        category_hint=category_hint,
        confidence=confidence,
        reason=str(decision.get("reason", "") or "").strip(),
        user_text=text,
        recall_candidates=recall_candidates,
    )


def apply_direct_product_detection(
    state: GiftRecommendationState,
    detection: Optional[DirectProductDetectionResult],
    *,
    slots_already_extracted: bool = False,
) -> Optional[Tuple[str, List[str]]]:
    if detection is None or not detection.matched:
        return None

    text = (detection.user_text or detection.product_query or "").strip()
    if not text:
        return None

    product_query = (detection.product_query or text).strip()
    category_hint = (detection.category_hint or "").strip()

    if not slots_already_extracted:
        feature_extraction(state, text)
    matched_products = _search_products_by_query(state, product_query, category_hint=category_hint)

    if matched_products:
        _sync_selected_category_from_direct_search(
            state,
            product_query=product_query,
            category_hint=category_hint,
            matched_products=matched_products,
        )
        state.filtered_products = matched_products
        try:
            state.final_product_cards = [_convert_product_to_card(p) for p in matched_products]
        except Exception:
            state.final_product_cards = []

        q_clean = product_query
        for token in ("的", "鐨?"):
            q_clean = q_clean.replace(token, "")
        q_clean = q_clean.strip()
        has_match = any(
            q_clean[:i] in (p.name or '') or q_clean[:i] in product_search_text(p)
            for p in matched_products
            for i in range(len(q_clean), 1, -1)
        )
        if not has_match:
            msg = (
                f"商品库中暂未找到“{product_query}”的完全匹配商品，"
                f"以下是与您搜索相关的其他商品，供参考："
            )
        else:
            msg = f"小Q猜您想找“{product_query}”，为您找到了这些商品："
        return ("done", [msg])

    state.filtered_products = []
    state.final_product_cards = []
    return (
        "await_need",
        [
            f"小Q猜您想找“{product_query}”，但当前商品库里没有找到符合条件的结果。"
            "您可以补充预算、对象或偏好，我再帮您找相似替代。"
        ],
    )


def try_direct_product_search(
    state: GiftRecommendationState,
    user_text: str,
) -> Optional[Tuple[str, List[str]]]:
    detection = detect_direct_product_query(user_text)
    return apply_direct_product_detection(state, detection)


def _looks_like_catalog_level_request(text: str) -> bool:
    if not text:
        return False
    if not any(signal in text for signal in CATALOG_LEVEL_REQUEST_SIGNALS):
        return False
    for alias, target_subcategories in CATALOG_LEVEL_REQUEST_ALIASES.items():
        if alias not in text:
            continue
        if any(subcategory in COMPLETE_SMALL_TO_MID_CATEGORY_MAP for subcategory in target_subcategories):
            return True
    return False


def _sync_selected_category_from_direct_search(
    state: GiftRecommendationState,
    product_query: str,
    category_hint: str,
    matched_products: List[ProductCandidate],
) -> None:
    direct_text = f"{product_query or ''} {category_hint or ''}"

    for keyword, small_category in sorted(
        subcategory_keyword_map.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if keyword and keyword in direct_text and small_category in COMPLETE_SMALL_TO_MID_CATEGORY_MAP:
            apply_subcategory_selection(
                state,
                small_category,
                selection_reason=f"Direct product search matched small category: {small_category}",
                description=f"Matched from direct query/category hint: {direct_text.strip()}",
            )
            print(f"[直接搜索品类同步] small_category={small_category}")
            return

    top_product = matched_products[0] if matched_products else None
    if top_product is None:
        return

    small_category = str(getattr(top_product, "small_category", "") or "").strip()
    if small_category in COMPLETE_SMALL_TO_MID_CATEGORY_MAP:
        apply_subcategory_selection(
            state,
            small_category,
            selection_reason=f"Direct product search result small category: {small_category}",
            description=f"Matched from top product small category: {small_category}",
        )
        print(f"[直接搜索品类同步] small_category={small_category}")
        return

    mid_category = str(getattr(top_product, "mid_category", "") or "").strip()
    if mid_category in COMPLETE_MID_TO_BIG_CATEGORY_MAP:
        apply_mid_category_selection(
            state,
            mid_category,
            selection_reason=f"Direct product search result mid category: {mid_category}",
            description=f"Matched from top product mid category: {mid_category}",
        )
        print(f"[直接搜索品类同步] mid_category={mid_category}")


def _detect_explicit_product_query(
    user_text: str,
    recall_candidates: List[ProductCandidate],
) -> Optional[Dict[str, Any]]:
    prompt = _build_detection_prompt(user_text, recall_candidates)
    try:
        result = call_json(
            prompt=prompt,
            system_prompt=DIRECT_PRODUCT_DETECTION_SYSTEM_PROMPT,
            temperature=0.1,
        )
    except Exception:
        return None

    if not isinstance(result, dict):
        return None
    return result


def _build_detection_prompt(
    user_text: str,
    recall_candidates: List[ProductCandidate],
) -> str:
    candidates_payload = []
    for product in recall_candidates:
        candidates_payload.append(
            {
                "product_name": getattr(product, "sku_name", None) or product.name,
                "brand": product.brand or "",
                "mid_category": getattr(product, "mid_category", "") or "",
                "small_category": getattr(product, "small_category", "") or "",
                "price": product.price,
            }
        )

    return (
        f"用户输入：{user_text}\n\n"
        f"规则召回候选：\n{candidates_payload}\n\n"
        "请判断这句话是否是在找具体商品。"
    )


def _search_products_by_query(
    state: GiftRecommendationState,
    product_query: str,
    category_hint: str = "",
    limit: int = DIRECT_SEARCH_MAX_RESULTS,
) -> List[ProductCandidate]:
    candidates = _recall_candidate_products(product_query, limit=max(limit * 3, 20))
    if not candidates:
        return []

    budget_min, budget_max = _get_budget_range(state)
    taboo_keywords = _extract_taboo_keywords(state)

    filtered: List[ProductCandidate] = []
    for product in candidates:
        if budget_min is not None and product.price < budget_min:
            continue
        if budget_max is not None and product.price > budget_max:
            continue
        if taboo_keywords and _contains_any_keyword(product_search_text(product), taboo_keywords):
            continue
        if not _matches_category_hint(product, category_hint):
            continue
        filtered.append(product)

    return filtered[:limit]


def _recall_candidate_products(
    query_text: str,
    limit: int,
) -> List[ProductCandidate]:
    normalized_query = _normalize_text(_strip_query_noise(query_text))
    if not normalized_query:
        normalized_query = _normalize_text(query_text)
    if not normalized_query:
        return []

    query_tokens = _tokenize_query(normalized_query)
    scored: List[Tuple[int, float, ProductCandidate]] = []
    for product in _load_products_from_csv():
        score = _score_product_match(product, normalized_query, query_tokens)
        if score <= 0:
            continue
        scored.append((score, product.price, product))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [product for _, _, product in scored[:limit]]


def _score_product_match(
    product: ProductCandidate,
    normalized_query: str,
    query_tokens: List[str],
) -> int:
    # name_text = _normalize_text(getattr(product, "sku_name", None) or product.name)
    name_text = _normalize_text(
        f"{product.brand or ''} {getattr(product, 'sku_name', None) or product.name} "
        f"{getattr(product, 'mid_category', '') or ''} {getattr(product, 'small_category', '') or ''}"
    )
    search_text = _normalize_text(product_search_text(product))
    compact_query = _normalize_compact_text(normalized_query)
    compact_name = _normalize_compact_text(name_text)
    compact_search = _normalize_compact_text(search_text)
    if not name_text and not search_text:
        return 0

    score = 0
    if normalized_query == name_text or (compact_query and compact_query == compact_name):
        score += 220
    elif normalized_query and normalized_query in name_text:
        score += 160
    elif compact_query and compact_query in compact_name:
        score += 160
    elif normalized_query and normalized_query in search_text:
        score += 100
    elif compact_query and compact_query in compact_search:
        score += 100

    expanded_tokens = _expand_query_terms(query_tokens)
    for token in expanded_tokens:
        if len(token) < 2:
            continue
        compact_token = _normalize_compact_text(token)
        if token in name_text or (compact_token and compact_token in compact_name):
            score += 24
        elif token in search_text or (compact_token and compact_token in compact_search):
            score += 10

    if not score:
        return 0

    # Too many broad hits usually indicate a generic category term rather than a
    # concrete product name; keep a small base score so LLM can still reject it.
    return score


def _strip_query_noise(text: str) -> str:
    cleaned = str(text or "").strip()
    for pattern in _QUERY_NOISE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[，。！？、,.!?\-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _tokenize_query(text: str) -> List[str]:
    tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", text)
    cleaned: List[str] = []
    seen = set()
    for token in tokens:
        token_norm = token.strip().lower()
        if not token_norm or token_norm in seen:
            continue
        seen.add(token_norm)
        cleaned.append(token_norm)
    return cleaned


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _normalize_compact_text(text: str) -> str:
    normalized = _normalize_text(text)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)


def _expand_query_terms(tokens: List[str]) -> List[str]:
    expanded: List[str] = []
    seen = set()

    def add_term(term: str) -> None:
        term_norm = term.strip().lower()
        if len(term_norm) < 2 or term_norm in seen:
            return
        seen.add(term_norm)
        expanded.append(term_norm)

    for token in tokens:
        add_term(token)

        chinese_parts = re.findall(r"[\u4e00-\u9fff]+", token)
        for part in chinese_parts:
            if len(part) >= 4:
                add_term(part[:4])
                add_term(part[-4:])
            if len(part) >= 3:
                for size in (4, 3, 2):
                    if len(part) < size:
                        continue
                    for index in range(0, len(part) - size + 1):
                        add_term(part[index : index + size])

        alpha_numeric_parts = re.findall(r"[a-z0-9]+", token.lower())
        for part in alpha_numeric_parts:
            add_term(part)

    return expanded


def _get_budget_range(
    state: GiftRecommendationState,
) -> Tuple[Optional[float], Optional[float]]:
    budget_min = _coerce_float(_get_slot_value(state, "budget_min"))
    budget_max = _coerce_float(_get_slot_value(state, "budget_max"))
    return budget_min, budget_max


def _get_slot_value(state: GiftRecommendationState, slot_name: str) -> Any:
    slot = state.filled_slots.get(slot_name)
    if not slot:
        return None
    return getattr(slot, "value", None)


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_taboo_keywords(state: GiftRecommendationState) -> List[str]:
    taboo_value = _get_slot_value(state, "taboo")
    if taboo_value is None:
        return []
    return _tokenize_query(str(taboo_value))


def _contains_any_keyword(text: str, keywords: List[str]) -> bool:
    normalized_text = _normalize_text(text)
    return any(keyword in normalized_text for keyword in keywords if keyword)


def _matches_category_hint(product: ProductCandidate, category_hint: str) -> bool:
    """品类模糊匹配：category_hint 的 token 是否命中产品的 mid_category 或 small_category。"""
    if not category_hint or not category_hint.strip():
        return True
    hint_tokens = _tokenize_query(category_hint)
    if not hint_tokens:
        return True
    category_text = _normalize_text(
        f"{getattr(product, 'mid_category', '') or ''} {getattr(product, 'small_category', '') or ''}"
    )
    if not category_text:
        return True
    return any(token in category_text for token in hint_tokens if token)


def _convert_product_to_card(product: ProductCandidate) -> Dict[str, Any]:
    price_value = getattr(product, "fixed_sku_price", None)
    parsed_price = _coerce_float(price_value)
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
    }


def _format_price_str(price: Optional[float]) -> str:
    if price is None:
        return ""
    if float(price).is_integer():
        return str(int(price))
    return f"{price:.2f}".rstrip("0").rstrip(".")
