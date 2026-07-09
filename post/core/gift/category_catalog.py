from __future__ import annotations

import csv
import os
from functools import lru_cache
from typing import Dict, Iterable, List, Optional


def get_complete_small_to_mid_category_map(
    base_small_to_mid_map: Optional[Dict[str, str]] = None,
    valid_mid_categories: Optional[Iterable[str]] = None,
) -> Dict[str, str]:
    complete_map: Dict[str, str] = {}
    if base_small_to_mid_map:
        complete_map.update(
            {
                str(small).strip(): str(mid).strip()
                for small, mid in base_small_to_mid_map.items()
                if str(small).strip() and str(mid).strip()
            }
        )

    valid_mid_set = {
        str(category).strip()
        for category in (valid_mid_categories or [])
        if str(category).strip()
    }
    for small_category, mid_category in _load_csv_small_to_mid_category_map().items():
        if valid_mid_set and mid_category not in valid_mid_set:
            continue
        complete_map.setdefault(small_category, mid_category)
    return complete_map


def get_mid_to_small_category_map(
    small_to_mid_map: Dict[str, str],
) -> Dict[str, List[str]]:
    mid_to_small_map: Dict[str, List[str]] = {}
    for small_category, mid_category in small_to_mid_map.items():
        if not small_category or not mid_category:
            continue
        mid_to_small_map.setdefault(mid_category, [])
        if small_category not in mid_to_small_map[mid_category]:
            mid_to_small_map[mid_category].append(small_category)
    return mid_to_small_map


def get_complete_mid_to_big_category_map(
    base_mid_to_big_map: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    complete_map: Dict[str, str] = {}
    if base_mid_to_big_map:
        complete_map.update(
            {
                str(mid).strip(): str(big).strip()
                for mid, big in base_mid_to_big_map.items()
                if str(mid).strip() and str(big).strip()
            }
        )

    for mid_category, big_category in _load_csv_mid_to_big_category_map().items():
        complete_map.setdefault(mid_category, big_category)
    return complete_map


def build_mid_category_candidate_text(
    mid_to_big_map: Dict[str, str],
    excluded_mid_categories: Optional[Iterable[str]] = None,
    max_items: int = 220,
) -> str:
    excluded_mid_category_set = {
        str(category).strip()
        for category in (excluded_mid_categories or [])
        if str(category).strip()
    }
    lines: List[str] = []
    for mid_category, big_category in sorted(mid_to_big_map.items(), key=lambda item: (item[1], item[0])):
        if mid_category in excluded_mid_category_set:
            continue
        lines.append(f"- {mid_category} -> {big_category}")
        if len(lines) >= max_items:
            break
    return "\n".join(lines) if lines else "(无)"


def build_small_category_candidate_text(
    small_to_mid_map: Dict[str, str],
    excluded_subcategories: Optional[Iterable[str]] = None,
    excluded_mid_categories: Optional[Iterable[str]] = None,
    max_items: int = 350,
) -> str:
    excluded_subcategory_set = {
        str(category).strip()
        for category in (excluded_subcategories or [])
        if str(category).strip()
    }
    excluded_mid_category_set = {
        str(category).strip()
        for category in (excluded_mid_categories or [])
        if str(category).strip()
    }
    lines: List[str] = []
    for small_category, mid_category in sorted(small_to_mid_map.items(), key=lambda item: (item[1], item[0])):
        if small_category in excluded_subcategory_set or mid_category in excluded_mid_category_set:
            continue
        lines.append(f"- {small_category} -> {mid_category}")
        if len(lines) >= max_items:
            break
    return "\n".join(lines) if lines else "(无)"


@lru_cache(maxsize=1)
def _load_csv_mid_to_big_category_map() -> Dict[str, str]:
    csv_path = _resolve_catalog_path()
    if not csv_path:
        return {}

    mid_to_big_map: Dict[str, str] = {}
    try:
        with open(csv_path, "r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                big_category = _clean_category_value(
                    row.get("lefox_category_name_lvl_01")
                    or row.get("category_name_lvl_01")
                    or row.get("category_name")
                )
                mid_category = _clean_category_value(
                    row.get("lefox_category_name_lvl_02")
                    or row.get("category_name_lvl_02")
                )
                if not mid_category or not big_category:
                    continue
                mid_to_big_map.setdefault(mid_category, big_category)
    except Exception as exc:
        print(f"加载完整中类映射失败: {exc}")
        return {}
    return mid_to_big_map


@lru_cache(maxsize=1)
def _load_csv_small_to_mid_category_map() -> Dict[str, str]:
    csv_path = _resolve_catalog_path()
    if not csv_path:
        return {}

    small_to_mid_map: Dict[str, str] = {}
    try:
        with open(csv_path, "r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                mid_category = _clean_category_value(
                    row.get("lefox_category_name_lvl_02")
                    or row.get("category_name_lvl_02")
                )
                small_category = _clean_category_value(
                    row.get("lefox_category_name_lvl_03")
                    or row.get("category_name_lvl_03")
                )
                if not small_category or not mid_category:
                    continue
                small_to_mid_map.setdefault(small_category, mid_category)
    except Exception as exc:
        print(f"加载完整小类映射失败: {exc}")
        return {}
    return small_to_mid_map


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


def _clean_category_value(value: Optional[str]) -> str:
    cleaned = str(value or "").strip()
    if not cleaned or cleaned.upper() == "NULL":
        return ""
    return cleaned
