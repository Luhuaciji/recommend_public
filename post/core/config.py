"""
配置中心：所有环境变量、业务常量、开关配置
"""
import os
from pathlib import Path
from typing import List, Dict, Any
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# ==========================================
# 大模型配置 (Docker 运行时由环境变量注入)
# ==========================================
API_BASE_URL = os.getenv("API_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
API_KEY = os.getenv("API_KEY", "")
TEXT_MODEL_NAME = os.getenv("TEXT_MODEL_NAME", "qwen3-max")

LLM_TIMEOUT = 30.0
LLM_MAX_RETRIES = 2

# ==========================================
# 会话配置
# ==========================================
SESSION_EXPIRE_SECONDS = 1800  # 30分钟超时
MAX_HISTORY_LENGTH = 40        # 最大保留历史轮数
MAX_DIALOG_TURNS = 40          # 单个会话最大正常交互轮数
DIALOG_LIMIT_MESSAGE = (
    "本轮商品推荐已达到最多交互次数，小Q先基于当前信息结束本轮推荐。"
    "如需重新选品，请重新发起一轮新的推荐会话。"
)

# ==========================================
# 业务配置：送礼场景
# ✅ 产品经理改配置都来这里
# ==========================================
SCENARIO_CONFIG = {
    "required_slots": ["skin_type"],
    "composite_prompt": "您好，请问您对象皮肤肤质是什么样的啊，是干皮还是油皮？"
}

# 模拟商品数据库
MOCK_PRODUCT_DB: Dict[str, Dict[str, Any]] = {
    "干皮": {
        "productId": "61d87a94ba34884377525e03",
        "productName": "修丽可臻白焕彩精华液-30ml",
        "productPic": "https://lefox-data-library-cdn.cdfsunrise.com/shopping_hub/2022-10/182315246__-479731126__w960__h8000.jpg",
        "payPrice": "2724",
        "merchantId": "cdfhainan"
    },
    "油皮": {
        "productId": "72e98b05cc45995488636f14",
        "productName": "海蓝之谜清透控油精华水-150ml",
        "productPic": "https://lefox-data-library-cdn.cdfsunrise.com/shopping_hub/sample_oil.jpg",
        "payPrice": "1500",
        "merchantId": "cdfhainan"
    }
}
