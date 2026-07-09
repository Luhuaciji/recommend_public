import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .category_catalog import (
    build_mid_category_candidate_text,
    build_small_category_candidate_text,
    get_complete_mid_to_big_category_map,
    get_complete_small_to_mid_category_map,
)
from .llm_client import call_json
from .models import CategorySelectionResult, GiftRecommendationState, ProductCategory


MID_CATEGORY_TO_BIG_CATEGORY_MAP = {
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


MID_CATEGORY_SELECTION_SYSTEM_PROMPT = f"""
你是一个送礼助手，需要根据用户信息选择最合适的商品中类。

请根据提供的用户上下文信息，从下面给定的候选中类中选择最多三个最匹配的中类，并按匹配度从高到低排序。

### 候选中类
- 面部护肤
- 男士护肤
- 儿童护肤
- 面部彩妆
- 美妆工具
- 香水香氛
- 美发护发
- 口腔护理
- 身体护理
- 女性护理
- 个护电器
- 儿童洗护用品
- 家庭清洁
- 纸品清洗
- 女装（含中性）
- 男装
- 内衣
- 儿童服饰
- 女鞋
- 男鞋
- 功能箱包
- 男包
- 女包（含中性）
- 旅行用品
- 时尚配饰
- 服配
- 腕表
- 眼镜
- 黄金珠宝
- 婴儿喂养用品
- 儿童家纺
- 玩具
- 文具
- 品质生活
- 厨房小电
- 粮油调味速食
- 海鲜水产
- 休闲食品
- 咖啡冲饮
- 茗茶
- 葡萄酒
- 洋酒
- 国酒
- 手机通讯
- 影音娱乐
- 体重管理
- 调节三高
- 骨骼健康
- 维生素/矿物质
- 运动营养
- 其他滋补品
- 滋补贵细
- 参茸制品
- 其他营养健康
- 健康理疗
- 宠物医疗保健
- 宠物玩具
- 礼盒礼袋
- 推广商品

### 选择规则
1. 只能从上述候选中类中选择，返回值必须与候选中类名称完全一致
2. 最多返回3个中类，至少返回1个
3. 如果用户明确提到了某个具体品类（如手表香水白酒），只返回该品类对应的中类，不要添加无关的补充品类
4. 只有当用户需求确实模糊宽泛（如随便送个礼物），才返回2-3个差异化的中类供用户选择
5. 补充品类必须与用户表达的需求有直接关联（如用户提到手表不可补充护肤）
6. 优先结合收礼人关系、年龄、性别、喜好、预算、送礼场景等信息判断
7. 不要输出候选列表之外的中类名称
8. 必须严格返回JSON，不要输出任何额外内容

### 返回格式
{{
  "selected_category": ["面部护肤", "香水香氛", "面部彩妆"],
  "selection_reason": "基于用户关系、场景、预算与偏好综合判断，以上中类更适合作为礼物选择。"
}}
""".strip()

EXPLICIT_CATEGORY_RESOLVE_SYSTEM_PROMPT = """
你是送礼推荐系统的“显式品类识别器”。

任务：只判断用户最新输入是否明确指向某个商品中类或小类。

规则：
1. 只有用户明确说想要、想看、推荐、换成、送某类商品时，才返回匹配结果。
2. 如果用户只是描述收礼人、节日、预算、关系、年龄、性别、风格，不能强行推断小类。
3. 如果用户是否定表达，例如“不要口红”“不看相机”，不能把该品类作为正向目标。
4. 中类名称必须来自候选中类；小类名称必须来自候选小类，不能编造。
5. “相机”可以语义匹配到候选小类中的“照相机”“智能摄像/运动相机”等真实小类。
6. 如果多个小类都合理，返回 candidates，按置信度从高到低排列。
7. 如果无法判断，返回 matched=false。

严格返回 JSON：
{
  "matched": true,
  "match_type": "subcategory",
  "target_mid_category": "摄影摄像",
  "target_subcategory": "照相机",
  "candidates": [
    {"subcategory": "照相机", "mid_category": "摄影摄像", "confidence": 0.86}
  ],
  "confidence": 0.0,
  "reason": "一句话说明"
}
""".strip()

EXPLICIT_CATEGORY_LLM_CONFIDENCE_THRESHOLD = 0.78

UNIFIED_CATEGORY_DECISION_SYSTEM_PROMPT = """
你是送礼推荐系统的品类决策器。一次完成两个相关判断：
1. 判断用户最新输入是否明确指定了某个商品中类或小类。
2. 如果没有明确指定，则根据完整送礼上下文推荐最多三个候选中类。

显式品类规则：
1. 只有用户明确说想要、想看、推荐、换成、购买或赠送某类商品时，explicit_category.matched 才能为 true。
2. 只描述收礼人、节日、预算、关系、年龄、性别、风格时，不能猜测显式小类。
3. 否定或排除的品类不能作为正向目标。
4. 中类和小类必须来自用户 prompt 中提供的候选列表；不能编造。
5. 多个小类都合理时，在 candidates 中按置信度从高到低返回。

通用品类推荐规则：
1. selected_category 只能包含候选中类中的名称，最多三个。
2. 用户明确指定品类时，selected_category 应只包含该品类对应的中类。
3. 如果 explicit_category.matched=false，但用户仍是送礼意图，则 selected_category 必须返回 3 个最接近、最适合作为礼物的中类；不得返回空数组。
4. 必须遵守 prompt 中的中类和小类排除条件。
5. 如果用户提到的是自然语言品类或小类，例如“茶叶/护肤/彩妆”，必须映射到候选中的标准中类，例如“茗茶/面部护肤/面部彩妆”。
6. 不要把大类当作中类返回；返回名称必须与候选中类完全一致。

严格返回 JSON，不要输出额外内容：
{
  "explicit_category": {
    "matched": true,
    "match_type": "subcategory",
    "target_mid_category": "面部护肤",
    "target_subcategory": "面膜",
    "candidates": [
      {"subcategory": "面膜", "mid_category": "面部护肤", "confidence": 0.9}
    ],
    "confidence": 0.9,
    "reason": "一句话说明"
  },
  "selected_category": ["面部护肤"],
  "selection_reason": "一句话说明"
}

无法识别显式品类时，explicit_category.matched 必须为 false，其余显式字段使用空值；
只要用户仍是送礼意图，selected_category 必须返回 1-3 个中类，优先返回 3 个；只有明确不是送礼意图或退出送礼场景时才允许为空。
""".strip()

_NON_GIFT_MID_CATEGORIES = {
    "礼盒礼袋", "推广商品",
    "家庭清洁", "纸品清洗", "女性护理", "粮油调味速食",
}

MID_CATEGORY_ALIAS_MAP = {
    "茶叶": "茗茶",
    "茶": "茗茶",
    "绿茶": "茗茶",
    "红茶": "茗茶",
    "白茶": "茗茶",
    "乌龙茶": "茗茶",
    "护肤": "面部护肤",
    "护肤品": "面部护肤",
    " skincare": "面部护肤",
    "彩妆": "面部彩妆",
    "化妆品": "面部彩妆",
    "美妆": "面部彩妆",
    "口红": "面部彩妆",
    "唇膏": "面部彩妆",
    "粉底": "面部彩妆",
    "眼影": "面部彩妆",
    "香水": "香水香氛",
    "香氛": "香水香氛",
    "香薰": "香水香氛",
    "手表": "腕表",
    "表": "腕表",
    "首饰": "黄金珠宝",
    "饰品": "黄金珠宝",
    "珠宝": "黄金珠宝",
    "项链": "黄金珠宝",
    "戒指": "黄金珠宝",
    "包包": "女包（含中性）",
    "女包": "女包（含中性）",
    "行李箱": "功能箱包",
    "拉杆箱": "功能箱包",
    "箱包": "功能箱包",
    "咖啡": "咖啡冲饮",
    "咖啡豆": "咖啡冲饮",
    "咖啡粉": "咖啡冲饮",
    "冲饮": "咖啡冲饮",
}

EXTENDED_MID_CATEGORY_TRIGGERS = {
    "摄影摄像": ("相机", "拍照", "摄影", "摄像", "拍摄", "记录生活", "旅行拍照", "入门级"),
    "智能设备": ("运动相机", "智能摄像", "智能设备", "监控", "摄像头", "户外拍摄", "vlog"),
}

MID_CATEGORY_SELECTION_PROMPT_TEMPLATE = """
你是一个送礼助手，需要根据用户信息选择最合适的商品中类。

请根据提供的用户上下文信息，从本轮给定的候选中类中选择最多三个最匹配的中类，并按匹配度从高到低排序。

### 本轮候选中类
{candidate_mid_categories}

### 选择规则
1. 只能从本轮候选中类中选择，返回值必须与候选中类名称完全一致。
2. 最多返回3个中类，至少返回1个。
3. 如果用户明确提到了某个具体品类或兴趣用途，优先选择与该品类/用途直接相关的中类，不要退到更宽泛的近似中类。
4. 只有当用户需求确实模糊宽泛（如随便送个礼物），才返回2-3个差异化的中类供用户选择。
5. 补充品类必须与用户表达的需求有直接关联。
6. 优先结合收礼人关系、年龄、性别、喜好、预算、送礼场景等信息判断。
7. 不要输出候选列表之外的中类名称。
8. 必须严格返回JSON，不要输出任何额外内容。
9. 当用户明确提到某个品类偏好（如"爱喝咖啡"）时，只返回与该品类直接相关的中类（如"咖啡冲饮"），不要补充其他品类。优先该品类内选择具体商品。
10. 送礼应考虑礼品的体面性和仪式感。家庭清洁、纸品清洗、女性护理、粮油调味速食等日耗品类不适合作为礼物；礼盒礼袋、推广商品等包装/营销品类也不应作为独立礼物推荐。以上品类除非用户明确指定，否则不要选择。
11. 如果信息有限但仍是送礼需求，不要返回空数组；请返回3个最接近、最适合作为礼物的中类。

### 返回格式
{{
  "selected_category": ["面部护肤", "香水香氛", "面部彩妆"],
  "selection_reason": "基于用户关系、场景、预算与偏好综合判断，以上中类更适合作为礼物选择。"
}}
""".strip()


@dataclass
class ExplicitCategoryResolveResult:
    match_type: str = "none"
    subcategory: str = ""
    mid_category: str = ""
    candidate_subcategories: List[str] = field(default_factory=list)
    candidate_mid_categories: List[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = ""
    reason: str = ""


subcategory_keyword_map = {
    '餐具': '餐具',
    '燕窝': '燕窝',
    '保温杯': '保温杯/焖烧罐',
    '焖烧罐': '保温杯/焖烧罐',
    '男士护肤套装': '男士护肤套装',
    '白葡萄酒': '白葡萄酒',
    '拉杆箱': '拉杆箱',
    '蛋白粉（运动类）': '蛋白粉（运动类）',
    '儿童其他护肤': '儿童其他护肤',
    '戒指': '戒指',
    '脸部彩妆套装': '脸部彩妆套装',
    '其它面部护肤': '其它面部护肤',
    '身体护理套装': '身体护理套装',
    '代餐奶昔': '代餐奶昔',
    '早教益智': '早教益智',
    '沐浴露': '沐浴露',
    '男士面部护理': '男士面部护理',
    '茶具': '茶具',
    '粉饼': '粉饼',
    '国产腕表': '国产腕表',
    '巧克力': '巧克力',
    '手部护理': '手部护理',
    '洁面': '洁面',
    '时尚配饰套装': '时尚配饰套装',
    '礼袋': '礼袋',
    '牙刷': '牙刷',
    '洗发沐浴': '洗发沐浴',
    '地板清洁剂': '地板清洁剂',
    '男士双肩包': '男士双肩包',
    '黄金': '黄金',
    '女士腰带': '女士腰带',
    '卫生巾': '卫生巾',
    '女士裤装': '女士裤装（含中性）',
    '中性裤装': '女士裤装（含中性）',
    '定妆喷雾': '定妆喷雾/水',
    '定妆水': '定妆喷雾/水',
    '耳饰': '耳饰',
    '维生素': '维生素',
    '美容仪': '美容仪',
    '电动牙刷': '电动牙刷',
    '家纺': '家纺',
    '花胶': '花胶/鱼胶',
    '鱼胶': '花胶/鱼胶',
    '化妆棉': '化妆棉',
    '白酒': '白酒',
    '防晒': '防晒',
    '男士腰包': '男士腰包/胸包',
    '男士胸包': '男士腰包/胸包',
    '散粉': '散粉/蜜粉',
    '蜜粉': '散粉/蜜粉',
    '耳机': '耳机耳麦',
    '耳麦': '耳机耳麦',
    '食用油': '食用油/调味油',
    '调味油': '食用油/调味油',
    '手镯': '手镯/手链',
    '手链': '手镯/手链',
    '假睫毛': '假睫毛',
    '儿童家纺': '儿童家纺',
    '坚果': '坚果',
    '方便速食': '方便速食/速冻食品',
    '速冻食品': '方便速食/速冻食品',
    '阿胶': '阿胶',
    '眼线': '眼线',
    '眼膜': '眼膜',
    '遥控玩具': '遥控/电动/模型玩具',
    '电动玩具': '遥控/电动/模型玩具',
    '模型玩具': '遥控/电动/模型玩具',
    '女士单鞋': '女士单鞋',
    '牙膏': '牙膏',
    '男士洁面': '男士洁面',
    '口喷': '口喷',
    '男士单鞋': '男士单鞋',
    '眉笔': '眉笔/眉粉',
    '眉粉': '眉笔/眉粉',
    '益生菌': '益生菌',
    '麦片': '麦片',
    '唇笔': '唇笔/唇线笔',
    '唇线笔': '唇笔/唇线笔',
    '笔': '笔',
    '吊坠': '吊坠',
    '气垫': '气垫',
    '纤体塑形': '纤体塑形',
    '女士双肩包': '女士双肩包（含中性双肩包）',
    '中性双肩包': '女士双肩包（含中性双肩包）',
    '乐高积木': '拼接积木（乐高/木质）',
    '木质积木': '拼接积木（乐高/木质）',
    '糕点': '糕点（非月饼）',
    '妆前乳': '妆前乳/隔离',
    '隔离': '妆前乳/隔离',
    '饼干': '饼干',
    '车载用品': '车载用品',
    '唇彩': '唇彩/唇蜜/唇釉',
    '唇蜜': '唇彩/唇蜜/唇釉',
    '唇釉': '唇彩/唇蜜/唇釉',
    '手机': '手机',
    '发饰': '发饰',
    '遮瑕': '遮瑕',
    '女士外套': '女士外套（含中性）',
    '中性外套': '女士外套（含中性）',
    '野山参': '野山参',
    '乳液': '乳液面霜',
    '面霜': '乳液面霜',
    '太阳镜': '太阳镜',
    '起泡酒': '起泡酒',
    '剃须护理': '剃须护理',
    '氨糖': '氨糖/软骨素',
    '软骨素': '氨糖/软骨素',
    '威士忌': '威士忌/Whiskey',
    'Whiskey': '威士忌/Whiskey',
    '卷发器': '卷/直发器',
    '直发器': '卷/直发器',
    '户外玩具': '其他玩具（户外玩具/黏土/水上玩具）',
    '黏土': '其他玩具（户外玩具/黏土/水上玩具）',
    '水上玩具': '其他玩具（户外玩具/黏土/水上玩具）',
    '女士单肩包': '女士单肩包/斜挎包（含中性单肩包）',
    '斜挎包': '女士单肩包/斜挎包（含中性单肩包）',
    '中性单肩包': '女士单肩包/斜挎包（含中性单肩包）',
    '沐浴香皂': '沐浴香皂',
    '瑞士腕表': '瑞士腕表',
    '男士外套': '男士外套',
    '日常护理': '日常护理',
    '洗衣液': '洗衣液',
    'BB霜': 'BB霜/CC霜',
    'CC霜': 'BB霜/CC霜',
    '其他体重管理': '其他体重管理',
    '口罩': '口罩',
    '润肤油': '润肤油',
    '玩具': '玩具',
    '腮红': '腮红',
    '白茶': '白茶',
    '男士单肩包': '男士单肩包/斜挎包',
    '男士斜挎包': '男士单肩包/斜挎包',
    '大闸蟹': '大闸蟹',
    '女士T恤': '女士T恤',
    '香水套装': '香水套装',
    '儿童面霜': '儿童面霜',
    '面部护理套装': '面部护理套装',
    '其它身体护理': '其它身体护理',
    '围巾': '围巾/披肩/丝巾',
    '披肩': '围巾/披肩/丝巾',
    '丝巾': '围巾/披肩/丝巾',
    '摆件': '摆件/挂饰',
    '挂饰': '摆件/挂饰',
    '驱蚊驱虫': '驱蚊驱虫',
    '剃毛器': '剃/脱毛器',
    '脱毛器': '剃/脱毛器',
    '乌龙茶': '乌龙茶',
    '儿童餐具': '儿童餐具',
    '粉底液': '粉底液/霜',
    '粉底霜': '粉底液/霜',
    '护发': '护发',
    '眼霜': '眼霜/眼部精华',
    '眼部精华': '眼霜/眼部精华',
    '修容': '修容',
    '胸针': '胸针',
    '爽肤水': '爽肤水/化妆水',
    '化妆水': '爽肤水/化妆水',
    '市场推广商品': '市场推广商品',
    '电吹风': '电吹风',
    '猫保健品': '猫/狗保健品',
    '狗保健品': '猫/狗保健品',
    '男士休闲鞋': '男士休闲鞋',
    '儿童太阳镜': '儿童太阳镜',
    '精华': '精华',
    '其他美妆工具': '其他美妆工具',
    '洗手液': '洗手液',
    '欧美腕表': '欧美腕表',
    '眼影': '眼影',
    '袜子': '袜子',
    '家居香氛': '家居香氛',
    '咖啡机': '咖啡机',
    '酒杯': '酒杯',
    '洗发': '洗发',
    '其他健康理疗': '其他健康理疗',
    '香水': '香水',
    '奶瓶': '奶瓶奶嘴',
    '奶嘴': '奶瓶奶嘴',
    '高光': '高光',
    '女士手提包': '女士手提包（仅可手提不可肩挎）',
    '日韩腕表': '日韩腕表',
    '眼罩': '眼罩',
    '化妆刷': '化妆刷',
    '音响': '音响',
    '膳食纤维素': '膳食纤维素',
    '蜂蜜': '蜂蜜/蜂类制品',
    '蜂类制品': '蜂蜜/蜂类制品',
    '辅酶Q10': '辅酶Q10',
    '光学眼镜': '光学眼镜',
    '梳妆用品': '梳妆用品',
    '红葡萄酒': '红葡萄酒',
    '按摩仪器': '按摩仪器',
    '其他滋补品': '其他滋补品',
    '油污净': '油污净',
    '项链': '项链',
    '睡裙': '睡裙',
    '面膜': '面膜',
    '睫毛夹': '睫毛夹',
    '粉扑': '粉扑',
    '润肤乳': '润肤乳',
    '口红': '口红',
    '唇膜': '唇膜/唇部精华',
    '唇部精华': '唇膜/唇部精华',
    '润唇膏': '润唇膏',
    '毛绒玩具': '毛绒玩具',
    '女士休闲鞋': '女士休闲鞋',
    '其他参茸制品': '其他参茸制品',
    '生活日用': '生活日用',
    '帽子': '帽子/手套',
    '手套': '帽子/手套',
    '私处护理': '私处护理',
    '儿童沐浴': '儿童沐浴',
    '睫毛膏': '睫毛膏/睫毛液',
    '睫毛液': '睫毛膏/睫毛液',
    '卸妆': '卸妆',
    '其他滋补贵细': '其他滋补贵细',
}


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
    "白酒": "白酒",
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


def _clear_category_selection(state: GiftRecommendationState) -> None:
    state.selected_category = None
    state.recommended_categories = []
    state.detailed_dimensions = {}
    state.filtered_products = []
    state.final_product_cards = []
    state.candidate_products = []
    state.candidate_pool_summary = {}
    state.candidate_pool_reason = ""
    setattr(state, "selected_subcategory", None)
    setattr(state, "selected_mid_category", None)
    setattr(state, "selected_big_category", None)
    setattr(state, "category_level", "")


def apply_subcategory_selection(
    state: GiftRecommendationState,
    subcategory_name: str,
    selection_reason: str,
    description: str,
) -> None:
    selected_mid_category = _get_complete_small_to_mid_category_map().get(subcategory_name, "")
    selected_big_category = _get_complete_mid_to_big_category_map().get(selected_mid_category, "")

    _clear_category_selection(state)
    setattr(state, "selected_subcategory", subcategory_name)
    setattr(state, "selected_mid_category", selected_mid_category)
    setattr(state, "selected_big_category", selected_big_category)
    setattr(state, "category_level", "subcategory")

    state.selected_category = ProductCategory(
        category_id=subcategory_name,
        category_name=subcategory_name,
        description=description,
        selection_reason=selection_reason,
    )


def apply_mid_category_selection(
    state: GiftRecommendationState,
    mid_category_name: str,
    selection_reason: str,
    description: str,
) -> None:
    selected_big_category = _get_complete_mid_to_big_category_map().get(mid_category_name, "")

    _clear_category_selection(state)
    setattr(state, "selected_subcategory", None)
    setattr(state, "selected_mid_category", mid_category_name)
    setattr(state, "selected_big_category", selected_big_category)
    setattr(state, "category_level", "mid_category")

    state.selected_category = ProductCategory(
        category_id=mid_category_name,
        category_name=mid_category_name,
        description=description,
        selection_reason=selection_reason,
    )


def _build_allowed_mid_categories_for_generic_selection(
    context_text: str,
    excluded_mid_category_set: set,
) -> List[str]:
    allowed_mid_categories: List[str] = []
    seen = set()

    def add_mid_category(mid_category: str) -> None:
        if not mid_category or mid_category in seen or mid_category in excluded_mid_category_set:
            return
        if mid_category in _NON_GIFT_MID_CATEGORIES:
            ctx = (context_text or "")
            if mid_category not in ctx and not any(part in ctx for part in mid_category.split("（")[0].split("/") if len(part) >= 2):
                return
        allowed_mid_categories.append(mid_category)
        seen.add(mid_category)

    for mid_category in MID_CATEGORY_TO_BIG_CATEGORY_MAP.keys():
        add_mid_category(mid_category)

    complete_mid_to_big_map = _get_complete_mid_to_big_category_map()
    for mid_category, triggers in EXTENDED_MID_CATEGORY_TRIGGERS.items():
        if mid_category not in complete_mid_to_big_map:
            continue
        if any(trigger and trigger in (context_text or "") for trigger in triggers):
            add_mid_category(mid_category)

    return allowed_mid_categories


def _build_mid_category_selection_system_prompt(
    allowed_mid_categories: List[str],
) -> str:
    complete_mid_to_big_map = _get_complete_mid_to_big_category_map()
    candidate_map = {
        mid_category: complete_mid_to_big_map.get(mid_category, "")
        for mid_category in allowed_mid_categories
        if mid_category
    }
    return MID_CATEGORY_SELECTION_PROMPT_TEMPLATE.format(
        candidate_mid_categories=build_mid_category_candidate_text(candidate_map)
    )


def _apply_explicit_category_selection_result(
    state: GiftRecommendationState,
    explicit_result: ExplicitCategoryResolveResult,
    excluded_mid_category_set: set,
) -> Optional[CategorySelectionResult]:
    if explicit_result.match_type == "subcategory" and explicit_result.subcategory:
        selection_reason = (
            explicit_result.reason
            or f"检测到您明确提及了“小类：{explicit_result.subcategory}”，因此将直接为您推荐该小类商品。"
        )
        apply_subcategory_selection(
            state,
            explicit_result.subcategory,
            selection_reason=selection_reason,
            description=f"命中的小类：{explicit_result.subcategory}",
        )
        return CategorySelectionResult(
            result_type="direct_subcategory",
            selected_category_name=explicit_result.subcategory,
            selection_reason=selection_reason,
        )

    if explicit_result.match_type == "mid_category" and explicit_result.mid_category:
        selection_reason = (
            explicit_result.reason
            or f"检测到您明确提及了“中类：{explicit_result.mid_category}”，因此将基于该中类为您推荐商品。"
        )
        apply_mid_category_selection(
            state,
            explicit_result.mid_category,
            selection_reason=selection_reason,
            description=f"命中的中类：{explicit_result.mid_category}",
        )
        return CategorySelectionResult(
            result_type="direct_mid_category",
            selected_category_name=explicit_result.mid_category,
            selection_reason=selection_reason,
        )

    if explicit_result.match_type != "ambiguous_subcategories":
        return None

    candidate_mid_categories = [
        category for category in explicit_result.candidate_mid_categories
        if category not in excluded_mid_category_set
    ]
    if len(candidate_mid_categories) == 1:
        selected_mid_category = candidate_mid_categories[0]
        selection_reason = (
            explicit_result.reason
            or f"检测到用户提到多个相近小类，均属于“{selected_mid_category}”，因此先按该中类推荐。"
        )
        apply_mid_category_selection(
            state,
            selected_mid_category,
            selection_reason=selection_reason,
            description=f"多个小类共同归属中类：{selected_mid_category}",
        )
        return CategorySelectionResult(
            result_type="direct_mid_category",
            selected_category_name=selected_mid_category,
            selection_reason=selection_reason,
        )
    if len(candidate_mid_categories) > 1:
        _clear_category_selection(state)
        state.recommended_categories = candidate_mid_categories[:3]
        return CategorySelectionResult(
            result_type="recommend_list",
            recommended_categories=candidate_mid_categories[:3],
            selection_reason=explicit_result.reason
            or "用户提到的商品方向可能对应多个中类，请先选择更想看的方向。",
        )
    return None


def _normalize_mid_category_name(
    category_name: str,
    excluded_mid_category_set: Optional[set] = None,
) -> str:
    category_name = (category_name or "").strip()
    if not category_name:
        return ""
    excluded_mid_category_set = excluded_mid_category_set or set()
    complete_mid_to_big_map = _get_complete_mid_to_big_category_map()
    if category_name in complete_mid_to_big_map and category_name not in excluded_mid_category_set:
        return category_name
    alias_mid_category = MID_CATEGORY_ALIAS_MAP.get(category_name, "")
    if (
        alias_mid_category
        and alias_mid_category in complete_mid_to_big_map
        and alias_mid_category not in excluded_mid_category_set
    ):
        return alias_mid_category
    return ""


def _resolve_mid_category_alias_from_text(
    text: str,
    excluded_mid_category_set: Optional[set] = None,
) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    excluded_mid_category_set = excluded_mid_category_set or set()
    complete_mid_to_big_map = _get_complete_mid_to_big_category_map()
    matched_aliases = [
        alias
        for alias in MID_CATEGORY_ALIAS_MAP.keys()
        if alias
        and alias in text
        and not _is_category_rejected_expression(text, alias)
    ]
    matched_aliases.sort(key=len, reverse=True)
    for alias in matched_aliases:
        mid_category = MID_CATEGORY_ALIAS_MAP.get(alias, "")
        if (
            mid_category
            and mid_category in complete_mid_to_big_map
            and mid_category not in excluded_mid_category_set
            and not _is_category_rejected_expression(text, mid_category)
        ):
            return mid_category
    return ""


def _normalize_selected_mid_categories(
    selected_mid_categories: Any,
    valid_mid_categories: List[str],
    excluded_mid_category_set: set,
) -> List[str]:
    valid_mid_category_set = set(valid_mid_categories)
    filtered_mid_categories: List[str] = []
    for category in selected_mid_categories or []:
        if not isinstance(category, str):
            continue
        normalized_category = _normalize_mid_category_name(category, excluded_mid_category_set)
        if (
            normalized_category
            and normalized_category in valid_mid_category_set
            and normalized_category not in filtered_mid_categories
        ):
            filtered_mid_categories.append(normalized_category)
    return filtered_mid_categories


def _is_llm_explicit_result_anchored(
    user_text: str,
    explicit_result: ExplicitCategoryResolveResult,
) -> bool:
    if not explicit_result or not explicit_result.source.startswith("llm"):
        return True
    text = (user_text or "").strip()
    if not text:
        return False
    if explicit_result.subcategory and explicit_result.subcategory in text:
        return True
    if explicit_result.mid_category and explicit_result.mid_category in text:
        return True
    target_mid_categories = set(explicit_result.candidate_mid_categories or [])
    if explicit_result.mid_category:
        target_mid_categories.add(explicit_result.mid_category)
    for alias, mid_category in MID_CATEGORY_ALIAS_MAP.items():
        if alias and alias in text and mid_category in target_mid_categories:
            return True
    for subcategory in explicit_result.candidate_subcategories or []:
        if subcategory and subcategory in text:
            return True
    return False


def _call_unified_category_decision(
    latest_user_text: str,
    selection_prompt: str,
    allowed_mid_categories: List[str],
    excluded_mid_category_set: set,
    excluded_subcategory_set: set,
) -> Optional[Dict[str, Any]]:
    complete_mid_to_big_map = _get_complete_mid_to_big_category_map()
    allowed_mid_category_map = {
        category: complete_mid_to_big_map.get(category, "")
        for category in allowed_mid_categories
    }
    small_to_mid_map = _get_complete_small_to_mid_category_map()
    prompt = (
        f"用户最新输入：\n{latest_user_text}\n\n"
        f"{selection_prompt}\n\n"
        "本轮候选中类：\n"
        f"{build_mid_category_candidate_text(allowed_mid_category_map)}\n\n"
        "候选小类及所属中类：\n"
        f"{build_small_category_candidate_text(small_to_mid_map, excluded_subcategory_set, excluded_mid_category_set)}"
    )
    try:
        result = call_json(
            prompt=prompt,
            system_prompt=UNIFIED_CATEGORY_DECISION_SYSTEM_PROMPT,
            temperature=0.1,
        )
    except Exception as exc:
        print(f"统一品类决策 LLM 调用失败，将回退旧链路: {exc}")
        return None

    if not isinstance(result, dict):
        return None
    if not isinstance(result.get("explicit_category"), dict):
        return None
    if not isinstance(result.get("selected_category"), list):
        return None
    return result


def category_selection(
    state: GiftRecommendationState,
    excluded_mid_categories: Optional[List[str]] = None,
    excluded_subcategories: Optional[List[str]] = None,
) -> CategorySelectionResult:
    """
    品类选择逻辑：
    1. 优先匹配小类，命中则直接返回小类
    2. 若未命中，则尝试直接识别中类
    3. 若仍未命中，则调用 LLM 推荐中类
    """
    context_info = _build_context_info(state)
    excluded_mid_category_set = {
        category for category in (excluded_mid_categories or []) if category
    }
    excluded_subcategory_set = {
        category for category in (excluded_subcategories or []) if category
    }

    latest_user_text = _get_latest_user_text(state)
    explicit_result = _resolve_explicit_category_reference_from_text(
        latest_user_text,
        excluded_mid_categories=excluded_mid_category_set,
        excluded_subcategories=excluded_subcategory_set,
        allow_llm=False,
    )
    explicit_selection_result = _apply_explicit_category_selection_result(
        state,
        explicit_result,
        excluded_mid_category_set,
    )
    if explicit_selection_result is not None:
        return explicit_selection_result

    allowed_mid_categories = _build_allowed_mid_categories_for_generic_selection(
        context_info,
        excluded_mid_category_set,
    )
    selection_prompt = f"用户送礼需求信息：\n{context_info}"
    if excluded_mid_category_set:
        excluded_text = "\n".join(
            f"- {category}" for category in sorted(excluded_mid_category_set)
        )
        selection_prompt += (
            "\n\n本轮需要避开的中类：\n"
            f"{excluded_text}\n"
            "用户已经表示这些方向不合适，请不要再次返回这些中类；"
            "如果现有信息不足以判断新的中类，可以返回空数组。"
        )
    if excluded_subcategory_set:
        excluded_subcategory_text = "\n".join(
            f"- {category}" for category in sorted(excluded_subcategory_set)
        )
        selection_prompt += (
            "\n\n本轮需要避开的小类：\n"
            f"{excluded_subcategory_text}\n"
            "用户已经明确表示这些小类不合适；这些小类只代表排除条件，不能作为用户想要的目标品类。"
            "如果用户没有明确正向目标品类，请推荐2-3个差异化中类，不要因为排除小类而直接收窄到其所属中类。"
        )

    result: Any = None
    should_try_unified_decision = _should_try_explicit_category_llm(latest_user_text)
    if should_try_unified_decision:
        result = _call_unified_category_decision(
            latest_user_text,
            selection_prompt,
            allowed_mid_categories,
            excluded_mid_category_set,
            excluded_subcategory_set,
        )
        if result is not None:
            explicit_result = _validate_explicit_category_resolve_result(
                result.get("explicit_category"),
                excluded_mid_categories=excluded_mid_category_set,
                excluded_subcategories=excluded_subcategory_set,
            )
            if not _is_llm_explicit_result_anchored(latest_user_text, explicit_result):
                explicit_result = ExplicitCategoryResolveResult()
            explicit_selection_result = _apply_explicit_category_selection_result(
                state,
                explicit_result,
                excluded_mid_category_set,
            )
            if explicit_selection_result is not None:
                return explicit_selection_result

    if result is None:
        if should_try_unified_decision:
            explicit_result = _resolve_explicit_category_reference_by_llm(
                latest_user_text,
                excluded_mid_categories=excluded_mid_category_set,
                excluded_subcategories=excluded_subcategory_set,
            )
            if not _is_llm_explicit_result_anchored(latest_user_text, explicit_result):
                explicit_result = ExplicitCategoryResolveResult()
            explicit_selection_result = _apply_explicit_category_selection_result(
                state,
                explicit_result,
                excluded_mid_category_set,
            )
            if explicit_selection_result is not None:
                return explicit_selection_result
        if explicit_result.match_type == "none":
            explicit_result = _resolve_explicit_category_reference_from_text(
                _collect_context_text(state),
                excluded_mid_categories=excluded_mid_category_set,
                excluded_subcategories=excluded_subcategory_set,
                allow_llm=False,
            )
            explicit_selection_result = _apply_explicit_category_selection_result(
                state,
                explicit_result,
                excluded_mid_category_set,
            )
            if explicit_selection_result is not None:
                return explicit_selection_result
        result = call_json(
            prompt=selection_prompt,
            system_prompt=_build_mid_category_selection_system_prompt(allowed_mid_categories),
            temperature=0.2,
        )
    elif explicit_result.match_type == "none":
        explicit_result = _resolve_explicit_category_reference_from_text(
            _collect_context_text(state),
            excluded_mid_categories=excluded_mid_category_set,
            excluded_subcategories=excluded_subcategory_set,
            allow_llm=False,
        )
        explicit_selection_result = _apply_explicit_category_selection_result(
            state,
            explicit_result,
            excluded_mid_category_set,
        )
        if explicit_selection_result is not None:
            return explicit_selection_result

    selected_mid_categories = result.get("selected_category") if isinstance(result, dict) else None
    selection_reason = result.get("selection_reason", "") if isinstance(result, dict) else ""

    valid_mid_categories = list(allowed_mid_categories)
    filtered_mid_categories = _normalize_selected_mid_categories(
        selected_mid_categories,
        valid_mid_categories,
        excluded_mid_category_set,
    )

    if not filtered_mid_categories:
        filtered_mid_categories = _fallback_three_mid_categories(
            state,
            valid_mid_categories,
            excluded_mid_category_set,
        )
        if selection_reason:
            selection_reason = f"{selection_reason}；未得到稳定标准中类，已按送礼场景补充候选方向。"
        else:
            selection_reason = "当前仍是送礼需求，先给出三个较适合作为礼物的候选中类。"

    if not filtered_mid_categories:
        _clear_category_selection(state)
        return CategorySelectionResult(result_type="need_more_info")

    if len(filtered_mid_categories) == 1:
        diversified_mid_categories = _supplement_diverse_mid_categories(
            filtered_mid_categories,
            excluded_mid_category_set,
        )
        if len(diversified_mid_categories) > 1 and (
            excluded_subcategory_set or not _resolve_mid_category_alias_from_text(latest_user_text, excluded_mid_category_set)
        ):
            _clear_category_selection(state)
            state.recommended_categories = diversified_mid_categories
            return CategorySelectionResult(
                result_type="recommend_list",
                recommended_categories=diversified_mid_categories,
                selection_reason=selection_reason
                or "目标品类尚未完全确定，因此提供多个差异化中类供选择。",
            )

    if len(filtered_mid_categories) == 1:
        selected_mid_category = filtered_mid_categories[0]
        if not selection_reason:
            selection_reason = (
                f"结合当前已知信息，{selected_mid_category}是最匹配的商品中类。"
            )
        apply_mid_category_selection(
            state,
            selected_mid_category,
            selection_reason=selection_reason,
            description=f"推荐的中类：{selected_mid_category}",
        )
        return CategorySelectionResult(
            result_type="direct_mid_category",
            selected_category_name=selected_mid_category,
            selection_reason=selection_reason,
        )

    _clear_category_selection(state)
    state.recommended_categories = filtered_mid_categories
    return CategorySelectionResult(
        result_type="recommend_list",
        recommended_categories=filtered_mid_categories,
        selection_reason=selection_reason,
    )


def _supplement_diverse_mid_categories(
    selected_mid_categories: List[str],
    excluded_mid_category_set: set,
    target_count: int = 3,
) -> List[str]:
    preferred_mid_categories = [
        "香水香氛",
        "腕表",
        "面部护肤",
        "时尚配饰",
        "美妆工具",
        "面部彩妆",
        "女包（含中性）",
        "黄金珠宝",
    ]
    result: List[str] = []
    valid_mid_categories = set(MID_CATEGORY_TO_BIG_CATEGORY_MAP.keys())

    for category in selected_mid_categories + preferred_mid_categories:
        if (
            category
            and category in valid_mid_categories
            and category not in excluded_mid_category_set
            and category not in result
        ):
            result.append(category)
        if len(result) >= target_count:
            break

    return result


def _get_slot_text(state: GiftRecommendationState, slot_name: str) -> str:
    slot = state.filled_slots.get(slot_name)
    value = getattr(slot, "value", "") if slot else ""
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item)
    return str(value or "")


def _append_fallback_category(
    result: List[str],
    category: str,
    valid_mid_category_set: set,
    excluded_mid_category_set: set,
    target_count: int,
) -> None:
    if len(result) >= target_count:
        return
    if (
        category
        and category in valid_mid_category_set
        and category not in excluded_mid_category_set
        and category not in result
    ):
        result.append(category)


def _fallback_three_mid_categories(
    state: GiftRecommendationState,
    valid_mid_categories: List[str],
    excluded_mid_category_set: set,
    target_count: int = 3,
) -> List[str]:
    valid_mid_category_set = set(valid_mid_categories)
    context_text = " ".join(
        text
        for text in [
            _collect_context_text(state),
            _get_slot_text(state, "recipient_relation"),
            _get_slot_text(state, "recipient_preferences"),
            _get_slot_text(state, "occasion"),
            _get_slot_text(state, "recipient_gender"),
        ]
        if text
    )
    result: List[str] = []

    alias_mid_category = _resolve_mid_category_alias_from_text(
        context_text,
        excluded_mid_category_set=excluded_mid_category_set,
    )
    _append_fallback_category(
        result,
        alias_mid_category,
        valid_mid_category_set,
        excluded_mid_category_set,
        target_count,
    )

    recipient_gender = _get_slot_text(state, "recipient_gender")
    recipient_relation = _get_slot_text(state, "recipient_relation")
    recipient_preferences = _get_slot_text(state, "recipient_preferences")
    occasion = _get_slot_text(state, "occasion")

    preference_rules = [
        (("茶", "茶叶", "绿茶", "红茶", "白茶", "乌龙茶"), ["茗茶", "品质生活", "咖啡冲饮"]),
        (("咖啡", "冲饮"), ["咖啡冲饮", "品质生活", "茗茶"]),
        (("护肤", "面膜", "精华", "面霜", "洁面"), ["面部护肤", "身体护理", "香水香氛"]),
        (("彩妆", "口红", "粉底", "眼影"), ["面部彩妆", "香水香氛", "美妆工具"]),
        (("香水", "香氛", "香薰"), ["香水香氛", "面部护肤", "时尚配饰"]),
        (("酒", "红酒", "葡萄酒"), ["葡萄酒", "洋酒", "国酒"]),
        (("表", "手表", "腕表"), ["腕表", "时尚配饰", "黄金珠宝"]),
        (("包", "包包", "箱包", "行李箱"), ["女包（含中性）", "功能箱包", "时尚配饰"]),
        (("首饰", "珠宝", "项链", "戒指"), ["黄金珠宝", "时尚配饰", "腕表"]),
        (("实用", "体面", "稳妥"), ["面部护肤", "香水香氛", "品质生活"]),
    ]
    for keywords, categories in preference_rules:
        if any(keyword in context_text for keyword in keywords):
            for category in categories:
                _append_fallback_category(
                    result,
                    category,
                    valid_mid_category_set,
                    excluded_mid_category_set,
                    target_count,
                )
        if len(result) >= target_count:
            return result

    female_relation_signal = any(
        signal in recipient_relation
        for signal in ("妈妈", "母亲", "女友", "老婆", "妻子", "闺蜜", "姐姐", "妹妹", "女士", "女性")
    )
    male_relation_signal = any(
        signal in recipient_relation
        for signal in ("爸爸", "父亲", "男友", "老公", "丈夫", "兄弟", "哥哥", "弟弟", "男士", "男性")
    )

    if "女" in recipient_gender or female_relation_signal:
        for category in ["面部护肤", "香水香氛", "面部彩妆"]:
            _append_fallback_category(
                result,
                category,
                valid_mid_category_set,
                excluded_mid_category_set,
                target_count,
            )
    elif "男" in recipient_gender or male_relation_signal:
        for category in ["男士护肤", "腕表", "功能箱包"]:
            _append_fallback_category(
                result,
                category,
                valid_mid_category_set,
                excluded_mid_category_set,
                target_count,
            )

    if len(result) >= target_count:
        return result

    if any(signal in occasion for signal in ("生日", "纪念日", "节日", "母亲节", "父亲节", "新婚", "乔迁")):
        for category in ["香水香氛", "面部护肤", "品质生活"]:
            _append_fallback_category(
                result,
                category,
                valid_mid_category_set,
                excluded_mid_category_set,
                target_count,
            )

    if len(result) >= target_count:
        return result

    for category in [
        "面部护肤",
        "香水香氛",
        "品质生活",
        "腕表",
        "功能箱包",
        "黄金珠宝",
        "茗茶",
        "面部彩妆",
    ]:
        _append_fallback_category(
            result,
            category,
            valid_mid_category_set,
            excluded_mid_category_set,
            target_count,
        )
        if len(result) >= target_count:
            break

    return result


def _build_context_info(state: GiftRecommendationState) -> str:
    slot_descriptions = {
        "recipient_relation": "收礼人关系",
        "occasion": "送礼场景",
        "budget_min": "预算下限",
        "budget_max": "预算上限",
        "recipient_preferences": "收礼人偏好",
        "recipient_age": "收礼人年龄",
        "recipient_gender": "收礼人性别",
        "delivery_time": "期望送达时间",
        "taboo": "禁忌信息",
    }

    context_lines: List[str] = []

    for slot_name, slot in state.filled_slots.items():
        if getattr(slot, "value", None) is not None:
            description = slot_descriptions.get(slot_name, slot_name)
            context_lines.append(f"- {description}: {slot.value}")

    inference_results = getattr(state, "inference_results", None)
    if inference_results and isinstance(inference_results, list):
        applied_inference = [
            item for item in inference_results
            if isinstance(item, dict) and item.get("applied") is True
        ]
        if applied_inference:
            context_lines.append("- 已应用推理结果:")
            for item in applied_inference:
                slot_name = item.get("slot_name", "")
                value = item.get("value", "")
                reasoning = item.get("reasoning", "")
                description = slot_descriptions.get(slot_name, slot_name)
                context_lines.append(f"  - {description}: {value}（依据：{reasoning}）")

    if state.chat_history:
        last_user_message = next(
            (msg.get("content", "") for msg in reversed(state.chat_history) if msg.get("role") == "user"),
            "",
        )
        if last_user_message:
            context_lines.append(f"- 最近用户表达: {last_user_message}")
        last_assistant_message = next(
            (msg.get("content", "") for msg in reversed(state.chat_history) if msg.get("role") == "assistant"),
            "",
        )
        if last_assistant_message:
            context_lines.append(f"- 最近助手回复: {last_assistant_message}")

    return "\n".join(context_lines) if context_lines else "无具体信息"


def _collect_context_text(state: GiftRecommendationState) -> str:
    signals: List[str] = []

    for slot_name, slot in state.filled_slots.items():
        if slot_name == "taboo":
            continue
        if getattr(slot, "value", None):
            if isinstance(slot.value, list):
                signals.extend([str(v) for v in slot.value if v])
            else:
                signals.append(str(slot.value))

    for msg in state.chat_history:
        if msg.get("role") == "user" and msg.get("content"):
            signals.append(str(msg["content"]))

    return " ".join(signals)


def _detect_subcategory_from_context(
    state: GiftRecommendationState,
    excluded_subcategories: Optional[set] = None,
) -> str:
    excluded_subcategories = excluded_subcategories or set()
    latest_user_text = _get_latest_user_text(state)
    matched_subcategory = _match_subcategory_from_text(
        latest_user_text,
        excluded_subcategories=excluded_subcategories,
    )
    if matched_subcategory:
        return matched_subcategory

    return _match_subcategory_from_text(
        _collect_context_text(state),
        excluded_subcategories=excluded_subcategories,
    )


def resolve_explicit_category_reference(
    state: GiftRecommendationState,
    user_text: str,
    excluded_mid_categories: Optional[set] = None,
    excluded_subcategories: Optional[set] = None,
) -> ExplicitCategoryResolveResult:
    excluded_mid_categories = excluded_mid_categories or set()
    excluded_subcategories = excluded_subcategories or set()
    latest_user_text = (user_text or "").strip()
    context_text = _collect_context_text(state)

    result = _resolve_explicit_category_reference_from_text(
        latest_user_text,
        excluded_mid_categories=excluded_mid_categories,
        excluded_subcategories=excluded_subcategories,
        allow_llm=True,
    )
    if result.match_type != "none":
        return result

    return _resolve_explicit_category_reference_from_text(
        context_text,
        excluded_mid_categories=excluded_mid_categories,
        excluded_subcategories=excluded_subcategories,
        allow_llm=False,
    )


def _resolve_explicit_category_reference_from_text(
    text: str,
    excluded_mid_categories: Optional[set] = None,
    excluded_subcategories: Optional[set] = None,
    allow_llm: bool = True,
) -> ExplicitCategoryResolveResult:
    text = (text or "").strip()
    if not text:
        return ExplicitCategoryResolveResult()

    excluded_mid_categories = excluded_mid_categories or set()
    excluded_subcategories = excluded_subcategories or set()
    small_to_mid_map = _get_complete_small_to_mid_category_map()

    matched_subcategory = _match_subcategory_from_text(
        text,
        excluded_subcategories=excluded_subcategories,
    )
    matched_mid_category = small_to_mid_map.get(matched_subcategory, "")
    if (
        matched_subcategory
        and matched_subcategory not in excluded_subcategories
        and matched_mid_category not in excluded_mid_categories
    ):
        return ExplicitCategoryResolveResult(
            match_type="subcategory",
            subcategory=matched_subcategory,
            mid_category=matched_mid_category,
            confidence=1.0,
            source="rule",
            reason=f"检测到您明确提及了“小类：{matched_subcategory}”，因此将直接为您推荐该小类商品。",
        )

    direct_mid_category = _match_mid_category_from_text(
        text,
        excluded_mid_categories=excluded_mid_categories,
    )
    if direct_mid_category:
        return ExplicitCategoryResolveResult(
            match_type="mid_category",
            mid_category=direct_mid_category,
            confidence=1.0,
            source="rule",
            reason=f"检测到您明确提及了“中类：{direct_mid_category}”，因此将基于该中类为您推荐商品。",
        )

    alias_mid_category = _resolve_mid_category_alias_from_text(
        text,
        excluded_mid_category_set=excluded_mid_categories,
    )
    if alias_mid_category:
        return ExplicitCategoryResolveResult(
            match_type="mid_category",
            mid_category=alias_mid_category,
            confidence=1.0,
            source="rule_alias",
            reason=f"检测到用户使用自然语言品类表达，已映射到标准中类：{alias_mid_category}。",
        )

    if not allow_llm or not _should_try_explicit_category_llm(text):
        return ExplicitCategoryResolveResult()

    return _resolve_explicit_category_reference_by_llm(
        text,
        excluded_mid_categories=excluded_mid_categories,
        excluded_subcategories=excluded_subcategories,
    )


def _resolve_explicit_category_reference_by_llm(
    text: str,
    excluded_mid_categories: Optional[set] = None,
    excluded_subcategories: Optional[set] = None,
) -> ExplicitCategoryResolveResult:
    excluded_mid_categories = excluded_mid_categories or set()
    excluded_subcategories = excluded_subcategories or set()
    small_to_mid_map = _get_complete_small_to_mid_category_map()
    complete_mid_to_big_map = _get_complete_mid_to_big_category_map()
    prompt = (
        f"用户最新输入：\n{text}\n\n"
        f"候选中类：\n{build_mid_category_candidate_text(complete_mid_to_big_map, excluded_mid_categories)}\n\n"
        "候选小类及所属中类：\n"
        f"{build_small_category_candidate_text(small_to_mid_map, excluded_subcategories, excluded_mid_categories)}\n\n"
        "请只基于用户最新输入识别显式品类，不要根据收礼人或节日猜测小类。"
    )
    try:
        result = call_json(
            prompt=prompt,
            system_prompt=EXPLICIT_CATEGORY_RESOLVE_SYSTEM_PROMPT,
            temperature=0.1,
        )
    except Exception as exc:
        print(f"显式品类 LLM 识别失败: {exc}")
        return ExplicitCategoryResolveResult()
    return _validate_explicit_category_resolve_result(
        result,
        excluded_mid_categories=excluded_mid_categories,
        excluded_subcategories=excluded_subcategories,
    )


def _validate_explicit_category_resolve_result(
    result: Any,
    excluded_mid_categories: Optional[set] = None,
    excluded_subcategories: Optional[set] = None,
) -> ExplicitCategoryResolveResult:
    if not isinstance(result, dict):
        return ExplicitCategoryResolveResult()

    raw_matched = result.get("matched", False)
    if isinstance(raw_matched, str):
        raw_matched = raw_matched.strip().lower() in {"true", "1", "yes"}
    if not raw_matched:
        return ExplicitCategoryResolveResult()

    try:
        confidence = float(result.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < EXPLICIT_CATEGORY_LLM_CONFIDENCE_THRESHOLD:
        return ExplicitCategoryResolveResult()

    excluded_mid_categories = excluded_mid_categories or set()
    excluded_subcategories = excluded_subcategories or set()
    small_to_mid_map = _get_complete_small_to_mid_category_map()
    complete_mid_to_big_map = _get_complete_mid_to_big_category_map()

    target_subcategory = str(result.get("target_subcategory", "") or "").strip()
    target_mid_category = str(result.get("target_mid_category", "") or "").strip()
    normalized_target_mid_category = _normalize_mid_category_name(
        target_mid_category,
        excluded_mid_categories,
    )
    if normalized_target_mid_category:
        target_mid_category = normalized_target_mid_category

    if target_subcategory:
        normalized_subcategory_as_mid = _normalize_mid_category_name(
            target_subcategory,
            excluded_mid_categories,
        )
        if normalized_subcategory_as_mid and not small_to_mid_map.get(target_subcategory, ""):
            return ExplicitCategoryResolveResult(
                match_type="mid_category",
                mid_category=normalized_subcategory_as_mid,
                confidence=confidence,
                source="llm_level_corrected",
                reason=str(result.get("reason", "") or "").strip(),
            )

    if target_subcategory:
        resolved_mid_category = small_to_mid_map.get(target_subcategory, "")
        if (
            resolved_mid_category
            and resolved_mid_category == (target_mid_category or resolved_mid_category)
            and target_subcategory not in excluded_subcategories
            and resolved_mid_category not in excluded_mid_categories
        ):
            return ExplicitCategoryResolveResult(
                match_type="subcategory",
                subcategory=target_subcategory,
                mid_category=resolved_mid_category,
                confidence=confidence,
                source="llm",
                reason=str(result.get("reason", "") or "").strip(),
            )

    if target_mid_category:
        if target_mid_category in complete_mid_to_big_map and target_mid_category not in excluded_mid_categories:
            return ExplicitCategoryResolveResult(
                match_type="mid_category",
                mid_category=target_mid_category,
                confidence=confidence,
                source="llm",
                reason=str(result.get("reason", "") or "").strip(),
            )

    candidate_subcategories, candidate_mid_categories = _collect_valid_subcategory_candidates(
        result.get("candidates", []),
        small_to_mid_map,
        excluded_mid_categories,
        excluded_subcategories,
    )
    if candidate_subcategories:
        if len(candidate_subcategories) == 1:
            subcategory = candidate_subcategories[0]
            mid_category = small_to_mid_map.get(subcategory, "")
            return ExplicitCategoryResolveResult(
                match_type="subcategory",
                subcategory=subcategory,
                mid_category=mid_category,
                candidate_subcategories=candidate_subcategories,
                candidate_mid_categories=candidate_mid_categories,
                confidence=confidence,
                source="llm",
                reason=str(result.get("reason", "") or "").strip(),
            )
        return ExplicitCategoryResolveResult(
            match_type="ambiguous_subcategories",
            candidate_subcategories=candidate_subcategories,
            candidate_mid_categories=candidate_mid_categories,
            confidence=confidence,
            source="llm",
            reason=str(result.get("reason", "") or "").strip(),
        )

    return ExplicitCategoryResolveResult()


def _collect_valid_subcategory_candidates(
    candidates: Any,
    small_to_mid_map: Dict[str, str],
    excluded_mid_categories: set,
    excluded_subcategories: set,
) -> tuple[List[str], List[str]]:
    if not isinstance(candidates, list):
        return [], []

    subcategories: List[str] = []
    mid_categories: List[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        subcategory = str(candidate.get("subcategory", "") or "").strip()
        mid_category = str(candidate.get("mid_category", "") or "").strip()
        if not subcategory or subcategory in excluded_subcategories:
            continue
        resolved_mid_category = small_to_mid_map.get(subcategory, "")
        if not resolved_mid_category:
            continue
        if mid_category and mid_category != resolved_mid_category:
            continue
        if resolved_mid_category in excluded_mid_categories:
            continue
        if subcategory not in subcategories:
            subcategories.append(subcategory)
        if resolved_mid_category not in mid_categories:
            mid_categories.append(resolved_mid_category)
    return subcategories, mid_categories


def _should_try_explicit_category_llm(text: str) -> bool:
    if not text:
        return False
    negative_signals = ("不要", "不想要", "不考虑", "不推荐", "别推荐", "别送", "不送", "排除", "避开")
    strong_positive_signals = ("想要", "想看", "看看", "看下", "推荐", "送", "买", "换成", "改成", "还是", "就", "可以", "也行")
    if any(signal in text for signal in negative_signals) and not any(signal in text for signal in strong_positive_signals):
        return False
    return any(signal in text for signal in strong_positive_signals) or re.search(r"(^|[，,。！？\s])要[^，,。！？\s]{1,12}", text) is not None


def _match_subcategory_from_text(
    text: str,
    excluded_subcategories: Optional[set] = None,
) -> str:
    if not text:
        return ""
    excluded_subcategories = excluded_subcategories or set()
    matched_keys = [
        key for key in subcategory_keyword_map.keys()
        if (
            key
            and key in text
            and subcategory_keyword_map.get(key, "") not in excluded_subcategories
            and not _is_category_rejected_expression(text, subcategory_keyword_map.get(key, ""))
        )
    ]
    if not matched_keys:
        return ""
    matched_keys.sort(key=len, reverse=True)
    best_key = matched_keys[0]
    return subcategory_keyword_map[best_key]


def _detect_mid_category_from_context(
    state: GiftRecommendationState,
    excluded_mid_categories: Optional[set] = None,
) -> str:
    """
    如果用户输入中直接提到了中类名称，则直接返回该中类。
    """
    excluded_mid_categories = excluded_mid_categories or set()
    latest_user_text = _get_latest_user_text(state)
    matched_mid_category = _match_mid_category_from_text(
        latest_user_text,
        excluded_mid_categories=excluded_mid_categories,
    )
    if matched_mid_category:
        return matched_mid_category

    return _match_mid_category_from_text(
        _collect_context_text(state),
        excluded_mid_categories=excluded_mid_categories,
    )


def _match_mid_category_from_text(
    text: str,
    excluded_mid_categories: Optional[set] = None,
) -> str:
    if not text:
        return ""
    excluded_mid_categories = excluded_mid_categories or set()
    matched_mid_categories = [
        mid_category for mid_category in _get_complete_mid_to_big_category_map().keys()
        if (
            mid_category
            and mid_category in text
            and mid_category not in excluded_mid_categories
            and not _is_category_rejected_expression(text, mid_category)
        )
    ]
    if not matched_mid_categories:
        return ""

    matched_mid_categories.sort(key=len, reverse=True)
    return matched_mid_categories[0]


def _get_complete_small_to_mid_category_map() -> Dict[str, str]:
    return get_complete_small_to_mid_category_map(SMALL_TO_MID_CATEGORY_MAP)


def _get_complete_mid_to_big_category_map() -> Dict[str, str]:
    return get_complete_mid_to_big_category_map(MID_CATEGORY_TO_BIG_CATEGORY_MAP)


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


def _get_latest_user_text(state: GiftRecommendationState) -> str:
    for msg in reversed(state.chat_history):
        if msg.get("role") == "user" and msg.get("content"):
            return str(msg["content"])
    return ""


def _build_category_recommendation_message(state: GiftRecommendationState, category: ProductCategory) -> str:
    user_summary: List[str] = []
    for slot_name, slot in state.filled_slots.items():
        if getattr(slot, "value", None) is not None:
            user_summary.append(f"{slot.display_name}:{slot.value}")

    summary_text = "，".join(user_summary) if user_summary else "当前信息较少"

    return (
        f"根据您的需求，我们推荐中类：{category.category_name}。"
        f"（参考信息：{summary_text}）"
        f"如果您愿意，我还可以继续细化到具体商品方向。"
    )
