"""
数据库文件夹

文件说明：
- feedback.py       存用户的点赞点踩
- product.py        查商品数据
- test_db_api.py    给运维看的数据库状态接口

写代码的原则：
- 所有 SQL 语句都写在这个文件夹里
- 不要在业务代码里写 SQL

怎么用：
    from db import save_feedback
"""
from .feedback import save_feedback, get_all_feedback, get_feedback_stats, init_db

__all__ = [
    "save_feedback",
    "get_all_feedback",
    "get_feedback_stats",
    "init_db"
]
