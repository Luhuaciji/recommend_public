"""
接口协议文件夹

文件说明：
- schemas.py    请求和返回的数据长什么样
- deps.py       验证请求签名，防止别人乱调用

怎么用：
    from api import CDFRequest, CDFResponse, FeedbackRequest, verify_signature

注意：
    改了这里的字段要同步告诉前端同学
"""
from .schemas import CDFRequest, CDFResponse, FeedbackRequest
from .deps import verify_signature

__all__ = [
    "CDFRequest", "CDFResponse", "FeedbackRequest",
    "verify_signature"
]
