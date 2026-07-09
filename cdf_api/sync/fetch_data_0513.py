#!/usr/bin/env python3
import os
import csv
import time
from datetime import datetime
from pathlib import Path
import mysql.connector
from mysql.connector import Error
import schedule
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _build_db_config():
    return {
        'host': os.getenv('SYNC_MYSQL_HOST', ''),
        'port': int(os.getenv('SYNC_MYSQL_PORT', '3306')),
        'user': os.getenv('SYNC_MYSQL_USER', ''),
        'password': os.getenv('SYNC_MYSQL_PASSWORD', ''),
        'database': os.getenv('SYNC_MYSQL_DATABASE', ''),
        'charset': os.getenv('SYNC_MYSQL_CHARSET', 'utf8mb4'),
        'use_ssl': _env_bool('SYNC_MYSQL_SSL', False),
    }


DB_CONFIG = _build_db_config()


OUTPUT_DIR = 'db_export'


def get_connection():
    missing = [
        env_name
        for env_name, value in (
            ('SYNC_MYSQL_HOST', DB_CONFIG['host']),
            ('SYNC_MYSQL_USER', DB_CONFIG['user']),
            ('SYNC_MYSQL_DATABASE', DB_CONFIG['database']),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing MySQL config env: {', '.join(missing)}")

    connect_kwargs = {
        'host': DB_CONFIG['host'],
        'port': DB_CONFIG['port'],
        'user': DB_CONFIG['user'],
        'password': DB_CONFIG['password'],
        'database': DB_CONFIG['database'],
        'charset': DB_CONFIG['charset'],
        'connection_timeout': 60,
        'ssl_disabled': not DB_CONFIG['use_ssl'],
    }
    
    return mysql.connector.connect(**connect_kwargs)


def get_all_tables(conn):
    cursor = conn.cursor()
    cursor.execute("SHOW TABLES")
    tables = [row[0] for row in cursor.fetchall()]
    cursor.close()
    return tables


def export_table_to_csv(conn, table_name, output_path):
    cursor = conn.cursor(dictionary=True)
    try:
        # 尝试使用筛选条件
        # cursor.execute(f"SELECT * FROM `{table_name}` WHERE b2c_status = 1 AND is_stop_sale = 0")
        cursor.execute(f"SELECT * FROM `{table_name}` WHERE itn_bsc_sell_state_type='1' AND is_itn_buy=1")
    except Exception:
        # 如果筛选字段不存在，则查询所有数据
        print(f"表 {table_name} 没有筛选字段，导出所有数据")
        cursor.execute(f"SELECT * FROM `{table_name}`")
    
    with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = None
        total_rows = 0
        
        while True:
            rows = cursor.fetchmany(1000)
            
            if not rows:
                break
            
            if writer is None:
                fieldnames = list(rows[0].keys())
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
            
            writer.writerows(rows)
            total_rows += len(rows)
    
    if total_rows == 0:
        print(f"表 {table_name} 没有数据，跳过")
        os.remove(output_path)
    else:
        print(f"已导出 {total_rows} 条数据到 {output_path}")
    
    cursor.close()


def fetch_data():
    """执行一次数据拉取任务"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    target_table = 'dim_pub_sku'
    
    try:
        conn = get_connection()
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 成功连接到数据库 {DB_CONFIG['database']}")
        
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始导出表: {target_table}")
        output_file = os.path.join(OUTPUT_DIR, f"{target_table}_{timestamp}.csv")
        export_table_to_csv(conn, target_table, output_file)
        
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 导出完成！\n")
        
    except Error as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MySQL 错误: {e}")
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 错误: {e}")
    finally:
        if 'conn' in locals() and conn.is_connected():
            conn.close()


def main():
    import sys
    
    # 检查是否是立即执行模式
    if len(sys.argv) > 1 and sys.argv[1] == '--now':
        print("立即执行一次数据拉取...\n")
        fetch_data()
        return
    
    # 定时任务模式
    print("=== 定时数据拉取服务启动 ===")
    print(f"每日 09:00 自动从 {DB_CONFIG['database']}.dim_pub_sku 拉取数据")
    print(f"输出目录: {os.path.abspath(OUTPUT_DIR)}")
    print("按 Ctrl+C 停止服务\n")
    
    # 设置每日9点执行
    schedule.every().day.at("09:00").do(fetch_data)
    
    # 立即执行一次（可选）
    print("首次启动，立即执行一次...\n")
    fetch_data()
    
    # 保持运行，检查定时任务
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)  # 每分钟检查一次
    except KeyboardInterrupt:
        print("\n\n定时任务已停止")


if __name__ == "__main__":
    main()
