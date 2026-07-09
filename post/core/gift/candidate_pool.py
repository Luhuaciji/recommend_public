from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from .models import GiftRecommendationState, ProductCandidate
from .product_filtering import (
    _apply_category_filter_with_fallback,
    _build_product_cards,
    _extract_taboo_keywords,
    _extract_user_keywords,
    _keyword_score,
    _load_products_from_csv,
    _match_keywords,
    _rank_by_keyword_score,
    _resolve_selected_categories,
    product_search_text,
)


MAX_CANDIDATE_POOL_SIZE = 50
MAX_ROUTER_TOP_PRODUCTS = 5


def update_candidate_pool(
    state: GiftRecommendationState,
    user_text: str,
    stage: str,
    pending_categories: Optional[List[str]] = None,
) -> GiftRecommendationState:
    """
    Maintain a lightweight rolling product candidate pool before routing.

    The pool is process state, not final recommendations. It gives the router
    enough evidence to skip rigid category steps when the current products are
    already concentrated enough.
    """
    text = (user_text or "").strip()
    if not text:
        _refresh_candidate_summary(state, stage=stage, reason="empty_user_text")
        return state

    catalog = _load_products_from_csv()
    if not catalog:
        state.candidate_products = []
        state.candidate_pool_summary = {
            "count": 0,
            "confidence": 0.0,
            "stage": stage,
            "reason": "catalog_empty",
        }
        state.candidate_pool_reason = "catalog_empty"
        return state

    budget_min = _get_budget_value(state, "budget_min")
    budget_max = _get_budget_value(state, "budget_max")
    taboo_keywords = _extract_taboo_keywords(state)
    selected_small_category, selected_mid_category = _resolve_selected_categories(
        state,
        text,
    )
    keywords = _extract_user_keywords(state, text)

    base_products = _apply_basic_filters(
        catalog,
        budget_min=budget_min,
        budget_max=budget_max,
        taboo_keywords=taboo_keywords,
    )
    category_products = _apply_category_filter_with_fallback(
        base_products,
        selected_small_category=selected_small_category,
        selected_mid_category=selected_mid_category,
    )

    ranked_from_catalog = _rank_by_keyword_score(category_products, keywords)
    ranked_from_existing = _rank_existing_pool(
        state.candidate_products,
        keywords,
        budget_min=budget_min,
        budget_max=budget_max,
        taboo_keywords=taboo_keywords,
    )

    merged = _merge_ranked_products(
        ranked_from_existing,
        ranked_from_catalog,
        keywords=keywords,
    )
    state.candidate_products = merged[:MAX_CANDIDATE_POOL_SIZE]
    _refresh_candidate_summary(
        state,
        stage=stage,
        reason="updated",
        matched_signals=keywords[:12],
        pending_categories=pending_categories or [],
    )
    return state


def _apply_basic_filters(
    products: List[ProductCandidate],
    budget_min: Optional[float],
    budget_max: Optional[float],
    taboo_keywords: List[str],
) -> List[ProductCandidate]:
    filtered: List[ProductCandidate] = []
    for product in products:
        if budget_min is not None and product.price < budget_min:
            continue
        if budget_max is not None and product.price > budget_max:
            continue
        if taboo_keywords and _match_keywords(taboo_keywords, product_search_text(product)):
            continue
        filtered.append(product)
    return filtered


def _rank_existing_pool(
    products: List[ProductCandidate],
    keywords: List[str],
    budget_min: Optional[float],
    budget_max: Optional[float],
    taboo_keywords: List[str],
) -> List[ProductCandidate]:
    if not products:
        return []
    filtered = _apply_basic_filters(
        products,
        budget_min=budget_min,
        budget_max=budget_max,
        taboo_keywords=taboo_keywords,
    )
    return _rank_by_keyword_score(filtered, keywords)


def _merge_ranked_products(
    existing_products: List[ProductCandidate],
    catalog_products: List[ProductCandidate],
    keywords: List[str],
) -> List[ProductCandidate]:
    product_map: Dict[str, Tuple[int, float, ProductCandidate]] = {}

    def add_products(products: List[ProductCandidate], source_bonus: int) -> None:
        for product in products:
            sku_id = str(product.sku_id)
            score = _keyword_score(product, keywords) + source_bonus
            current = product_map.get(sku_id)
            if current is None or score > current[0]:
                product_map[sku_id] = (score, product.price, product)

    add_products(catalog_products, source_bonus=0)
    add_products(existing_products, source_bonus=1)

    ranked = sorted(product_map.values(), key=lambda item: (-item[0], item[1]))
    return [product for _, _, product in ranked]


def _refresh_candidate_summary(
    state: GiftRecommendationState,
    stage: str,
    reason: str,
    matched_signals: Optional[List[str]] = None,
    pending_categories: Optional[List[str]] = None,
) -> None:
    products = list(getattr(state, "candidate_products", []) or [])
    summary = build_candidate_pool_summary(
        products,
        stage=stage,
        reason=reason,
        matched_signals=matched_signals or [],
        pending_categories=pending_categories or [],
    )
    state.candidate_pool_summary = summary
    state.candidate_pool_reason = reason

    try:
        state.final_product_cards = _build_product_cards(products[:MAX_ROUTER_TOP_PRODUCTS])
    except Exception:
        state.final_product_cards = []


def build_candidate_pool_summary(
    products: List[ProductCandidate],
    stage: str = "",
    reason: str = "",
    matched_signals: Optional[List[str]] = None,
    pending_categories: Optional[List[str]] = None,
) -> Dict[str, Any]:
    products = list(products or [])
    if not products:
        return {
            "count": 0,
            "confidence": 0.0,
            "stage": stage,
            "reason": reason or "empty",
            "matched_signals": matched_signals or [],
            "pending_categories": pending_categories or [],
            "top_products": [],
            "top_mid_categories": [],
            "top_small_categories": [],
            "top_brands": [],
        }

    mid_counter = Counter(
        str(getattr(product, "mid_category", "") or product.category or "").strip()
        for product in products
    )
    small_counter = Counter(
        str(getattr(product, "small_category", "") or "").strip()
        for product in products
    )
    brand_counter = Counter(
        str(product.brand or "").strip()
        for product in products
    )

    top_products = [
        {
            "sku_id": product.sku_id,
            "name": getattr(product, "sku_name", None) or product.name,
            "price": product.price,
            "brand": product.brand or "",
            "mid_category": getattr(product, "mid_category", "") or "",
            "small_category": getattr(product, "small_category", "") or "",
        }
        for product in products[:MAX_ROUTER_TOP_PRODUCTS]
    ]

    prices = [product.price for product in products if product.price is not None]
    count = len(products)
    top_mid_count = mid_counter.most_common(1)[0][1] if mid_counter else 0
    top_brand_count = brand_counter.most_common(1)[0][1] if brand_counter else 0
    category_focus = top_mid_count / count if count else 0.0
    brand_focus = top_brand_count / count if count else 0.0

    confidence = min(
        0.95,
        0.25
        + (0.35 * category_focus)
        + (0.2 * brand_focus)
        + (0.2 if count <= 15 else 0.0),
    )

    return {
        "count": count,
        "confidence": round(confidence, 2),
        "stage": stage,
        "reason": reason or "updated",
        "matched_signals": matched_signals or [],
        "pending_categories": pending_categories or [],
        "price_range": {
            "min": min(prices) if prices else None,
            "max": max(prices) if prices else None,
        },
        "top_mid_categories": _counter_to_list(mid_counter),
        "top_small_categories": _counter_to_list(small_counter),
        "top_brands": _counter_to_list(brand_counter),
        "top_products": top_products,
    }


def _counter_to_list(counter: Counter) -> List[Dict[str, Any]]:
    return [
        {"name": name, "count": count}
        for name, count in counter.most_common(5)
        if name
    ]


def _get_budget_value(
    state: GiftRecommendationState,
    slot_name: str,
) -> Optional[float]:
    slot = state.filled_slots.get(slot_name)
    if not slot:
        return None
    value = getattr(slot, "value", None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
