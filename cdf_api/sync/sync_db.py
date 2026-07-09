import json
import time
import sqlite3
import gc
import requests
import schedule

# ================= 配置区域 =================
API_URL = "https://bigdata-datax-dev.cdfsunrise.com/item/tags/batch/query"
TABLE_NAME = "algo_smart_q_item_detail_tags_new"
from pathlib import Path
DB_FILE = Path(__file__).parent.parent / "product_data.db"
PAGE_SIZE = 500
AUTH_TOKEN = "" # 如果开发环境不需要 token 则保持为空

# ================= 数据库操作 =================
def init_db():
    """初始化 SQLite 数据库及表结构（完整 18 个字段）"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS item_details (
            itemid TEXT PRIMARY KEY,
            sku_id TEXT,
            brand_name TEXT,
            brand_english_name TEXT,
            category_name TEXT,
            buy_type INTEGER,
            goods_short_name TEXT,
            description TEXT,
            notice TEXT,
            piclists TEXT,
            main_pic TEXT,
            img_content TEXT,
            is_onsale INTEGER,
            create_date TEXT,
            update_time TEXT,
            pt TEXT,
            quantitys_last_year TEXT,
            sku_price REAL,
            last_synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('CREATE TABLE IF NOT EXISTS sync_config (key TEXT PRIMARY KEY, value TEXT)')
    conn.commit()
    conn.close()

def get_last_update_time():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM sync_config WHERE key='last_update_time'")
    row = cursor.fetchone()
    conn.close()
    return int(row[0]) if row else 0

def save_last_update_time(timestamp):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO sync_config (key, value) VALUES ('last_update_time', ?)", (str(timestamp),))
    conn.commit()
    conn.close()

def save_to_sqlite(tags_list):
    """将 API 返回的 tags 列表打平并存入 SQLite"""
    if not tags_list:
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    count = 0
    for item_tags in tags_list:
        item_data = {tag["name"]: tag["value"] for tag in item_tags}
        
        cursor.execute('''
            INSERT OR REPLACE INTO item_details 
            (itemid, sku_id, brand_name, brand_english_name, category_name, buy_type,
             goods_short_name, description, notice, piclists, main_pic, img_content,
             is_onsale, create_date, update_time, pt, quantitys_last_year, sku_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            item_data.get("itemid"),
            item_data.get("sku_id"),
            item_data.get("brand_name"),
            item_data.get("brand_english_name"),
            item_data.get("category_name"),
            item_data.get("buy_type"),
            item_data.get("goods_short_name"),
            item_data.get("description"),
            item_data.get("notice"),
            item_data.get("piclists"),
            item_data.get("main_pic"),
            item_data.get("img_content"),
            item_data.get("is_onsale"),
            item_data.get("create_date"),
            item_data.get("update_time"),
            item_data.get("pt"),
            item_data.get("quantitys_last_year"),
            item_data.get("sku_price")
        ))
        count += 1
    
    conn.commit()
    conn.close()

# ================= 业务拉取逻辑 =================
def fetch_data(start_row_key, update_time):
    """直接发送明文请求"""
    payload = {
        "tableName": TABLE_NAME,
        "startRowKey": start_row_key,
        "pageSize": PAGE_SIZE,
        "updateTime": update_time
    }

    headers = {"Content-Type": "application/json; charset=utf-8"}
    if AUTH_TOKEN:
        headers["Authorization"] = AUTH_TOKEN
    
    try:
        # 去除了 proxies 参数，直连请求
        resp = requests.post(
            API_URL, 
            headers=headers, 
            json=payload,
            timeout=60
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"❌ 请求失败: {str(e)[:100]}")
        return None

def job_sync_database():
    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动数据同步任务...")
    print("=" * 70)
    
    last_ts = get_last_update_time()
    current_session_ts = int(time.time())
    
    next_key = ""
    has_next = True
    total = 0
    page = 0
    start_time = time.time()
    
    while has_next:
        page += 1
        data = fetch_data(next_key, last_ts)
        
        if not data or data.get("code") not in [0, "0"]:
            print(f"第 {page} 页抓取异常，退出本轮循环")
            break
            
        tags = data.get("tagsList", [])
        if tags:
            save_to_sqlite(tags)
            total += len(tags)
        
        speed = total / (time.time() - start_time) if time.time() - start_time > 0 else 0
        print(f"[页{page:3d}] 累计: {total:6d} 条 | 速度: {speed:.0f} 条/秒 | nextKey: {next_key[:20]}...")
            
        has_next = data.get("hasNext", False)
        next_key = data.get("nextRowKey", "")
        
        if not next_key and has_next: 
            break
            
        gc.collect()
        
    save_last_update_time(current_session_ts)
    duration = time.time() - start_time
    print("=" * 70)
    print(f"✅ 同步结束，本次新增/更新: {total} 条，耗时: {duration:.1f} 秒")

# ================= 入口 =================
if __name__ == "__main__":
    init_db()
    
    # 提醒：因为跑在 Windows 上，直接用本地北京时间 09:00 即可
    print("启动定时服务 (每天 09:00)")
    print("首次运行，会先同步一次历史数据...")
    
    job_sync_database() # 启动时先跑一次
    
    # 修改为 09:00
    schedule.every().day.at("09:00").do(job_sync_database)
    print("\n定时调度已就绪，请保持本窗口开启，按 Ctrl+C 停止服务...")
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n服务已停止")