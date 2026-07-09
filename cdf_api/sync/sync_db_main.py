import time
import schedule

# 导入两个脚本的核心逻辑
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sync_db import init_db, job_sync_database
from sync_all import job_sync_lefox

def job_pipeline():
    """
    定义一个完整的串行工作流：先同步 DB，再同步 Lefox。
    这样彻底不需要硬性规定 09:00 和 09:05，避免前置任务超时导致的冲突。
    """
    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] === 开始每日全量同步流水线 ===")
    
    try:
        # 第一步：拉取基础商品数据
        job_sync_database()
        
        # 第二步：依赖第一步的基础数据，拉取 Lefox 数据并合并导出
        job_sync_lefox()
        
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] === 每日全量同步流水线顺利完成 ===\n")
    except Exception as e:
        print(f"❌ 流水线执行中断: {e}")

if __name__ == "__main__":
    print("初始化数据库环境...")
    init_db()  # 确保主表被创建
    
    print("启动后台服务...")
    print("正在执行首次全量同步流水线（这可能需要一些时间）...")
    
    # 首次启动时，强制按顺序跑一次全流程
    job_pipeline()
    
    # 定时调度：每天 09:00 启动整个流水线
    schedule.every().day.at("09:00").do(job_pipeline)
    
    print("\n定时调度已就绪 (每天 09:00 执行)，请保持本窗口开启，按 Ctrl+C 停止服务...")
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n服务已停止")