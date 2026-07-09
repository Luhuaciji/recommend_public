from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


@dataclass
class GiftSlot:
    slot_name: str
    display_name: str
    value: Optional[Any] = None
    is_filled: bool = False
    priority: str = "high"


@dataclass
class ProductCategory:
    category_id: str
    category_name: str
    description: str
    selection_reason: str = ""


@dataclass
class ProductCandidate:
    sku_id: str
    name: str
    category: str
    price: float
    subcategory: Optional[str] = None
    brand: Optional[str] = None
    description: str = ""


@dataclass
class CategorySelectionResult:
    result_type: Literal[
        "direct_subcategory",
        "direct_mid_category",
        "recommend_list",
        "need_more_info",
    ]
    selected_category_name: str = ""
    recommended_categories: List[str] = field(default_factory=list)
    selection_reason: str = ""


@dataclass
class RouterDecision:
    next_action: Literal[
        "ask_need",
        "run_category_flow",
        "choose_pending_category",
        "re_prompt_choose_category",
        "ask_detail",
        "filter_products",
        "restart_flow",
        "exit_flow",
    ]
    selected_category: str = ""
    confidence: float = 0.0
    reason: str = ""


@dataclass
class GiftRecommendationState:
    user_id: str
    session_id: str
    account_id: str = ""
    member_profile: Dict[str, Any] = field(default_factory=dict)
    query_context: Dict[str, Any] = field(default_factory=dict)
    chat_history: List[Dict[str, str]] = field(default_factory=list)
    filled_slots: Dict[str, GiftSlot] = field(default_factory=dict)
    selected_category: Optional[ProductCategory] = None
    recommended_categories: List[str] = field(default_factory=list)
    detailed_dimensions: Dict[str, Any] = field(default_factory=dict)
    filtered_products: List[ProductCandidate] = field(default_factory=list)
    selected_subcategory: Optional[str] = None
    selected_mid_category: Optional[str] = None
    selected_big_category: Optional[str] = None
    category_level: str = ""
    inference_results: List[Dict[str, Any]] = field(default_factory=list)
    slot_inference_cache: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    last_slot_inference_state_hash: str = ""
    final_product_cards: List[Dict[str, Any]] = field(default_factory=list)
    candidate_products: List[ProductCandidate] = field(default_factory=list)
    seen_product_ids: List[str] = field(default_factory=list)
    rejected_product_ids: List[str] = field(default_factory=list)
    last_recommended_product_ids: List[str] = field(default_factory=list)
    last_recommended_products_snapshot: List[Dict[str, Any]] = field(default_factory=list)
    candidate_pool_summary: Dict[str, Any] = field(default_factory=dict)
    candidate_pool_reason: str = ""
    downgrade_retry_triggered: bool = False
    downgrade_retry_reason: str = ""
    current_turn_slot_updates: Dict[str, Any] = field(default_factory=dict)
    turn_understanding: Dict[str, Any] = field(default_factory=dict)


HIGH_PRIORITY_SLOTS = [
    "recipient_relation",
    "occasion",
    "budget_min",
    "budget_max",
    "recipient_preferences",
]

PRODUCT_CATEGORIES = [
    {"id": "护肤", "name": "护肤", "description": "面部护肤、男士护肤、儿童护肤等"},
    {"id": "美妆", "name": "美妆", "description": "面部彩妆、美妆工具等"},
    {"id": "香氛", "name": "香氛", "description": "香水香氛等"},
    {"id": "个护清洁", "name": "个护清洁", "description": "美发护发、口腔护理、身体护理、女性护理、个护电器、儿童洗护用品等"},
    {"id": "家庭清洁", "name": "家庭清洁", "description": "家庭清洁、纸品清洗等"},
    {"id": "服装（男女/内衣/童装）", "name": "服装（男女/内衣/童装）", "description": "女装（含中性）、男装、内衣、儿童服饰等"},
    {"id": "鞋靴", "name": "鞋靴", "description": "女鞋、男鞋等"},
    {"id": "箱包出行", "name": "箱包出行", "description": "功能箱包、男包、女包（含中性）等"},
    {"id": "旅行用品", "name": "旅行用品", "description": "旅行用品等"},
    {"id": "配饰（钟表/眼镜/珠宝）", "name": "配饰（钟表/眼镜/珠宝）", "description": "时尚配饰、服配、腕表、眼镜、黄金珠宝等"},
    {"id": "母婴", "name": "母婴", "description": "婴儿喂养用品、儿童家纺、玩具等"},
    {"id": "文具", "name": "文具", "description": "文具等"},
    {"id": "家居与厨房", "name": "家居与厨房", "description": "品质生活、厨房小电等"},
    {"id": "食品与冲饮（非酒）", "name": "食品与冲饮（非酒）", "description": "粮油调味速食、海鲜水产、休闲食品、咖啡冲饮、茗茶等"},
    {"id": "酒类", "name": "酒类", "description": "葡萄酒、洋酒、国酒等"},
    {"id": "数码影音", "name": "数码影音", "description": "手机通讯、影音娱乐等"},
    {"id": "营养保健（滋补/维矿/功能健康）", "name": "营养保健（滋补/维矿/功能健康）", "description": "体重管理、调节三高、骨骼健康、维生素/矿物质、运动营养、其他滋补品、滋补贵细、参茸制品、其他营养健康、健康理疗等"},
    {"id": "宠物", "name": "宠物", "description": "宠物医疗保健、宠物玩具等"},
    {"id": "礼赠/营销", "name": "礼赠/营销", "description": "礼盒礼袋、推广商品等"},
]
