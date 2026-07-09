#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量获取所有商品数据 - 生产版本
支持分页、多线程、断点续传、数据库写入、优雅中断
"""

import json
import time
import base64
import hashlib
import os
import sqlite3
import signal
import sys
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
import requests

# ================= 配置信息 =================
APPID = "d4de48dd1f3b509a"
SIGN_KEY = "4hnfDs0s"
HOST = "http://lefox-marketing-openapi-gateway-dev-pub.cdfsunrise.com"
AUTH_TOKEN = ""
API_PATH = "/v2/proxy/goods_mgr/manager.goods.list"
BASE_URL = f"{HOST}{API_PATH}"
DB_FILE = "goods_data.db"

# RSA 密钥
PUBLIC_KEY = """-----BEGIN RSA PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAw5ZXDVY6xP+C8zJjiInoBDWr62OuDvDVBWF97qYPERh+i18cJPlZmGuPh5IldYjYiv0F0m9/hPkSw4+goQhyDtoU004tk0hALGRBWUvbZkpPkcBYSEuMtknKObMG+Te3s73lXO0HsMrkqXCBrVcwuJYzvBnk8uvK0Oq3vJ9s8gxhTnYOBIJf34tgEBY6CmYzPKCa9HDVxyEbZfdLx0b1JSBrv7hvK78x/YAzv1XiyE0mLdIvnfnXndEx9d21icihLJey8w6A4jmB41nQ5aB8UCM22CLgxNCBokGZANgw0pP13dNF3zxmd+dYUV6Zb1N/8gycHJZUR1SlRnjlfK3hiQIDAQAB
-----END RSA PUBLIC KEY-----"""

PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAw5ZXDVY6xP+C8zJjiInoBDWr62OuDvDVBWF97qYPERh+i18cJPlZmGuPh5IldYjYiv0F0m9/hPkSw4+goQhyDtoU004tk0hALGRBWUvbZkpPkcBYSEuMtknKObMG+Te3s73lXO0HsMrkqXCBrVcwuJYzvBnk8uvK0Oq3vJ9s8gxhTnYOBIJf34tgEBY6CmYzPKCa9HDVxyEbZfdLx0b1JSBrv7hvK78x/YAzv1XiyE0mLdIvnfnXndEx9d21icihLJey8w6A4jmB41nQ5aB8UCM22CLgxNCBokGZANgw0pP13dNF3zxmd+dYUV6Zb1N/8gycHJZUR1SlRnjlfK3hiQIDAQABAoIBAFIDg6NTCje7ENUbxwLlGQZS3zFITh9zu0+TTvQ4a872X3HfwvR6Hqi8SaZGkTCU3oCBkuRn3qgKrWSVoHyGBxXVOrBUcuX0gPxcWc6w8WIWPQFYD2zZSTrS/FpviLgONhjHwxrRRc1LdtDaHXZrPkHYsf7pOMjoONab5cnRbCSeck9NSK2tQVQUbwl8DHiLbzqsxHCWDcagbZni/Jpzr2/nG7Dd6CDCO75hxq6xFOsIYan+06hriY9n0pQMWj52azPEofqpl2dg/1mbNyZER/RNIDaEaZDK/iKCN+a/BzN+XiHBLA8kRBRW4moc70lNa9uCb9AxJ2aCm/M0I/xlS6UCgYEA0d3q1Qgr+QPtmnA8/qadTD6OUhv//nNt1BVaDXVPnsCAAjXPuzfwYpxMc26BtHGDAFa8Dl1NMwiYcv3jqmOu+oSkDVU8ITzkaGmVBRtuyCFFqzVUPKC12nGYmlZDB7RVc1AaQL6nOWcQHyil26XFKVNm5uKyAuU7sjgs2SjzX0sCgYEA7pTZqNZshq+X5I1/BVXn0DcJMQgc8S42dSwcSxjU0xENS8HX2egtv87GnoYibP6DTlmS4vxAAWJ8WVGe5dBc/JvTEEbKcgtVbZbYtwVhiXmrGNp2Lh7mngJ2Db07pfD76rDUgwrWVgoFp3bZBBSAjMWCSYrHZaEDsklnOqXvefsCgYBP3H3fYUOyd98z9OARG4AiIm/wKP2Ka4xwt0tUcb7Br4lGzgllugy8ybOB/ZxX+RYby+W2JaUZ9Xxlzd2T2mkcpdrzw+qkz1IvtXXiwqSufZLQavPKOnHZVX73xDmvtd0Ki4Hh/2sNOf31jXdIFVXouvceh0esOGU2FZBjduTd7wKBgQCPZjpZR+BdJ+eVARMqwtKdjKbiqKy0rgEfdk7fOGKbJnGmajFyESKGWiN5nsfMWUHU8vPdqJ5T9r0k9nEaUp3BW2uGKfZ+i59iSjuh7gMNHWsgehZ0bDBTIsmQQoA6oljYXEavKPFhL47sc/vGfwgxFHnV+CsXQ6s0GviIALqqxwKBgEUNf/FCwfByPmA5ap7fuDfAzRlFZ8o1/cHTbTtbwy8ayC5tYYGKzF8B+1itqbV5zcwOIFSaZ1gRubUZ45MjlajtrUUlHoQZcth86486anpGGIbj6DAH9errnXnCWuRZF1/zKwV5mnWbpxANo3MkdnC9Fwta2+/02E9OCtFlMAA4
-----END RSA PRIVATE KEY-----"""

# 并发配置
MAX_WORKERS = 10
PAGE_SIZE = 200
STATUS_FILE = "fetch_status.json"
OUTPUT_FILE = "all_goods_list.json"

# ================= 全局状态 =================
interrupted = False
shutdown_initiated = False
executor = None

# ================= RSA 加密解密 =================
def rsa_encrypt(plain_text, public_key_str):
    public_key_str = public_key_str.replace("-----BEGIN RSA PUBLIC KEY-----", "")
    public_key_str = public_key_str.replace("-----END RSA PUBLIC KEY-----", "")
    public_key_str = public_key_str.replace("\n", "").replace(" ", "")

    public_key_bytes = base64.b64decode(public_key_str)

    public_key = serialization.load_der_public_key(
        public_key_bytes,
        backend=default_backend()
    )

    key_size = public_key.key_size
    chunk_size = (key_size // 8) - 11

    encrypted_chunks = []
    plain_bytes = plain_text.encode('utf-8')

    for i in range(0, len(plain_bytes), chunk_size):
        chunk = plain_bytes[i:i + chunk_size]
        encrypted_chunk = public_key.encrypt(chunk, padding.PKCS1v15())
        encrypted_chunks.append(encrypted_chunk)

    encrypted_data = b''.join(encrypted_chunks)
    encrypted_base64 = base64.urlsafe_b64encode(encrypted_data).decode('utf-8')
    return encrypted_base64.rstrip('=')

def rsa_decrypt(cipher_text, private_key_str):
    cipher_text += '=' * ((4 - len(cipher_text) % 4) % 4)
    encrypted_bytes = base64.urlsafe_b64decode(cipher_text)

    private_key_str = private_key_str.replace("-----BEGIN RSA PRIVATE KEY-----", "")
    private_key_str = private_key_str.replace("-----END RSA PRIVATE KEY-----", "")
    private_key_str = private_key_str.replace("\n", "").replace(" ", "")

    private_key_bytes = base64.b64decode(private_key_str)

    private_key = serialization.load_der_private_key(
        private_key_bytes,
        password=None,
        backend=default_backend()
    )

    key_size = private_key.key_size
    chunk_size = key_size // 8

    decrypted_chunks = []
    for i in range(0, len(encrypted_bytes), chunk_size):
        chunk = encrypted_bytes[i:i + chunk_size]
        decrypted_chunk = private_key.decrypt(chunk, padding.PKCS1v15())
        decrypted_chunks.append(decrypted_chunk)

    return b''.join(decrypted_chunks).decode('utf-8')

# ================= 签名生成 =================
def generate_sign(sorted_params):
    param_str = '&'.join([f"{k}={v}" for k, v in sorted_params.items()])
    return hashlib.md5(param_str.encode('utf-8')).hexdigest()

# ================= 进度条工具 =================
def print_progress_bar(progress, current, total, batch_added=0, bar_length=50):
    filled_length = int(bar_length * progress // 100)
    bar = '█' * filled_length + '░' * (bar_length - filled_length)
    if batch_added > 0:
        print(f'\r⏳ [{bar}] {progress:5.1f}% | 本批次 +{batch_added:4d} | 累计 {current:6d}/{total}', end='', flush=True)
    else:
        print(f'\r⏳ [{bar}] {progress:5.1f}% | 累计 {current:6d}/{total}', end='', flush=True)

# ================= 数据库操作 =================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS goods_list (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lefox_id INTEGER UNIQUE,
        lefox_id_str TEXT,
        name TEXT,
        title TEXT,
        price TEXT,
        original_price TEXT,
        brand TEXT,
        brand_en TEXT,
        category TEXT,
        merchant_id TEXT,
        merchant_name TEXT,
        sell_mode TEXT,
        goods_type INTEGER,
        is_delete INTEGER,
        platform_up_down_state INTEGER,
        stock_state INTEGER,
        sync_state INTEGER,
        channel_sale_state INTEGER,
        valid INTEGER,
        create_time TEXT,
        update_time TEXT,
        goods_data TEXT,
        page_number INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    conn.commit()
    return conn

def save_goods_to_db(conn, goods_list, page_number):
    if not goods_list:
        return
    
    cursor = conn.cursor()
    
    for goods in goods_list:
        lefox_id = goods.get("lefoxId")
        lefox_id_str = goods.get("lefoxIdStr", "")
        name = goods.get("name", "")
        title = goods.get("title", "")
        price = goods.get("price", "")
        original_price = goods.get("originalPrice", "")
        brand = goods.get("brand", "")
        brand_en = goods.get("brandEn", "")
        category = goods.get("category", "")
        merchant_id = goods.get("merchantId", "")
        merchant_name = goods.get("merchantName", "")
        sell_mode = goods.get("sellMode", "")
        goods_type = goods.get("goodsType", 0)
        is_delete = goods.get("isDelete", 0)
        platform_up_down_state = goods.get("platformUpDownState", 0)
        stock_state = goods.get("stockState", 0)
        sync_state = goods.get("syncState", 0)
        channel_sale_state = goods.get("channelSaleState", 0)
        valid = 1 if goods.get("valid") else 0
        create_time = goods.get("createTime", "")
        update_time = goods.get("updateTime", "")
        goods_data = json.dumps(goods, ensure_ascii=False)
        
        cursor.execute('''
        INSERT OR REPLACE INTO goods_list (
            lefox_id, lefox_id_str, name, title, price, original_price,
            brand, brand_en, category, merchant_id, merchant_name, sell_mode,
            goods_type, is_delete, platform_up_down_state, stock_state,
            sync_state, channel_sale_state, valid, create_time, update_time,
            goods_data, page_number, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (
            lefox_id, lefox_id_str, name, title, price, original_price,
            brand, brand_en, category, merchant_id, merchant_name, sell_mode,
            goods_type, is_delete, platform_up_down_state, stock_state,
            sync_state, channel_sale_state, valid, create_time, update_time,
            goods_data, page_number
        ))
    
    conn.commit()

def save_to_files(all_goods, processed_pages, total_counts):
    status = {
        "goods": all_goods,
        "processed_pages": list(processed_pages),
        "total_counts": total_counts
    }
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_goods, f, ensure_ascii=False, indent=2)

# ================= 中断处理 =================
def init_signal_handlers():
    def signal_handler(signum, frame):
        global interrupted, shutdown_initiated
        if shutdown_initiated:
            print("\n强制退出...")
            sys.exit(1)
        
        signal_name = {signal.SIGINT: "中断信号", signal.SIGTERM: "终止信号"}.get(signum, f"信号 {signum}")
        print(f"\n{'='*70}")
        print(f"⚠️  收到 {signal_name}，正在保存数据...")
        print(f"{'='*70}")
        interrupted = True
        shutdown_initiated = True
        
        if executor:
            executor.shutdown(wait=False, cancel_futures=True)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

def emergency_save(all_goods, processed_pages, total_counts, conn, start_time):
    try:
        save_to_files(all_goods, processed_pages, total_counts)
        if conn:
            conn.commit()
        print("✓ 数据已保存")
    except Exception as e:
        print(f"✗ 保存失败: {e}")
    
    duration = time.time() - start_time
    print()
    print("=" * 70)
    print("⚠️  任务已中断")
    print(f"  已获取: {len(all_goods)} 条")
    print(f"  已处理: {len(processed_pages)} 页")
    print(f"  耗时:   {duration:.1f} 秒")
    print(f"  提示:   下次运行将从断点继续")
    print("=" * 70)

# ================= 单个页面获取 =================
def fetch_page(page_number):
    if interrupted:
        return None
    
    try:
        business_data = {
            "pageNumber": str(page_number),
            "pageSize": str(PAGE_SIZE)
        }
        business_json = json.dumps(business_data, separators=(',', ':'))
        encrypted_data = rsa_encrypt(business_json, PUBLIC_KEY)
        timestamp = str(int(time.time()))
        request_data = {
            "appid": APPID,
            "dataEncryptMethod": "rsa",
            "signEncryptMethod": "md5",
            "timestamp": timestamp,
            "data": encrypted_data
        }
        sorted_params = OrderedDict([
            ("appid", APPID),
            ("data", encrypted_data),
            ("dataEncryptMethod", "rsa"),
            ("key", SIGN_KEY),
            ("signEncryptMethod", "md5"),
            ("timestamp", timestamp)
        ])
        sign = generate_sign(sorted_params)
        request_data["sign"] = sign
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if AUTH_TOKEN:
            headers["Authorization"] = AUTH_TOKEN
        
        resp = requests.post(BASE_URL, headers=headers, json=request_data, timeout=60)
        
        if not resp.text:
            return None
        
        res = resp.json()
        code = res.get("code")
        if code not in (0, 1000):
            return None
        
        data_encrypt = res.get("data")
        if not data_encrypt:
            return None
        
        decrypted_response_data = rsa_decrypt(data_encrypt, PRIVATE_KEY)
        response_json = json.loads(decrypted_response_data)
        response_data = response_json.get("data", {})
        goods_list = response_data.get("goodsList", [])
        total_counts = response_data.get("totalCounts", 0)
        
        return {
            "page_number": page_number,
            "goods": goods_list,
            "total_counts": total_counts
        }
    except Exception:
        return None

# ================= 获取所有商品数据 =================
def get_all_goods():
    global executor
    all_goods = []
    processed_pages = set()
    total_counts = 0
    start_time = time.time()
    
    conn = init_db()
    
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                status = json.load(f)
                all_goods = status.get("goods", [])
                processed_pages = set(status.get("processed_pages", []))
                total_counts = status.get("total_counts", 0)
            print(f"✓ 断点续传，已获取 {len(all_goods)} 条数据")
        except Exception as e:
            print(f"✗ 状态文件损坏，重新开始: {e}")
            try:
                os.remove(STATUS_FILE)
            except:
                pass
            all_goods = []
            processed_pages = set()
            total_counts = 0
    else:
        print("✓ 开始新的同步任务")
    
    try:
        if not processed_pages and not interrupted:
            first_page_data = fetch_page(1)
            if first_page_data:
                all_goods.extend(first_page_data["goods"])
                processed_pages.add(1)
                total_counts = first_page_data["total_counts"]
                save_goods_to_db(conn, first_page_data["goods"], 1)
                save_to_files(all_goods, processed_pages, total_counts)
                print(f"✓ 第一页获取成功，总计 {total_counts} 条")
            else:
                print("✗ 第一页获取失败")
                return []
        
        if interrupted:
            emergency_save(all_goods, processed_pages, total_counts, conn, start_time)
            return all_goods
        
        total_pages = (total_counts + PAGE_SIZE - 1) // PAGE_SIZE
        remaining_pages = [p for p in range(1, total_pages + 1) if p not in processed_pages]
        print(f"📊 总计 {total_pages} 页，剩余 {len(remaining_pages)} 页待处理")
        
        if not remaining_pages:
            print("✓ 所有页面已完成")
            return all_goods
        
        batch_size = min(MAX_WORKERS * 2, len(remaining_pages))
        
        for i in range(0, len(remaining_pages), batch_size):
            if interrupted:
                break
            
            batch_pages = remaining_pages[i:i + batch_size]
            batch_goods = []
            
            executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
            try:
                future_to_page = {executor.submit(fetch_page, page): page for page in batch_pages}
                
                for future in as_completed(future_to_page):
                    if interrupted:
                        break
                    
                    page = future_to_page[future]
                    try:
                        result = future.result()
                        if result:
                            if result["goods"]:
                                batch_goods.extend(result["goods"])
                                processed_pages.add(page)
                                save_goods_to_db(conn, result["goods"], page)
                            else:
                                processed_pages.add(page)
                    except Exception:
                        pass
            finally:
                executor.shutdown(wait=True)
            
            all_goods.extend(batch_goods)
            save_to_files(all_goods, processed_pages, total_counts)
            
            progress = (i + len(batch_pages)) / len(remaining_pages) * 100
            print_progress_bar(progress, len(all_goods), total_counts, len(batch_goods))
            
            if interrupted:
                break
        
        print()  # 换行，避免进度条覆盖后续输出
        duration = time.time() - start_time
        if interrupted:
            emergency_save(all_goods, processed_pages, total_counts, conn, start_time)
        else:
            print()
            print("=" * 70)
            print("✓ 任务完成")
            print(f"  已获取: {len(all_goods)} 条")
            print(f"  耗时:   {duration:.1f} 秒")
            print("=" * 70)
    
    except Exception as e:
        print(f"\n✗ 发生错误: {e}")
        emergency_save(all_goods, processed_pages, total_counts, conn, start_time)
    finally:
        try:
            if conn:
                conn.close()
        except:
            pass
    
    return all_goods

# ================= 主函数 =================
if __name__ == "__main__":
    init_signal_handlers()
    
    print("=" * 70)
    print("商品数据批量获取工具")
    print("=" * 70)
    print()
    
    try:
        start_time = time.time()
        all_goods = get_all_goods()
        
        if not interrupted:
            print()
            print("输出文件:")
            print(f"  - JSON数据: {OUTPUT_FILE}")
            print(f"  - 状态文件: {STATUS_FILE}")
            print(f"  - 数据库:   {DB_FILE}")
            print()
            print("=" * 70)
    except KeyboardInterrupt:
        print("\n程序已退出")
    except Exception as e:
        print(f"\n程序异常: {e}")
