from fastapi import APIRouter, HTTPException
import sqlite3
import os

# 1. 这里不再用 FastAPI()，而是用 APIRouter()
# 统一定义前缀，这样下面的路由就不需要每次都写 /api/db 了
router = APIRouter(prefix="/cdfai-demo/v1/fudan/db", tags=["数据同步探针"])

# 确保这是 Docker 里的挂载路径
from pathlib import Path
DB_FILE = Path(__file__).parent.parent / "product_data.db" 

def get_db_connection():
    if not os.path.exists(DB_FILE):
        raise HTTPException(status_code=404, detail=f"找不到数据库文件: {DB_FILE}")
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# 2. 下面的 @app.get 全部换成 @router.get
@router.get("/summary", summary="获取数据库概览数据")
def get_db_summary():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as total_count FROM item_details")
        total_items = cursor.fetchone()["total_count"]
        cursor.execute("SELECT value FROM sync_config WHERE key='last_update_time'")
        row = cursor.fetchone()
        last_sync_timestamp = row["value"] if row else "0"
        conn.close()
        return {"code": 200, "data": {"total_items": total_items, "last_sync_timestamp": int(last_sync_timestamp)}}
    except Exception as e:
        return "wrong"
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/columns", summary="获取数据表结构")
def get_db_columns():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(item_details)")
        columns_info = cursor.fetchall()
        conn.close()
        columns = [{"name": col["name"], "type": col["type"]} for col in columns_info]
        return {"code": 200, "data": {"table_name": "item_details", "column_count": len(columns), "columns": columns}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sample", summary="抽样获取最新数据")
def get_data_sample(limit: int = 5):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT itemid, brand_name, goods_short_name, update_time, last_synced_at FROM item_details ORDER BY last_synced_at DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        return {"code": 200, "data": [dict(row) for row in rows]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))