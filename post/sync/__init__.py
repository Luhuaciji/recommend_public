"""
定时同步数据的脚本

和主服务分开跑，互不影响

文件说明：
- sync_db.py         每天凌晨从大数据平台拉商品
- sync_all.py        拉加密的商品数据
- sync_db_main.py   定时调度的入口

怎么运行：
    # 本地开个终端跑：
    python sync/sync_db_main.py
    
    # Docker 里自动后台跑
"""
