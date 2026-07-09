from collections import Counter
from typing import Dict, List, Any, Tuple

from .models import GiftRecommendationState
from .llm_client import call_text
from .category_catalog import (
    get_complete_mid_to_big_category_map,
    get_complete_small_to_mid_category_map,
)
from .product_filtering import _load_products_from_csv

# 通用槽位模板（所有类别都适用）
COMMON_SLOTS_TEMPLATE = [
    {
        "slot_name": "recipient_relation",
        "description": "收礼关系",
        "question_hint": "收礼人和您是什么关系？"
    },
    {
        "slot_name": "budget_max",
        "description": "预算范围",
        "question_hint": "预算大概是多少？"
    },
    {
        "slot_name": "recipient_gender",
        "description": "收礼人性别",
        "question_hint": "收礼人是男士、女士还是儿童？"
    },
    {
        "slot_name": "recipient_age", 
        "description": "收礼人年龄段",
        "question_hint": "收礼人大致年龄段是？"
    },
    {
        "slot_name": "recipient_preferences",
        "description": "收礼人喜好",
        "question_hint": "收礼人有什么特别的喜好或偏好？"
    },
    {
        "slot_name": "brand_preference",
        "description": "品牌偏好",
        "question_hint": "是否有偏好的品牌，还是更看重款式和预算？",
        "priority": "medium",
        "modes": ["normal", "detailed"],
    },
    {
        "slot_name": "occasion",
        "description": "送礼场景",
        "question_hint": "是什么特殊场合或节日送礼？"
    },
    {
        "slot_name": "taboo",
        "description": "禁忌信息",
        "question_hint": "有什么需要避免的禁忌或过敏情况？"
    }
]

# 类别特有槽位模板
CATEGORY_SPECIFIC_SLOTS = {
    "功能箱包": [
    {
        "slot_name": "gift_holiday",
        "description": "礼赠属性",
        "question_hint": "是否用于送礼场景？比如节日礼物、生日礼物、新年礼物等？"
    },
    {
        "slot_name": "dimensions_capacity",
        "description": "尺寸容量",
        "question_hint": "需要什么尺寸和容量？比如20寸登机箱、24寸旅行箱、31L容量等？"
    },
    {
        "slot_name": "type_and_style",
        "description": "类型与款式",
        "question_hint": "需要什么类型的箱包？比如旅行箱、拉杆箱、登机箱、智能行李箱、电动骑行箱、硬箱、铝拉杆箱、双肩包等？"
    },
    {
        "slot_name": "target_audience",
        "description": "适用人群",
        "question_hint": "主要面向什么人群？比如男女通用、儿童、男友、女友、差旅人士、科技爱好者？"
    },
    {
        "slot_name": "style_appearance",
        "description": "风格外观",
        "question_hint": "偏好什么外观风格？比如时尚箱包、经典复古、彩虹、小瓢虫、智能科技感？"
    },
    {
        "slot_name": "scene_function",
        "description": "使用场景与功能",
        "question_hint": "主要使用场景是什么？比如出行必备、大容量收纳、国际短途商旅、充电宝功能？"
    },
    {
        "slot_name": "origin_place_hint",
        "description": "产地信息",
        "question_hint": "对产地是否有偏好？比如加拿大、中国、意大利、德国？"
    }
],
"男包": [
    {
        "slot_name": "gift_holiday",
        "description": "礼赠属性",
        "question_hint": "是否用于送礼场景？比如节日礼物、生日礼物、新年礼物等？"
    },
    {
        "slot_name": "dimensions_capacity",
        "description": "尺寸容量",
        "question_hint": "需要什么尺寸和容量？比如是否有寸/L等规格要求？"
    },
    {
        "slot_name": "type_and_style",
        "description": "包型",
        "question_hint": "需要什么包型？比如单肩背包、单肩包、腰包、胸包、斜挎包、双肩背包、双肩包、背包、箱包？"
    },
    {
        "slot_name": "target_audience",
        "description": "适用人群",
        "question_hint": "主要面向什么人群？比如女士、男士、男女通用？"
    },
    {
        "slot_name": "material_and_details",
        "description": "材质与细节",
        "question_hint": "对材质和细节有什么要求？比如皮革、翻盖、超长等？"
    },
    {
        "slot_name": "origin_place_hint",
        "description": "产地信息",
        "question_hint": "对产地是否有偏好？比如美国、德国？"
    }
],
"女包（含中性）": [
    {
        "slot_name": "gift_holiday",
        "description": "礼赠属性",
        "question_hint": "是否用于送礼场景？比如节日礼物、生日礼物、新年礼物等？"
    },
    {
        "slot_name": "dimensions_capacity",
        "description": "尺寸容量",
        "question_hint": "需要什么尺寸和容量？比如是否有寸/L等规格要求？"
    },
    {
        "slot_name": "type_and_style",
        "description": "包型",
        "question_hint": "需要什么包型？比如波士顿包、单肩背包、双肩背包、单肩包、斜挎包、托特包、手提包、皮手提包、青蛙包、枕头包、饺子包、妈咪包、胶囊包、腰包、手机包、花桶包、子母包、购物袋、运动挎包、电脑包、旅行包、商务包、健身包、运动包、行李箱、登机包、包包、箱包？"
    },
    {
        "slot_name": "target_audience",
        "description": "适用人群",
        "question_hint": "主要面向什么人群？比如女士、男女通用、中性、男友、女友？"
    },
    {
        "slot_name": "size_weight_desc",
        "description": "尺寸重量描述",
        "question_hint": "对包袋大小或重量有什么偏好？比如大号、中号、小号、超轻？"
    },
    {
        "slot_name": "material_and_details",
        "description": "材质与细节",
        "question_hint": "对材质和细节有什么要求？比如真皮、丹宁牛仔、长毛绒、毛绒、防水、旋扣、大头、吐司、挂件款式随机发、岩石黑、灰棕等？"
    },
    {
        "slot_name": "style_appearance",
        "description": "风格外观",
        "question_hint": "偏好什么风格外观？比如爆款、休闲、轻量、可爱、嬉趣、轻奢、时尚、百搭、学院风、流行包、高级感？"
    },
    {
        "slot_name": "scene_function",
        "description": "使用场景与功能",
        "question_hint": "主要使用场景是什么？比如户外、运动、健身、日常通勤、商务简约？"
    },
    {
        "slot_name": "season",
        "description": "季节属性",
        "question_hint": "是否有季节偏好？比如春夏包、秋冬包？"
    },
    {
        "slot_name": "origin_place_hint",
        "description": "产地信息",
        "question_hint": "对产地是否有偏好？比如中国、美国、意大利？"
    }
],
"家庭清洁": [
    {
        "slot_name": "sub_category",
        "description": "细分类目",
        "question_hint": "需要哪类清洁产品？比如油污净、驱蚊驱虫、地板清洁剂？"
    },
    {
        "slot_name": "cleaning_appeal",
        "description": "清洁诉求",
        "question_hint": "主要清洁诉求是什么？比如去油污、去油渍、去污、清洗剂、除垢？"
    },
    {
        "slot_name": "usage_scene",
        "description": "使用场景",
        "question_hint": "主要用在什么场景？比如油烟机、地板、居家驱蚊？"
    },
    {
        "slot_name": "gift_holiday",
        "description": "礼赠属性",
        "question_hint": "是否用于送礼场景？比如春节、新年、年货等？"
    },
    {
        "slot_name": "specs_value",
        "description": "规格数值",
        "question_hint": "需要什么规格容量？"
    },
    {
        "slot_name": "specs_unit",
        "description": "规格单位",
        "question_hint": "规格单位是什么？比如ml、g、kg、l？"
    },
    {
        "slot_name": "specs_multiplier",
        "description": "规格倍数",
        "question_hint": "是单件还是组合装？比如30ml*2？"
    }
],
"口腔护理": [
    {
        "slot_name": "sub_category",
        "description": "细分类目",
        "question_hint": "需要哪类口腔护理产品？比如牙膏、牙刷、牙刷头、口腔喷雾、替换雾弹？"
    },
    {
        "slot_name": "efficacy",
        "description": "功效",
        "question_hint": "主要功效诉求是什么？比如清洁、美白、亮白、去黄、去渍、清新口气、去口臭？"
    },
    {
        "slot_name": "brushing_experience",
        "description": "刷护体验",
        "question_hint": "对刷毛和使用体验有什么要求？比如软毛、缓震？"
    },
    {
        "slot_name": "ingredients",
        "description": "成分",
        "question_hint": "是否有偏好的成分或配方？比如1.5%粉盐、粉盐、果酸、益生菌？"
    },
    {
        "slot_name": "flavor",
        "description": "口味/香型",
        "question_hint": "偏好什么口味或香型？比如冬青香型、北国雪松口味、清桃气泡？"
    },
    {
        "slot_name": "gift_holiday",
        "description": "礼赠属性",
        "question_hint": "是否用于送礼场景？"
    },
    {
        "slot_name": "specs_value",
        "description": "规格数值",
        "question_hint": "需要什么规格容量？"
    },
    {
        "slot_name": "specs_unit",
        "description": "规格单位",
        "question_hint": "规格单位是什么？比如ml、g、kg、l？"
    },
    {
        "slot_name": "specs_multiplier",
        "description": "规格倍数",
        "question_hint": "是单件还是组合装？"
    }
],
"美发护发": [
    {
        "slot_name": "sub_category",
        "description": "细分类目",
        "question_hint": "需要哪类美发护发产品？比如洗发、护发、发膜、精油、精华素、头皮按摩磨砂膏、染发？"
    },
    {
        "slot_name": "efficacy",
        "description": "功效",
        "question_hint": "主要功效诉求是什么？比如清洁控油、去屑止痒、滋润保湿、修护受损、柔顺顺滑、蓬松丰盈、强韧防断/防脱养发、亮泽护色、舒缓头皮？"
    },
    {
        "slot_name": "hair_type_problem",
        "description": "发质/问题",
        "question_hint": "发质类型或主要问题是什么？比如所有发质、干枯发质、中性发质、干性发质、油性发质、毛躁、打结、分叉、受损、油敏皮？"
    },
    {
        "slot_name": "ingredients",
        "description": "成分",
        "question_hint": "是否有偏好的成分或配方？比如芦荟、生姜、蜂胶、红参、复活草、山茶花、草本、玻尿酸、咖啡因、多肽、红没药醇、氨基酸、维他命b、摩洛哥油、摩洛哥坚果籽、玫瑰精油、三重蚕丝、无硅油、植物配方、无氨味？"
    },
    {
        "slot_name": "form",
        "description": "剂型",
        "question_hint": "偏好什么剂型？比如洗发液态、护发乳液、发膜、油类/精华、膏/水类、染发霜？"
    },
    {
        "slot_name": "care_area",
        "description": "护理部位",
        "question_hint": "主要护理哪个部位？比如头发/发丝、头皮、发根？"
    },
    {
        "slot_name": "fragrance",
        "description": "香氛",
        "question_hint": "偏好什么香型？比如香氛、经典香氛、芳氛、草本香、山茶花、玫瑰、香水染发、花香、果香？"
    },
    {
        "slot_name": "usage_scene",
        "description": "使用场景",
        "question_hint": "使用方式或时机是什么？比如免洗、洗护合一、日常洗护、染色&漂色洗发？"
    },
    {
        "slot_name": "gift_holiday",
        "description": "礼赠属性",
        "question_hint": "是否用于送礼场景？比如春节、情人节、圣诞、生日、母亲节、父亲节？"
    },
    {
        "slot_name": "specs_value",
        "description": "规格数值",
        "question_hint": "需要什么规格容量？"
    },
    {
        "slot_name": "specs_unit",
        "description": "规格单位",
        "question_hint": "规格单位是什么？比如ml、g、kg、l？"
    },
    {
        "slot_name": "specs_multiplier",
        "description": "规格倍数",
        "question_hint": "是单件还是组合装？"
    }
],
"女性护理": [
    {
        "slot_name": "efficacy",
        "description": "功效",
        "question_hint": "主要功效诉求是什么？比如保湿补水类、清洁净澈类、提亮焕亮类、修护舒缓类、紧致塑形类、肤感提升类？"
    },
    {
        "slot_name": "care_area",
        "description": "护理部位",
        "question_hint": "主要护理哪个部位？比如身体、手部、手甲、胸部、私处、眼部、鼻、头发？"
    },
    {
        "slot_name": "fragrance",
        "description": "香调",
        "question_hint": "偏好什么香型？比如玫瑰、薰衣草、茉莉、橙花、牡丹、柑橘、清柚、加州柚里、庭中橘树、绿野浆果、柠檬天竺葵、活力马鞭草、海茴香、雪松、杉间、木香调、青绿木质调、小豆蔻、广藿香、白茶、牛奶味、清冽香、珍华乌木香型、牡丹与胭红麂绒、夜幕之水、松间照？"
    },
    {
        "slot_name": "form",
        "description": "剂型/形态",
        "question_hint": "偏好什么剂型？比如清洁液态、油状、霜类、乳液类、啫喱/凝胶类、喷雾类、泡沫类、固体皂类、贴片类？"
    },
    {
        "slot_name": "target_audience",
        "description": "适用人群",
        "question_hint": "主要面向什么人群？比如女士、男士、婴儿？"
    },
    {
        "slot_name": "gift_holiday",
        "description": "礼赠属性",
        "question_hint": "是否用于送礼场景？"
    },
    {
        "slot_name": "specs_value",
        "description": "规格数值",
        "question_hint": "需要什么规格容量？"
    },
    {
        "slot_name": "specs_unit",
        "description": "规格单位",
        "question_hint": "规格单位是什么？比如ml、g、kg、l？"
    },
    {
        "slot_name": "specs_multiplier",
        "description": "规格倍数",
        "question_hint": "是单件还是组合装？"
    }
],
"纸品清洗": [
    {
        "slot_name": "sub_category",
        "description": "细分类目",
        "question_hint": "需要哪类清洗产品？比如洗衣液、液态洗衣皂液？"
    },
    {
        "slot_name": "fragrance",
        "description": "香型",
        "question_hint": "偏好什么香型？比如薰衣草、马赛味？"
    },
    {
        "slot_name": "gift_holiday",
        "description": "礼赠属性",
        "question_hint": "是否用于送礼场景？"
    },
    {
        "slot_name": "specs_value",
        "description": "规格数值",
        "question_hint": "需要什么规格容量？"
    },
    {
        "slot_name": "specs_unit",
        "description": "规格单位",
        "question_hint": "规格单位是什么？比如ml、g、kg、l？"
    },
    {
        "slot_name": "specs_multiplier",
        "description": "规格倍数",
        "question_hint": "是单件还是组合装？"
    }
],
"时尚配饰": [
    {
        "slot_name": "accessory_category",
        "description": "标准化品类",
        "question_hint": "需要什么类型的配饰？比如项链吊坠、手部饰品、耳饰、戒指？"
    },
    {
        "slot_name": "material_tags",
        "description": "主要材质",
        "question_hint": "偏好什么材质？比如银质、金属类木质香料类？"
    },
    {
        "slot_name": "style_tags",
        "description": "整体风格",
        "question_hint": "偏好什么风格？比如简约百搭、优雅浪漫、复古？"
    },
    {
        "slot_name": "theme_tags",
        "description": "主题元素",
        "question_hint": "喜欢什么主题或设计元素？比如花卉植物、动物形象、好运祈福？"
    },
    {
        "slot_name": "color_raw",
        "description": "原始颜色词",
        "question_hint": "偏好什么颜色？"
    },
    {
        "slot_name": "color_family",
        "description": "标准化色系",
        "question_hint": "偏好什么色系？"
    },
    {
        "slot_name": "wearing_feature_tags",
        "description": "佩戴/结构卖点",
        "question_hint": "对佩戴方式或结构有什么要求？比如开口设计、可调节？"
    }
],
"腕表": [
    {
        "slot_name": "watch_origin_category",
        "description": "腕表来源/产地类别",
        "question_hint": "偏好什么来源或产地的腕表？比如欧美腕表、国产腕表、日韩腕表、瑞士腕表？"
    },
    {
        "slot_name": "movement_type",
        "description": "驱动类型/机芯类型",
        "question_hint": "偏好什么驱动方式？比如石英表、自动机械表、手动机械表？"
    },
    {
        "slot_name": "material_tags",
        "description": "主要材质",
        "question_hint": "偏好什么材质？比如精钢、树脂、皮革、陶瓷、镀金？"
    },
    {
        "slot_name": "style_tags",
        "description": "整体风格",
        "question_hint": "偏好什么风格？比如经典、摩登、复古、北欧风、运动休闲、商务通勤、优雅女性？"
    },
    {
        "slot_name": "design_element_tags",
        "description": "设计元素/系列",
        "question_hint": "喜欢什么设计元素或系列？比如方表设计、满天星、星环、轮时代系列还是其他？"
    },
    {
        "slot_name": "target_gender",
        "description": "适用人群/性别",
        "question_hint": "主要面向什么人群？比如女性、男性、中性、情侣款？"
    }
],
"品质生活": [
    {
        "slot_name": "home_living_category",
        "description": "居家分类",
        "question_hint": "需要什么类型的居家商品？"
    },
    {
        "slot_name": "textile_material_tags",
        "description": "纺织/寝居材质",
        "question_hint": "对家纺/寝居材质有什么偏好？"
    },
    {
        "slot_name": "utensil_material_tags",
        "description": "器具材质",
        "question_hint": "对器具材质有什么偏好？"
    },
    {
        "slot_name": "sleep_function_tags",
        "description": "睡眠功能",
        "question_hint": "对睡眠用品有什么功能要求？"
    },
    {
        "slot_name": "drinkware_function_tags",
        "description": "杯壶功能",
        "question_hint": "对杯壶有什么功能要求？比如保温保冷型、便携随行型？"
    },
    {
        "slot_name": "home_style_tags",
        "description": "家居风格",
        "question_hint": "偏好什么风格？比如国风文创、婚庆喜庆、博物馆馆藏风？"
    },
    {
        "slot_name": "capacity_spec_raw",
        "description": "规格容量",
        "question_hint": "对规格容量有什么要求？"
    }
],
    "护肤": [
        {
            "slot_name": "skin_type",
            "description": "肤质类型",
            "question_hint": "收礼人的肤质偏干、偏油还是混合/敏感肌？"
        },
        {
            "slot_name": "skin_concern",
            "description": "护肤诉求",
            "question_hint": "主要诉求是保湿修护、控油祛痘、抗老紧致、提亮淡斑还是舒缓维稳？"
        },
        {
            "slot_name": "ingredient_preference",
            "description": "成分偏好",
            "question_hint": "是否对香精、酒精、酸类等成分有忌口或过敏史？"
        },
        {
            "slot_name": "usage_scenario",
            "description": "使用场景",
            "question_hint": "日常使用场景是早晚基础护理还是偏功效型精华/面霜？"
        }
    ],
    "美妆": [
        {
            "slot_name": "makeup_frequency",
            "description": "化妆频率",
            "question_hint": "收礼人平时化妆频率高吗（新手/日常/精致妆）？"
        },
        {
            "slot_name": "makeup_preference",
            "description": "化妆偏好",
            "question_hint": "更偏好底妆、眼妆、唇妆还是工具类？"
        },
        {
            "slot_name": "skin_tone",
            "description": "肤色",
            "question_hint": "肤色大致是偏白/自然/偏深？"
        }
    ],
    "女装（含中性）": [
    {
        "slot_name": "season_year",
        "description": "季节/年份时效",
        "question_hint": "关注什么季节或年份款式？"
    },
    {
        "slot_name": "warmth_level",
        "description": "保暖等级/充绒类别",
        "question_hint": "需要什么保暖等级？比如90绒、鹅绒、极寒、保暖、蓄热、加厚、轻薄？"
    },
    {
        "slot_name": "outdoor_performance",
        "description": "三防/户外性能",
        "question_hint": "对户外防护性能有什么要求？比如防风、防水、防污、三防、防泼水？"
    },
    {
        "slot_name": "wearing_scene",
        "description": "穿着场景",
        "question_hint": "主要在什么场景穿着？"
    },
    {
        "slot_name": "portability",
        "description": "轻便性",
        "question_hint": "对轻便性有什么要求？"
    },
    {
        "slot_name": "length_cut",
        "description": "长度剪裁",
        "question_hint": "偏好什么长度和版型？比如短款、中长款、长款、收腰、显瘦、修身、宽松？"
    },
    {
        "slot_name": "collar_design",
        "description": "领型/连帽设计",
        "question_hint": "偏好什么领型设计？比如立领、连帽、圆领、毛领、翻领、V领？"
    },
    {
        "slot_name": "craft_details",
        "description": "工艺细节",
        "question_hint": "对工艺细节有什么偏好？比如葫芦纹、叠穿？"
    },
    {
        "slot_name": "style_tags",
        "description": "风格标签",
        "question_hint": "偏好什么风格？比如联名、合作款、公益、休闲、时尚？"
    },
    {
        "slot_name": "suitable_gender",
        "description": "适用性别",
        "question_hint": "主要面向什么性别？比如女性、男性、中性/通用？"
    }
],
"内衣": [
    {
        "slot_name": "sock_height",
        "description": "袜筒高度",
        "question_hint": "需要什么袜筒高度？比如长筒、中筒、短筒、船袜、及膝、过膝？"
    },
    {
        "slot_name": "sleeve_length",
        "description": "袖长",
        "question_hint": "需要什么袖长？比如长袖、短袖、七分袖、九分袖、无袖？"
    },
    {
        "slot_name": "material",
        "description": "材质",
        "question_hint": "偏好什么材质？比如全棉、桑蚕丝、蚕丝、精梳棉、莫代尔、天丝？"
    },
    {
        "slot_name": "health_function",
        "description": "健康防护功能",
        "question_hint": "需要什么健康防护功能？比如抗菌、防臭、静脉曲张、压力？"
    },
    {
        "slot_name": "sport_scene",
        "description": "运动场景",
        "question_hint": "用于什么运动场景？"
    },
    {
        "slot_name": "season_attr",
        "description": "季节属性",
        "question_hint": "需要什么季节属性？"
    },
    {
        "slot_name": "style_tags",
        "description": "风格标签",
        "question_hint": "偏好什么风格？比如联名、合作款、公益、休闲、时尚、潮流？"
    },
    {
        "slot_name": "suitable_gender",
        "description": "适用性别",
        "question_hint": "主要面向什么性别？比如女性、男性、中性/通用？"
    }
],
"男装": [
    {
        "slot_name": "fill_power",
        "description": "充绒参数",
        "question_hint": "对充绒有什么要求？比如90绒、80绒、含绒量、鹅绒、鸭绒、加厚、轻薄？"
    },
    {
        "slot_name": "protection_level",
        "description": "防护等级",
        "question_hint": "需要什么防护等级？比如三防、防风、防水、防油、防污、抗寒、防寒？"
    },
    {
        "slot_name": "craft_structure",
        "description": "工艺结构",
        "question_hint": "对工艺结构有什么要求？比如连帽可脱卸、立领、可拆卸、便携收纳？"
    },
    {
        "slot_name": "length_cut",
        "description": "长度剪裁",
        "question_hint": "偏好什么长度？比如短款、常规款、中长款、长款、派克长款？"
    },
    {
        "slot_name": "matching_attr",
        "description": "搭配属性",
        "question_hint": "偏好什么搭配风格？比如百搭、休闲、商务、户外、通勤？"
    },
    {
        "slot_name": "series_model",
        "description": "系列/型号",
        "question_hint": "是否有特定系列或型号偏好？比如CHATEAU PARKA、B250131005等？"
    },
    {
        "slot_name": "climate_suitability",
        "description": "气候适配",
        "question_hint": "适合什么气候？比如极寒、抗寒、秋季、冬季、薄款、厚款？"
    },
    {
        "slot_name": "activity_scene",
        "description": "活动场景",
        "question_hint": "主要在什么场景活动？比如户外、探险、都市、通勤、差旅、商务、休闲？"
    },
    {
        "slot_name": "suitable_gender",
        "description": "适用性别",
        "question_hint": "主要面向什么性别？比如女性、男性、中性/通用？"
    }
],
"葡萄酒": [
    {
        "slot_name": "wine_body_type",
        "description": "酒体类型",
        "question_hint": "需要什么酒体类型？比如红葡萄酒、白葡萄酒、起泡酒、桃红葡萄酒？"
    },
    {
        "slot_name": "wine_volume",
        "description": "容量规格",
        "question_hint": "需要什么容量？"
    },
    {
        "slot_name": "wine_region",
        "description": "产区/产地",
        "question_hint": "偏好什么产区或产地？比如波尔多、勃艮第、香槟？"
    },
    {
        "slot_name": "grape_variety",
        "description": "葡萄品种",
        "question_hint": "偏好什么葡萄品种？比如赤霞珠、梅洛、西拉或其他？"
    },
    {
        "slot_name": "wine_sweetness",
        "description": "口感甜度",
        "question_hint": "偏好什么甜度？比如干型、半干、半甜、甜型？"
    },
    {
        "slot_name": "wine_vintage",
        "description": "年份",
        "question_hint": "是否有特定年份偏好？"
    }
],
"休闲食品": [
    {
        "slot_name": "food_subtype",
        "description": "食品子类型",
        "question_hint": "需要什么类型的休闲食品？比如饼干、巧克力？"
    },
    {
        "slot_name": "flavor_tags",
        "description": "风味标签",
        "question_hint": "偏好什么风味？比如原味、咸鲜、香辣"
    },
    {
        "slot_name": "packaging_type",
        "description": "包装形式",
        "question_hint": "偏好什么包装？？"
    },
    {
        "slot_name": "core_ingredient",
        "description": "核心成分",
        "question_hint": "对成分有什么偏好或要求？"
    },
    {
        "slot_name": "net_weight",
        "description": "净含量规格",
        "question_hint": "需要什么规格？"
    }
],
"烈酒与白酒": [
    {
        "slot_name": "liquor_type",
        "description": "烈酒大类",
        "question_hint": "需要什么类型的酒？"
    },
    {
        "slot_name": "whisky_craft",
        "description": "威士忌工艺",
        "question_hint": "对威士忌工艺有什么偏好？"
    },
    {
        "slot_name": "baijiu_aroma",
        "description": "白酒香型",
        "question_hint": "偏好什么白酒香型？"
    },
    {
        "slot_name": "alcohol_degree",
        "description": "酒精度",
        "question_hint": "需要什么酒精度？"
    },
    {
        "slot_name": "aging_years",
        "description": "陈酿年份",
        "question_hint": "需要什么陈酿年份？"
    },
    {
        "slot_name": "liquor_volume",
        "description": "容量规格",
        "question_hint": "需要什么容量？"
    },
    {
        "slot_name": "liquor_tier",
        "description": "档次定位",
        "question_hint": "需要什么档次？"
    }
],
"冲调与乳品茶": [
    {
        "slot_name": "beverage_form",
        "description": "形态",
        "question_hint": "需要什么形态？比如挂耳、粉末、液体、胶囊、冻干、散装、袋泡、罐装？"
    },
    {
        "slot_name": "beverage_craft",
        "description": "工艺",
        "question_hint": "偏好什么工艺？"
    },
    {
        "slot_name": "nutrition_tags",
        "description": "营养标签",
        "question_hint": "对营养有什么要求？"
    },
    {
        "slot_name": "tea_type",
        "description": "茶叶类型",
        "question_hint": "需要什么茶叶类型？"
    },
    {
        "slot_name": "net_weight",
        "description": "净含量",
        "question_hint": "需要什么规格？"
    }
],
"粮油调味速食": [
    {
        "slot_name": "oil_type",
        "description": "油品类型",
        "question_hint": "需要什么油品类型？比如橄榄油、花生油、菜籽油？"
    },
    {
        "slot_name": "oil_grade",
        "description": "等级/工艺",
        "question_hint": "对等级或工艺有什么要求？比如特级初榨、初榨、精炼、冷榨、原装进口？"
    },
    {
        "slot_name": "convenience_type",
        "description": "速食类型",
        "question_hint": "需要什么速食类型？比如速冻、方便速食、自热、速溶、即食、冷冻？"
    },
    {
        "slot_name": "net_weight",
        "description": "净含量",
        "question_hint": "需要什么规格？"
    }
],
"服配": [
    {
        "slot_name": "product_form",
        "description": "产品形态",
        "question_hint": "需要什么产品形态？比如方巾、长巾、窄版、宽版、空顶、棒球帽、渔夫帽？"
    },
    {
        "slot_name": "material_craft",
        "description": "材质工艺",
        "question_hint": "偏好什么材质工艺？比如桑蚕丝、真丝、提花、鎏金、幻彩、印花、刺绣？"
    },
    {
        "slot_name": "package_spec",
        "description": "规格包装",
        "question_hint": "对包装有什么要求？比如礼盒、盒装、套装、组合、单品？"
    },
    {
        "slot_name": "ip_culture",
        "description": "IP/文创关联",
        "question_hint": "是否有IP或文创偏好？比如大英博物馆、埃及、敦煌"
    },
    {
        "slot_name": "design_theme",
        "description": "设计主题",
        "question_hint": "偏好什么设计主题？比如雀枝锦、鎏金幻彩、花卉、系列、经典？"
    },
    {
        "slot_name": "brand_element",
        "description": "品牌经典元素",
        "question_hint": "是否有品牌经典元素偏好？"
    },
    {
        "slot_name": "function_scene",
        "description": "功能场景",
        "question_hint": "主要使用场景是什么？"
    }
],


    "服装（男女/内衣/童装）": [
        {
            "slot_name": "size_info",
            "description": "尺码信息",
            "question_hint": "收礼人大致身高体重/常穿尺码（或平时穿S/M/L）？"
        },
        {
            "slot_name": "wearing_scenario",
            "description": "穿着场景",
            "question_hint": "更常见的穿着场景是通勤、休闲、运动还是正式场合？"
        },
        {
            "slot_name": "style_preference",
            "description": "风格偏好",
            "question_hint": "偏好版型（宽松/合身）、颜色（深色/浅色/亮色）和风格（简约/潮流/复古）？"
        },
        {
            "slot_name": "fabric_preference",
            "description": "面料偏好",
            "question_hint": "面料更喜欢棉、羊毛、真丝、功能面料，是否有皮肤敏感？"
        }
    ],
    "鞋靴": [
        {
            "slot_name": "shoe_size",
            "description": "鞋码",
            "question_hint": "常穿鞋码以及脚型是否偏宽/偏瘦、是否容易磨脚？"
        },
        {
            "slot_name": "usage_purpose",
            "description": "用途",
            "question_hint": "用途更偏通勤、休闲、运动还是正式场合？"
        },
        {
            "slot_name": "style_preference",
            "description": "款式偏好",
            "question_hint": "偏好款式（运动鞋/皮鞋/靴子/乐福等）和颜色？"
        },
        {
            "slot_name": "comfort_requirement",
            "description": "舒适需求",
            "question_hint": "对舒适需求（软底、支撑、增高、防滑、防水）有要求吗？"
        }
    ],
    "数码影音": [
        {
            "slot_name": "device_type",
            "description": "设备类型",
            "question_hint": "主要使用的是手机通讯相关（耳机、充电、配件）还是影音娱乐（音箱、投影）？"
        },
        {
            "slot_name": "usage_scenario",
            "description": "使用场景",
            "question_hint": "使用场景是通勤、运动、居家还是办公？"
        },
        {
            "slot_name": "device_ecosystem",
            "description": "设备生态",
            "question_hint": "是否有设备生态偏好（iOS/安卓、特定品牌）？"
        },
        {
            "slot_name": "feature_priority",
            "description": "功能优先级",
            "question_hint": "更看重音质、降噪、续航、便携还是颜值？"
        }
    ],
    "食品与冲饮（非酒）": [
        {
            "slot_name": "dietary_restrictions",
            "description": "饮食限制",
            "question_hint": "是否有忌口或过敏（坚果、乳糖、麸质、海鲜等），以及是否控糖/控脂/素食？"
        },
        {
            "slot_name": "taste_preference",
            "description": "口味偏好",
            "question_hint": "口味喜欢甜/咸/辣、清淡还是重口？"
        },
        {
            "slot_name": "food_category",
            "description": "食品类别",
            "question_hint": "更偏好休闲零食、粮油调味速食、海鲜水产、咖啡冲饮还是茗茶？"
        }
    ],
    "酒类": [
        {
            "slot_name": "alcohol_type",
            "description": "酒类偏好",
            "question_hint": "平时更常喝葡萄酒、洋酒还是国酒？"
        },
        {
            "slot_name": "taste_preference",
            "description": "口感偏好",
            "question_hint": "偏好口感（清爽/醇厚、甜/干、柔和/烈）？"
        },
        {
            "slot_name": "drinking_scenario",
            "description": "饮用场景",
            "question_hint": "饮用场景是宴请、收藏、家饮？"
        },
        {
            "slot_name": "brand_preference",
            "description": "品牌偏好",
            "question_hint": "是否需要特定品牌、年份/产区/香型？"
        }
    ]
}

# 为其他类别提供默认模板（可以后续扩展）
for category in ["香氛", "个护清洁", "家庭清洁", "箱包出行", "旅行用品", 
                 "配饰（钟表/眼镜/珠宝）", "母婴", "文具", "家居与厨房", 
                 "营养保健（滋补/维矿/功能健康）", "宠物", "礼赠/营销"]:
    if category not in CATEGORY_SPECIFIC_SLOTS:
        CATEGORY_SPECIFIC_SLOTS[category] = [
            {
                "slot_name": "specific_preferences",
                "description": "具体偏好",
                "question_hint": "对品牌、风格、功能有什么特别的偏好吗？"
            },
            {
                "slot_name": "usage_scenario",
                "description": "使用场景",
                "question_hint": "主要的使用场景是什么？"
            }
        ]

QUESTION_MODE_CONFIG = {
    "brief": {
        "max_questions": 2,
        "target_length": "40-80字",
        "allow_direct_recommend_hint": True,
    },
    "normal": {
        "max_questions": 3,
        "target_length": "80-120字",
        "allow_direct_recommend_hint": False,
    },
    "detailed": {
        "max_questions": 5,
        "target_length": "120-200字",
        "allow_direct_recommend_hint": False,
    },
}

PRIORITY_WEIGHT = {"high": 0, "medium": 1, "low": 2}
PRIORITY_SCORE = {"high": 30, "medium": 20, "low": 10}
PRODUCT_POOL_CONFIDENCE_THRESHOLD = 0.35
PRODUCT_POOL_FOCUS_THRESHOLD = 0.7
PRODUCT_POOL_BROAD_THRESHOLD = 0.55
PRODUCT_POOL_MIN_PRODUCTS = 5
PRODUCT_POOL_PRICE_HIGH_RATIO = 3.0
PRODUCT_POOL_PRICE_MEDIUM_RATIO = 1.8
POOL_SIGNAL_SLOT_BOOSTS = {
    "price": {
        "budget_min": 18,
        "budget_max": 18,
    },
    "sub_category": {
        "sub_category": 18,
        "type_and_style": 14,
        "food_category": 14,
        "alcohol_type": 14,
        "watch_origin_category": 10,
        "home_living_category": 10,
        "accessory_category": 10,
    },
    "scene": {
        "scene_function": 14,
        "usage_scene": 14,
        "occasion": 10,
        "drinking_scenario": 10,
    },
    "efficacy": {
        "efficacy": 16,
        "skin_concern": 14,
        "cleaning_appeal": 12,
    },
    "style": {
        "style_appearance": 14,
        "style_tags": 12,
    },
    "brand": {
        "brand_preference": 18,
    },
    "taste": {
        "taste_preference": 12,
        "flavor": 10,
    },
}
POOL_SIGNAL_KEYWORDS = {
    "scene": ("通勤", "旅行", "户外", "商务", "日常", "运动", "宴请", "家用", "差旅", "健身"),
    "efficacy": ("保湿", "修护", "抗老", "提亮", "控油", "祛痘", "舒缓", "美白", "清洁", "滋补", "去屑", "防脱"),
    "style": ("简约", "轻奢", "可爱", "商务", "复古", "运动", "休闲", "优雅", "潮流", "百搭", "经典"),
    "taste": ("甜", "咸", "辣", "清淡", "醇厚", "清爽", "柔和", "干", "烈", "果味", "奶香"),
}
CORE_INFO_SLOTS = {
    "recipient_relation",
    "occasion",
    "budget_min",
    "budget_max",
    "recipient_preferences",
    "taboo",
}
LOW_WILLINGNESS_KEYWORDS = (
    "随便",
    "都行",
    "你看着办",
    "直接推荐",
    "直接给",
    "不清楚",
    "没要求",
    "不用问太多",
    "别问太多",
    "无所谓",
    "都可以",
    "少问",
    "问一个",
    "普通款",
    "普通的",
    "普通就行",
    "普通一点",
    "基础款",
    "基础的",
    "基础就行",
    "基础一点",
    "简单点",
    "简单一点",
    "简单的",
    "简单就行",
    "不用太复杂",
    "别太复杂",
    "不用太讲究",
    "别太讲究",
    "随意一点",
    "差不多就行",
    "常规款",
    "常规的",
)
DIRECT_RECOMMEND_KEYWORDS = (
    "直接推荐",
    "直接给",
    "直接来",
    "你看着办",
    "随便推荐",
    "不用问",
    "先推荐",
    "先给",
)
BRIEF_ONLY_KEYWORDS = (
    "少问",
    "少问点",
    "别问太多",
    "不用问太多",
    "问一个",
    "一个问题",
)
EXIT_LIKE_KEYWORDS = (
    "不用推荐",
    "不推荐了",
    "不用了",
    "不需要了",
    "先这样",
    "结束",
)
HIGH_RISK_CATEGORIES = {
    "护肤",
    "食品与冲饮（非酒）",
    "休闲食品",
    "酒类",
    "母婴",
    "营养保健（滋补/维矿/功能健康）",
}
HIGH_RISK_SAFETY_SLOTS = {
    "护肤": ("taboo", "recipient_preferences", "skin_type"),
    "食品与冲饮（非酒）": ("taboo", "food_taboo", "recipient_preferences"),
    "休闲食品": ("taboo", "food_taboo", "recipient_preferences"),
    "酒类": ("taboo", "taste_preference", "drinking_scenario"),
    "母婴": ("taboo", "recipient_age", "target_audience"),
    "营养保健（滋补/维矿/功能健康）": ("taboo",),
}
HIGH_WILLINGNESS_KEYWORDS = (
    "品牌",
    "预算",
    "场景",
    "禁忌",
    "过敏",
    "偏好",
    "风格",
    "功效",
    "用途",
    "使用",
    "材质",
    "颜色",
    "口味",
    "香型",
    "肤质",
    "敏感肌",
    "修护",
    "抗老",
    "区别",
    "哪个好",
    "怎么选",
    "比较",
)
HIGH_PRIORITY_SLOT_NAMES = {
    "recipient_relation",
    "budget_max",
    "recipient_preferences",
    "occasion",
    "taboo",
    "sub_category",
    "type_and_style",
    "dimensions_capacity",
    "scene_function",
    "usage_scene",
    "usage_scenario",
    "efficacy",
    "skin_type",
    "skin_concern",
    "hair_type_problem",
    "cleaning_appeal",
    "taste_preference",
    "drinking_scenario",
    "alcohol_type",
    "food_taboo",
    "flavor_preference",
    "feature_priority",
}
MEDIUM_PRIORITY_SLOT_NAMES = {
    "recipient_gender",
    "recipient_age",
    "target_audience",
    "style_appearance",
    "material_and_details",
    "ingredients",
    "flavor",
    "brand_preference",
    "size_weight_desc",
    "gift_holiday",
    "package_preference",
    "color_preference",
    "wearing_feature_tags",
}
LOW_PRIORITY_SLOT_NAMES = {
    "origin_place_hint",
    "season",
    "specs_unit",
    "specs_multiplier",
    "specs_value",
    "year_preference",
    "capacity",
}

# 动态追问话术生成提示词
DYNAMIC_QUESTION_PROMPT = """你是一个专业的送礼场景导购。规则层已经选出了本轮最值得追问的槽位，你只负责把这些槽位组装成自然流畅的中文追问话术：

## 用户信息：{user_info}
## 当前品类：{category_name}
## 已填信息：{filled_slots_summary}
## 当前追问模式：{question_mode}
## 最多问题数：{max_questions}
## 目标话术长度：{target_length}
## 最终选中的槽位：
{slots_to_ask}

## 生成要求：
1. 话术要自然流畅，像真人对话一样
2. 要体现对已填信息的理解，避免重复询问；但不要声称用户说过他们没有提到的事情，系统推测的信息不应包装成用户原话
3. 只能围绕“最终选中的槽位”追问，不允许新增任何未选中的槽位或问题
4. 语气要友好、专业、有帮助
5. 不要使用列表形式，要用自然语言
6. 将多个问题有机地融合成一段导购话术，不要机械罗列槽位名
7. 如果当前追问模式是 brief，话术必须明显简洁，不超过最多问题数；如果最多问题数为 1，只能生成 1 个问句，并可以提示“如果暂时不确定，也可以先推荐”
8. 话术长度控制在目标范围内

请直接输出追问话术，不要有其他内容。
"""


def prepare_detailed_dimensions(state: GiftRecommendationState) -> Dict[str, Any]:
    if not state.selected_category:
        return {}

    if "combined_message" in state.detailed_dimensions:
        return {
            "reuse_existing": True,
            "payload": dict(state.detailed_dimensions),
            "slots_to_ask": [],
            "category_name": str(
                state.detailed_dimensions.get("template_category_name")
                or state.detailed_dimensions.get("category_name")
                or state.selected_category.category_name
            ),
        }

    template_category_id = _get_template_category_id(state)
    category_name = template_category_id or state.selected_category.category_name
    user_willingness = _detect_user_willingness(state)
    direct_recommend_decision = _build_direct_recommend_decision(
        state,
        category_name,
        user_willingness,
    )
    product_pool_insight = _analyze_product_pool_for_followup(state)
    slots_to_ask, question_mode, user_willingness, info_sufficiency = _select_slots_to_ask(
        state,
        category_name,
        safety_guard_slot=direct_recommend_decision["safety_guard_slot"],
        product_pool_insight=product_pool_insight,
    )
    should_direct_recommend = direct_recommend_decision["should_direct_recommend"]

    return {
        "reuse_existing": False,
        "slots_to_ask": slots_to_ask,
        "category_name": category_name,
        "payload": {
            "questions": [],
            "category_name": state.selected_category.category_name,
            "template_category_name": template_category_id,
            "question_mode": question_mode,
            "slots_asked": [slot["slot_name"] for slot in slots_to_ask],
            "user_willingness": user_willingness,
            "info_sufficiency": info_sufficiency,
            "should_direct_recommend": should_direct_recommend,
            "direct_recommend_reason": direct_recommend_decision["direct_recommend_reason"],
            "direct_recommend_blocked_reason": direct_recommend_decision["direct_recommend_blocked_reason"],
            "safety_guard_slot": direct_recommend_decision["safety_guard_slot"],
            "product_pool_insight": product_pool_insight,
            "pool_status": product_pool_insight.get("pool_status", ""),
            "pool_dominant_differences": product_pool_insight.get("dominant_differences", []),
            "pool_slot_boosts": product_pool_insight.get("slot_boosts", {}),
        },
    }


def generate_detailed_dimensions_message(
    state: GiftRecommendationState,
    plan: Dict[str, Any],
) -> str:
    payload = plan.get("payload", {}) if isinstance(plan, dict) else {}
    if plan.get("reuse_existing"):
        return str(payload.get("combined_message", "") or "")

    category_name = str(plan.get("category_name", "") or "")
    slots_to_ask = plan.get("slots_to_ask", []) or []
    question_mode = str(payload.get("question_mode", "") or "normal")
    if payload.get("should_direct_recommend"):
        return _build_direct_recommend_message(category_name)

    combined_message = _generate_dynamic_follow_up(
        state,
        category_name,
        slots_to_ask,
        question_mode,
    )
    if combined_message:
        return combined_message
    return _build_fallback_follow_up(
        category_name,
        slots_to_ask,
        _build_filled_slots_summary(state),
        question_mode,
    )


def apply_detailed_dimensions_plan(
    state: GiftRecommendationState,
    plan: Dict[str, Any],
    combined_message: str,
) -> GiftRecommendationState:
    if not plan:
        return state
    payload = dict(plan.get("payload", {}) or {})
    payload["combined_message"] = combined_message or ""
    state.detailed_dimensions = payload
    return state


def detailed_dimensions(state: GiftRecommendationState) -> GiftRecommendationState:
    plan = prepare_detailed_dimensions(state)
    if not plan:
        return state
    combined_message = generate_detailed_dimensions_message(state, plan)
    return apply_detailed_dimensions_plan(state, plan, combined_message)


def _generate_dynamic_follow_up(
    state: GiftRecommendationState,
    category_name: str,
    slots_to_ask: List[Dict],
    question_mode: str,
) -> str:
    """
    动态生成追问话术
    """
    if not slots_to_ask:
        return ""
    
    # 构建已填信息摘要
    filled_slots_summary = _build_filled_slots_summary(state)
    mode_config = QUESTION_MODE_CONFIG[question_mode]
    slots_description = _build_slots_prompt(slots_to_ask)
    
    # 获取用户历史话语（来自feature extraction阶段）
    user_info = _get_user_input_history(state)
    
    # 使用LLM生成自然话术
    prompt = DYNAMIC_QUESTION_PROMPT.format(
        user_info=user_info,
        category_name=category_name,
        filled_slots_summary=filled_slots_summary,
        question_mode=question_mode,
        max_questions=mode_config["max_questions"],
        target_length=mode_config["target_length"],
        slots_to_ask=slots_description,
    )
    
    try:
        follow_up_text = call_text(
            prompt=prompt,
            system_prompt="你是一个专业的送礼场景对话专家，擅长生成自然流畅的追问话术。",
            temperature=0.7
        )
        return follow_up_text.strip()
    except Exception as e:
        print(f"动态话术生成失败: {e}")
        return _build_fallback_follow_up(
            category_name,
            slots_to_ask,
            filled_slots_summary,
            question_mode,
        )


def _get_user_input_history(state: GiftRecommendationState) -> str:
    """
    获取用户的历史话语信息
    """
    if not hasattr(state, "chat_history") or not state.chat_history:
        return "用户未提供详细信息"
    
    # 从聊天历史中提取用户的话语
    user_messages = []
    for message in state.chat_history:
        if message.get("role") == "user" and message.get("content"):
            user_messages.append(message["content"])
    
    if not user_messages:
        return "用户未提供详细信息"
    
    # 返回最近几条用户消息（避免过长）
    recent_messages = user_messages[-3:]  # 取最近3条消息
    return "; ".join(recent_messages)


def _get_slots_to_ask(state: GiftRecommendationState, category_name: str) -> List[Dict]:
    """
    获取需要追问的槽位列表
    """
    slots_to_ask, _, _, _ = _select_slots_to_ask(state, category_name)
    return slots_to_ask


def _select_slots_to_ask(
    state: GiftRecommendationState,
    category_name: str,
    safety_guard_slot: str = "",
    product_pool_insight: Dict[str, Any] = None,
) -> Tuple[List[Dict], str, str, str]:
    """根据用户意愿、信息充足度和槽位优先级选择本轮要问的槽位。"""
    product_pool_insight = product_pool_insight or _analyze_product_pool_for_followup(state)
    user_willingness = _detect_user_willingness(state)
    info_sufficiency = _detect_info_sufficiency(state)
    question_mode = _resolve_question_mode(user_willingness, info_sufficiency)
    mode_config = QUESTION_MODE_CONFIG[question_mode]
    max_questions = mode_config["max_questions"]
    if question_mode == "brief" and _has_single_question_request(state):
        max_questions = 1
    if product_pool_insight.get("pool_status") == "focused":
        max_questions = min(max_questions, 1)

    candidate_slots: List[Dict] = []
    for index, slot in enumerate(COMMON_SLOTS_TEMPLATE):
        normalized = _normalize_slot_config(slot, index=index, source="common")
        if not _is_slot_filled(state, normalized["slot_name"]):
            candidate_slots.append(normalized)

    category_specific_slots = CATEGORY_SPECIFIC_SLOTS.get(category_name, [])
    for index, slot in enumerate(category_specific_slots):
        normalized = _normalize_slot_config(slot, index=index, source="category")
        if not _is_slot_filled(state, normalized["slot_name"]):
            candidate_slots.append(normalized)

    if safety_guard_slot:
        safety_slots = [
            slot for slot in candidate_slots
            if slot.get("slot_name") == safety_guard_slot
        ]
        if not safety_slots:
            safety_slots = [
                _normalize_slot_config(slot, index=index, source="common")
                for index, slot in enumerate(COMMON_SLOTS_TEMPLATE)
                if slot.get("slot_name") == safety_guard_slot
            ]
        if safety_slots:
            return (
                safety_slots[:1],
                question_mode,
                user_willingness,
                info_sufficiency,
            )

    filtered_slots = [
        slot for slot in candidate_slots
        if question_mode in slot.get("modes", [])
    ]
    filtered_slots.sort(
        key=lambda slot: (
            0 if safety_guard_slot and slot.get("slot_name") == safety_guard_slot else 1,
            -_score_slot_for_followup(
                slot,
                state,
                product_pool_insight,
                question_mode,
                safety_guard_slot,
            ),
            int(slot.get("order", 9999)),
        )
    )

    return (
        filtered_slots[:max_questions],
        question_mode,
        user_willingness,
        info_sufficiency,
    )


def _has_single_question_request(state: GiftRecommendationState) -> bool:
    text = "；".join(_get_recent_user_messages(state))
    return any(keyword in text for keyword in ("问一个", "一个问题", "只问一个", "就问一个"))


def _analyze_product_pool_for_followup(state: GiftRecommendationState) -> Dict[str, Any]:
    """分析候选商品池，生成影响追问选槽的轻量信号。"""
    products = _get_followup_product_pool(state)

    summary = getattr(state, "candidate_pool_summary", None) or {}
    confidence = _to_float(summary.get("confidence"), 0.0) if isinstance(summary, dict) else 0.0
    if products and confidence <= 0:
        confidence = 0.5

    pool_size = len(products)
    dominant_differences: List[str] = []
    price_spread_level = "none"
    category_focus = 0.0
    brand_focus = 0.0

    if products:
        price_spread_level = _detect_price_spread_level(products)
        if price_spread_level in {"high", "medium"}:
            dominant_differences.append("price")

        category_focus = _top_value_focus(products, ("small_category", "subcategory", "mid_category", "category"))
        if category_focus and category_focus < PRODUCT_POOL_BROAD_THRESHOLD:
            dominant_differences.append("sub_category")

        brand_focus = _top_value_focus(products, ("brand",))
        if brand_focus and brand_focus < PRODUCT_POOL_BROAD_THRESHOLD:
            dominant_differences.append("brand")

        for signal, keywords in POOL_SIGNAL_KEYWORDS.items():
            if _is_keyword_signal_diverse(products, keywords):
                dominant_differences.append(signal)

    dominant_differences = _dedupe_keep_order(dominant_differences)
    pool_status = _resolve_product_pool_status(
        pool_size=pool_size,
        confidence=confidence,
        dominant_differences=dominant_differences,
        category_focus=category_focus,
    )
    slot_boosts = _build_pool_slot_boosts(dominant_differences, confidence)

    return {
        "pool_size": pool_size,
        "pool_status": pool_status,
        "confidence": confidence,
        "price_spread_level": price_spread_level,
        "category_focus": category_focus,
        "brand_focus": brand_focus,
        "dominant_differences": dominant_differences,
        "slot_boosts": slot_boosts,
        "filled_slots": sorted((getattr(state, "filled_slots", {}) or {}).keys()),
        "reason": _build_product_pool_reason(pool_status, dominant_differences),
    }


def _get_followup_product_pool(state: GiftRecommendationState) -> List[Any]:
    products = list(getattr(state, "candidate_products", []) or [])
    if products:
        return products

    products = list(getattr(state, "filtered_products", []) or [])
    if products:
        return products

    selected_small_category, selected_mid_category = _resolve_pool_categories(state)
    if not selected_small_category and not selected_mid_category:
        return []

    budget_min = _slot_float(state, "budget_min")
    budget_max = _slot_float(state, "budget_max")
    catalog = _load_products_from_csv()
    category_products = []
    for product in catalog:
        product_small = str(getattr(product, "small_category", "") or "").strip()
        product_mid = str(getattr(product, "mid_category", "") or "").strip()
        if selected_small_category and product_small != selected_small_category:
            continue
        if selected_mid_category and product_mid != selected_mid_category:
            continue
        price = _to_float(getattr(product, "price", None), 0.0)
        if budget_min is not None and price < budget_min:
            continue
        if budget_max is not None and price > budget_max:
            continue
        category_products.append(product)

    if not category_products and (budget_min is not None or budget_max is not None):
        for product in catalog:
            product_small = str(getattr(product, "small_category", "") or "").strip()
            product_mid = str(getattr(product, "mid_category", "") or "").strip()
            if selected_small_category and product_small != selected_small_category:
                continue
            if selected_mid_category and product_mid != selected_mid_category:
                continue
            category_products.append(product)

    return _sample_product_pool(category_products)


def _resolve_pool_categories(state: GiftRecommendationState) -> Tuple[str, str]:
    selected_small = str(getattr(state, "selected_subcategory", "") or "").strip()
    selected_mid = str(getattr(state, "selected_mid_category", "") or "").strip()
    category_name = str(getattr(getattr(state, "selected_category", None), "category_name", "") or "").strip()
    small_to_mid = get_complete_small_to_mid_category_map()
    mid_to_big = get_complete_mid_to_big_category_map()

    if not selected_small and category_name in small_to_mid:
        selected_small = category_name
    if not selected_mid and selected_small:
        selected_mid = small_to_mid.get(selected_small, "")
    if not selected_mid and category_name in mid_to_big:
        selected_mid = category_name
    return selected_small, selected_mid


def _slot_float(state: GiftRecommendationState, slot_name: str) -> float | None:
    slot = (getattr(state, "filled_slots", {}) or {}).get(slot_name)
    if not slot:
        return None
    value = _to_float(getattr(slot, "value", None), 0.0)
    return value if value > 0 else None


def _sample_product_pool(products: List[Any], limit: int = 500) -> List[Any]:
    if len(products) <= limit:
        return products
    step = max(len(products) / limit, 1)
    return [products[int(index * step)] for index in range(limit)]


def _resolve_product_pool_status(
    pool_size: int,
    confidence: float,
    dominant_differences: List[str],
    category_focus: float,
) -> str:
    if (
        pool_size >= PRODUCT_POOL_MIN_PRODUCTS
        and confidence >= PRODUCT_POOL_CONFIDENCE_THRESHOLD
        and category_focus >= PRODUCT_POOL_FOCUS_THRESHOLD
        and len(dominant_differences) <= 1
    ):
        return "focused"
    if dominant_differences:
        return "broad"
    return "normal"


def _detect_price_spread_level(products: List[Any]) -> str:
    prices = [
        _to_float(getattr(product, "price", None), 0.0)
        for product in products
        if _to_float(getattr(product, "price", None), 0.0) > 0
    ]
    if len(prices) < 2:
        return "none"

    min_price = min(prices)
    max_price = max(prices)
    if min_price <= 0:
        return "none"

    ratio = max_price / min_price
    if ratio >= PRODUCT_POOL_PRICE_HIGH_RATIO:
        return "high"
    if ratio >= PRODUCT_POOL_PRICE_MEDIUM_RATIO:
        return "medium"
    return "low"


def _top_value_focus(products: List[Any], attr_names: Tuple[str, ...]) -> float:
    values = []
    for product in products:
        value = ""
        for attr_name in attr_names:
            value = str(getattr(product, attr_name, "") or "").strip()
            if value:
                break
        if value:
            values.append(value)

    if not values:
        return 0.0
    counter = Counter(values)
    return counter.most_common(1)[0][1] / len(values)


def _is_keyword_signal_diverse(products: List[Any], keywords: Tuple[str, ...]) -> bool:
    if not products:
        return False

    matched_keywords = set()
    for product in products:
        text = _product_pool_search_text(product)
        for keyword in keywords:
            if keyword and keyword in text:
                matched_keywords.add(keyword)

    return len(matched_keywords) >= 2


def _product_pool_search_text(product: Any) -> str:
    parts = [
        getattr(product, "sku_name", ""),
        getattr(product, "name", ""),
        getattr(product, "description", ""),
        getattr(product, "brand", ""),
        getattr(product, "category", ""),
        getattr(product, "subcategory", ""),
        getattr(product, "mid_category", ""),
        getattr(product, "small_category", ""),
    ]
    return " ".join(str(part or "") for part in parts).lower()


def _build_pool_slot_boosts(
    dominant_differences: List[str],
    confidence: float,
) -> Dict[str, int]:
    if confidence and confidence < PRODUCT_POOL_CONFIDENCE_THRESHOLD:
        multiplier = 0.5
    else:
        multiplier = 1.0

    boosts: Dict[str, int] = {}
    for signal in dominant_differences:
        for slot_name, boost in POOL_SIGNAL_SLOT_BOOSTS.get(signal, {}).items():
            adjusted = int(boost * multiplier)
            if adjusted <= 0:
                continue
            boosts[slot_name] = max(boosts.get(slot_name, 0), adjusted)
    return boosts


def _score_slot_for_followup(
    slot: Dict[str, Any],
    state: GiftRecommendationState,
    product_pool_insight: Dict[str, Any],
    question_mode: str,
    safety_guard_slot: str = "",
) -> float:
    slot_name = str(slot.get("slot_name", ""))
    priority = str(slot.get("priority", "medium"))
    score = PRIORITY_SCORE.get(priority, PRIORITY_SCORE["medium"])

    slot_boosts = (product_pool_insight or {}).get("slot_boosts", {})
    score += int(slot_boosts.get(slot_name, 0))
    if slot_boosts:
        score += _conversion_boost(slot_name)

    if safety_guard_slot and slot_name == safety_guard_slot:
        score += 100

    if question_mode == "brief" and priority == "low":
        score -= 20

    if (product_pool_insight or {}).get("pool_status") == "focused" and priority == "low":
        score -= 15

    if _is_slot_filled(state, slot_name):
        score -= 1000

    return score


def _conversion_boost(slot_name: str) -> int:
    if slot_name in {
        "budget_min",
        "budget_max",
        "type_and_style",
        "sub_category",
        "scene_function",
        "usage_scene",
        "occasion",
        "efficacy",
        "skin_concern",
        "taboo",
        "recipient_preferences",
    }:
        return 5
    return 0


def _build_product_pool_reason(pool_status: str, dominant_differences: List[str]) -> str:
    if not dominant_differences:
        return f"商品池状态为{pool_status}，未识别出需要明显加权的分散维度。"
    labels = {
        "price": "价格跨度",
        "sub_category": "商品类型",
        "brand": "品牌",
        "scene": "使用场景",
        "efficacy": "功效",
        "style": "风格",
        "taste": "口味",
    }
    readable = "、".join(labels.get(item, item) for item in dominant_differences)
    return f"商品池状态为{pool_status}，主要分散维度为{readable}。"


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _detect_user_willingness(state: GiftRecommendationState) -> str:
    """结合最近历史判断用户愿不愿意继续回答细节。"""
    recent_messages = _get_recent_user_messages(state)
    if not recent_messages:
        return "medium"

    latest_message = recent_messages[-1]
    combined_text = "；".join(recent_messages)

    if any(keyword in combined_text for keyword in LOW_WILLINGNESS_KEYWORDS):
        return "low"

    high_signal_count = sum(
        1 for keyword in HIGH_WILLINGNESS_KEYWORDS if keyword in combined_text
    )
    if high_signal_count >= 3:
        return "high"
    if len(latest_message) >= 20 and high_signal_count >= 1:
        return "high"

    return "medium"


def _build_direct_recommend_decision(
    state: GiftRecommendationState,
    category_name: str,
    user_willingness: str,
) -> Dict[str, Any]:
    decision = {
        "should_direct_recommend": False,
        "direct_recommend_reason": "",
        "direct_recommend_blocked_reason": "",
        "safety_guard_slot": "",
    }
    recent_messages = _get_recent_user_messages(state)
    if not recent_messages:
        return decision

    latest_message = recent_messages[-1]
    combined_text = "；".join(recent_messages)
    if any(keyword in combined_text for keyword in EXIT_LIKE_KEYWORDS):
        decision["direct_recommend_blocked_reason"] = "exit_like_intent"
        return decision

    if user_willingness != "low":
        return decision

    if any(keyword in combined_text for keyword in BRIEF_ONLY_KEYWORDS):
        decision["direct_recommend_blocked_reason"] = "brief_only_request"
        return decision

    compact_latest = latest_message.replace("，", "").replace(",", "").replace("。", "").strip()
    direct_candidate = (
        any(keyword in combined_text for keyword in DIRECT_RECOMMEND_KEYWORDS)
        or compact_latest in {"随便", "都行", "无所谓", "都可以"}
    )
    if not direct_candidate:
        return decision

    safety_guard_slot = _get_missing_safety_guard_slot(state, category_name)
    if safety_guard_slot:
        decision["direct_recommend_blocked_reason"] = "high_risk_missing_safety_slot"
        decision["safety_guard_slot"] = safety_guard_slot
        return decision

    decision["should_direct_recommend"] = True
    decision["direct_recommend_reason"] = "explicit_direct_recommend"
    return decision


def _should_direct_recommend(state: GiftRecommendationState, user_willingness: str) -> bool:
    """兼容旧测试/调用：仅返回是否直推。"""
    category_name = getattr(getattr(state, "selected_category", None), "category_name", "")
    return _build_direct_recommend_decision(
        state,
        category_name,
        user_willingness,
    )["should_direct_recommend"]


def _get_missing_safety_guard_slot(state: GiftRecommendationState, category_name: str) -> str:
    risk_category = _resolve_high_risk_category(category_name)
    if not risk_category:
        return ""

    if risk_category == "营养保健（滋补/维矿/功能健康）":
        return "" if _has_explicit_taboo_signal(state) else "taboo"

    for slot_name in HIGH_RISK_SAFETY_SLOTS.get(risk_category, ()):
        if _safety_signal_available(state, slot_name):
            return ""

    preferred_slot = HIGH_RISK_SAFETY_SLOTS.get(risk_category, ("taboo",))[0]
    return preferred_slot


def _has_explicit_taboo_signal(state: GiftRecommendationState) -> bool:
    text = "；".join(_get_recent_user_messages(state))
    explicit_keywords = (
        "禁忌",
        "过敏",
        "忌口",
        "不能",
        "不要",
        "不吃",
        "避开",
        "不含",
        "无糖",
        "控糖",
        "乳糖不耐",
    )
    return any(keyword in text for keyword in explicit_keywords)


def _resolve_high_risk_category(category_name: str) -> str:
    if category_name in HIGH_RISK_CATEGORIES:
        return category_name
    mid_category = _get_complete_small_to_mid_category_map().get(category_name, "")
    if mid_category:
        mapped_from_small = _get_complete_mid_to_big_category_map().get(mid_category, "")
        if mapped_from_small in HIGH_RISK_CATEGORIES:
            return mapped_from_small
    mapped_category = _get_complete_mid_to_big_category_map().get(category_name, "")
    if mapped_category in HIGH_RISK_CATEGORIES:
        return mapped_category
    return ""


def _safety_signal_available(state: GiftRecommendationState, slot_name: str) -> bool:
    if _is_slot_filled(state, slot_name):
        return True

    safety_keywords_by_slot = {
        "recipient_preferences": ("肤质", "敏感肌", "修护", "忌口", "口味", "孕妇", "老人", "儿童"),
        "skin_type": ("肤质", "敏感肌", "干皮", "油皮", "混油", "混干"),
        "food_taboo": ("忌口", "过敏", "坚果", "乳糖", "控糖", "素食"),
        "target_audience": ("孕妇", "婴儿", "儿童", "老人", "妈妈", "宝宝"),
        "recipient_age": ("婴儿", "儿童", "老人", "宝宝", "岁"),
        "taste_preference": ("口感", "清爽", "醇厚", "甜", "干", "柔和", "烈"),
        "drinking_scenario": ("宴请", "收藏", "家饮", "送礼"),
        "taboo": ("禁忌", "过敏", "忌口", "不能", "不要", "不吃"),
    }
    text = "；".join(_get_recent_user_messages(state))
    return any(keyword in text for keyword in safety_keywords_by_slot.get(slot_name, ()))


def _build_direct_recommend_message(category_name: str) -> str:
    display_category = category_name or "当前"
    return f"好的，我先按{display_category}方向为您推荐一批合适商品，后续也可以再按预算或偏好调整。"


def _detect_info_sufficiency(state: GiftRecommendationState) -> str:
    """根据核心槽位已填数量判断当前信息是否已经足够。"""
    filled_count = sum(
        1 for slot_name in CORE_INFO_SLOTS
        if _is_slot_filled(state, slot_name)
    )
    if filled_count >= 3:
        return "high"
    if filled_count >= 1:
        return "medium"
    return "low"


def _resolve_question_mode(user_willingness: str, info_sufficiency: str) -> str:
    """用户弱意愿优先；信息越充足，越少追问。"""
    if user_willingness == "low":
        return "brief"
    if info_sufficiency == "high":
        return "normal" if user_willingness == "high" else "brief"
    if user_willingness == "high":
        return "detailed"
    return "normal"


def _get_recent_user_messages(
    state: GiftRecommendationState,
    limit: int = 3,
) -> List[str]:
    if not getattr(state, "chat_history", None):
        return []

    user_messages = []
    for message in state.chat_history:
        if message.get("role") == "user" and message.get("content"):
            content = str(message["content"]).strip()
            if content:
                user_messages.append(content)

    return user_messages[-limit:]


def _normalize_slot_config(slot: Dict, index: int, source: str) -> Dict:
    normalized = dict(slot)
    slot_name = str(normalized.get("slot_name", ""))
    priority = str(normalized.get("priority") or _infer_slot_priority(normalized))
    normalized["priority"] = priority
    normalized["order"] = int(normalized.get("order", _default_slot_order(index, source)))
    normalized["modes"] = list(normalized.get("modes") or _default_modes_for_priority(priority))
    return normalized


def _infer_slot_priority(slot: Dict) -> str:
    slot_name = str(slot.get("slot_name", ""))
    description = str(slot.get("description", ""))
    question_hint = str(slot.get("question_hint", ""))
    text = f"{slot_name} {description} {question_hint}"

    if slot_name in HIGH_PRIORITY_SLOT_NAMES:
        return "high"
    if slot_name in MEDIUM_PRIORITY_SLOT_NAMES:
        return "medium"
    if slot_name in LOW_PRIORITY_SLOT_NAMES:
        return "low"

    if any(keyword in text for keyword in ("产地", "季节", "规格单位", "规格倍数", "年份")):
        return "low"
    if any(keyword in text for keyword in ("风格", "材质", "品牌", "香型", "颜色", "包装")):
        return "medium"
    if any(keyword in text for keyword in ("类型", "品类", "功效", "场景", "用途", "尺寸", "容量", "口味", "禁忌", "过敏")):
        return "high"
    return "medium"


def _default_slot_order(index: int, source: str) -> int:
    return (100 if source == "common" else 200) + index


def _default_modes_for_priority(priority: str) -> List[str]:
    if priority == "high":
        return ["brief", "normal", "detailed"]
    if priority == "low":
        return ["detailed"]
    return ["normal", "detailed"]


def _is_slot_filled(state: GiftRecommendationState, slot_name: str) -> bool:
    if slot_name in {"budget_min", "budget_max"}:
        return any(_is_single_slot_filled(state, name) for name in ("budget_min", "budget_max"))
    return _is_single_slot_filled(state, slot_name)


def _is_single_slot_filled(state: GiftRecommendationState, slot_name: str) -> bool:
    slot_obj = getattr(state, "filled_slots", {}).get(slot_name)
    if not slot_obj:
        return False
    return bool(getattr(slot_obj, "is_filled", False) and getattr(slot_obj, "value", None))


def _build_slots_prompt(slots_to_ask: List[Dict]) -> str:
    lines = []
    for slot in slots_to_ask:
        lines.append(
            "- {description}（slot_name={slot_name}, priority={priority}, question_hint={question_hint}）".format(
                description=slot.get("description", ""),
                slot_name=slot.get("slot_name", ""),
                priority=slot.get("priority", ""),
                question_hint=slot.get("question_hint", ""),
            )
        )
    return "\n".join(lines)


def _build_filled_slots_summary(state: GiftRecommendationState) -> str:
    """
    构建已填信息摘要，区分用户明确提供的信息和系统推测的信息
    """
    inferred_slot_names = set()
    inference_results = getattr(state, "inference_results", None) or []
    for item in inference_results:
        if isinstance(item, dict) and item.get("applied") is True:
            inferred_slot_names.add(item.get("slot_name", ""))

    filled_info = []
    for slot_name, slot in state.filled_slots.items():
        if getattr(slot, "is_filled", False) and getattr(slot, "value", None):
            if slot_name in inferred_slot_names:
                filled_info.append(f"{slot.display_name}: {slot.value}(推测)")
            else:
                filled_info.append(f"{slot.display_name}: {slot.value}")

    return "; ".join(filled_info) if filled_info else "暂无已填信息"


def _build_fallback_follow_up(
    category_name: str,
    slots_to_ask: List[Dict],
    filled_slots_summary: str,
    question_mode: str = "normal",
) -> str:
    """
    备用话术生成（当LLM调用失败时使用）
    """
    display_category = category_name or "当前"
    if filled_slots_summary != "暂无已填信息":
        base_text = f"已为您锁定{display_category}方向。结合当前信息，"
    else:
        base_text = f"已为您锁定{display_category}方向。"

    if not slots_to_ask:
        return base_text + "如果暂时没有更多要求，我也可以先按通用偏好推荐。"

    hints = [_clean_question_hint(slot.get("question_hint", "")) for slot in slots_to_ask]
    hints = [hint for hint in hints if hint]
    if not hints:
        descriptions = [slot.get("description", "") for slot in slots_to_ask if slot.get("description")]
        hints = [f"确认一下{'、'.join(descriptions)}"]

    question_text = "，另外".join(hints)
    if question_mode == "brief":
        return base_text + f"我只再确认下：{question_text}？如果暂时不确定，也可以先推荐。"
    return base_text + f"想再确认下：{question_text}？这样我能筛得更准。"


def _clean_question_hint(question_hint: str) -> str:
    return str(question_hint or "").strip().rstrip("？?。.")


def _get_template_category_id(state: GiftRecommendationState) -> str:
    """
    获取用于详细追问模板的大类 ID：
    - 如果当前是小类：小类 -> 中类 -> 大类
    - 如果当前是中类：中类 -> 大类
    """
    category_level = getattr(state, "category_level", "")
    selected_category = state.selected_category

    if not selected_category:
        return ""

    category_name = selected_category.category_name

    if category_level == "subcategory":
        mid_category = _get_complete_small_to_mid_category_map().get(category_name, "")
        if not mid_category:
            return ""
        return _get_complete_mid_to_big_category_map().get(mid_category, "")

    if category_level == "mid_category":
        return _get_complete_mid_to_big_category_map().get(category_name, "")

    return ""


def _get_complete_small_to_mid_category_map() -> Dict[str, str]:
    return get_complete_small_to_mid_category_map(SMALL_TO_MID_CATEGORY_MAP)


def _get_complete_mid_to_big_category_map() -> Dict[str, str]:
    return get_complete_mid_to_big_category_map(MID_CATEGORY_TO_BIG_CATEGORY_MAP)


def _build_fallback_message(state: GiftRecommendationState, template_category_name: str = "") -> str:
    category_name = template_category_name or state.selected_category.category_name
    slots_to_ask, question_mode, _, _ = _select_slots_to_ask(state, category_name)
    return _build_fallback_follow_up(
        category_name,
        slots_to_ask,
        _build_filled_slots_summary(state),
        question_mode,
    )


# 保留原有的映射表（简化版本）
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
