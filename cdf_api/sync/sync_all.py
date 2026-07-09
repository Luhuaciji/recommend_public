      
import json
import base64
import hashlib
import time
import sqlite3
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
import schedule
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend


# ===================== 配置 =====================
from pathlib import Path
MAIN_DB = Path(__file__).parent.parent / "product_data.db"
LEFOX_DB = Path(__file__).parent.parent / "lefox_item_data_temp.db"

# Lefox API 配置
LEFOX_APPID = "d4de48dd1f3b509a"
LEFOX_SIGN_KEY = "4hnfDs0s"
LEFOX_HOST = "http://lefox-marketing-openapi-gateway-dev-pub.cdfsunrise.com"
LEFOX_AUTH_TOKEN = ""  # 填token

LEFOX_PUBLIC_KEY = """-----BEGIN RSA PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAw5ZXDVY6xP+C8zJjiIno
BDWr62OuDvDVBWF97qYPERh+i18cJPlZmGuPh5IldYjYiv0F0m9/hPkSw4+goQhy
DtoU004tk0hALGRBWUvbZkpPkcBYSEuMtknKObMG+Te3s73lXO0HsMrkqXCBrVcw
uJYzvBnk8uvK0Oq3vJ9s8gxhTnYOBIJf34tgEBY6CmYzPKCa9HDVxyEbZfdLx0b1
JSBrv7hvK78x/YAzv1XiyE0mLdIvnfnXndEx9d21icihLJey8w6A4jmB41nQ5aB8
UCM22CLgxNCBokGZANgw0pP13dNF3zxmd+dYUV6Zb1N/8gycHJZUR1SlRnjlfK3h
iQIDAQAB
-----END RSA PUBLIC KEY-----"""

LEFOX_PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAw5ZXDVY6xP+C8zJjiInoBDWr62OuDvDVBWF97qYPERh+i18c
JPlZmGuPh5IldYjYiv0F0m9/hPkSw4+goQhyDtoU004tk0hALGRBWUvbZkpPkcBY
SEuMtknKObMG+Te3s73lXO0HsMrkqXCBrVcwuJYzvBnk8uvK0Oq3vJ9s8gxhTnYO
BIJf34tgEBY6CmYzPKCa9HDVxyEbZfdLx0b1JSBrv7hvK78x/YAzv1XiyE0mLdIv
nfnXndEx9d21icihLJey8w6A4jmB41nQ5aB8UCM22CLgxNCBokGZANgw0pP13dNF
3zxmd+dYUV6Zb1N/8gycHJZUR1SlRnjlfK3hiQIDAQABAoIBAFIDg6NTCje7ENUb
xwLlGQZS3zFITh9zu0+TTvQ4a872X3HfwvR6Hqi8SaZGkTCU3oCBkuRn3qgKrWSV
oHyGBxXVOrBUcuX0gPxcWc6w8WIWPQFYD2zZSTrS/FpviLgONhjHwxrRRc1LdtDa
HXZrPkHYsf7pOMjoONab5cnRbCSeck9NSK2tQVQUbwl8DHiLbzqsxHCWDcagbZni
/Jpzr2/nG7Dd6CDCO75hxq6xFOsIYan+06hriY9n0pQMWj52azPEofqpl2dg/1mb
NyZER/RNIDaEaZDK/iKCN+a/BzN+XiHBLA8kRBRW4moc70lNa9uCb9AxJ2aCm/M0
I/xlS6UCgYEA0d3q1Qgr+QPtmnA8/qadTD6OUhv//nNt1BVaDXVPnsCAAjXPuzfw
YpxMc26BtHGDAFa8Dl1NMwiYcv3jqmOu+oSkDVU8ITzkaGmVBRtuyCFFqzVUPKC1
2nGYmlZDB7RVc1AaQL6nOWcQHyil26XFKVNm5uKyAuU7sjgs2SjzX0sCgYEA7pTZ
qNZshq+X5I1/BVXn0DcJMQgc8S42dSwcSxjU0xENS8HX2egtv87GnoYibP6DTlmS
4vxAAWJ8WVGe5dBc/JvTEEbKcgtVbZbYtwVhiXmrGNp2Lh7mngJ2Db07pfD76rDU
gwrWVgoFp3bZBBSAjMWCSYrHZaEDsklnOqXvefsCgYBP3H3fYUOyd98z9OARG4Ai
Im/wKP2Ka4xwt0tUcb7Br4lGzgllugy8ybOB/ZxX+RYby+W2JaUZ9Xxlzd2T2mkc
pdrzw+qkz1IvtXXiwqSufZLQavPKOnHZVX73xDmvtd0Ki4Hh/2sNOf31jXdIFVXo
uvceh0esOGU2FZBjduTd7wKBgQCPZjpZR+BdJ+eVARMqwtKdjKbiqKy0rgEfdk7f
OGKbJnGmajFyESKGWiN5nsfMWUHU8vPdqJ5T9r0k9nEaUp3BW2uGKfZ+i59iSjuh
7gMNHWsgehZ0bDBTIsmQQoA6oljYXEavKPFhL47sc/vGfwgxFHnV+CsXQ6s0GviI
ALqqxwKBgEUNf/FCwfByPmA5ap7fuDfAzRlFZ8o1/cHTbTtbwy8ayC5tYYGKzF8B
+1itqbV5zcwOIFSaZ1gRubUZ45MjlajtrUUlHoQZcth86486anpGGIbj6DAH9err
nXnCWuRZF1/zKwV5mnWbpxANo3MkdnC9Fwta2+/02E9OCtFlMAA4
-----END RSA PRIVATE KEY-----"""

# 并发配置
MAX_WORKERS = 10
BATCH_SIZE_PER_REQUEST = 500


# ===================== RSA =====================
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


def rsa_decrypt(encrypted_text, private_key_str):
    encrypted_text += '=' * ((4 - len(encrypted_text) % 4) % 4)
    encrypted_bytes = base64.urlsafe_b64decode(encrypted_text)

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


# ===================== 签名 =====================
def generate_sign(sorted_params):
    param_str = '&'.join([f"{k}={v}" for k, v in sorted_params.items()])
    return hashlib.md5(param_str.encode('utf-8')).hexdigest()


# ===================== 数据库操作 =====================
def get_item_ids_from_main_db():
    conn = sqlite3.connect(MAIN_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT itemid FROM item_details")
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]


def init_lefox_db():
    conn = sqlite3.connect(LEFOX_DB)
    cursor = conn.cursor()

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS item_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lefoxId TEXT UNIQUE,
        goodsID TEXT,
        goodsCode TEXT,
        goodsName TEXT,
        originalGoodsName TEXT,
        brandCode TEXT,
        categoryCode TEXT,
        categoryId TEXT,
        merchantID TEXT,
        merchantName TEXT,
        price TEXT,
        taxFreePrice TEXT,
        originalPrice TEXT,
        sellState TEXT,
        taxType TEXT,
        goodsType INTEGER,
        barCode TEXT,
        snapshotId TEXT,
        index_no INTEGER,
        sevenDaysNoReason INTEGER,
        currentBuyType INTEGER,
        nodeType INTEGER,
        isPrimary INTEGER,
        primaryId TEXT,
        brandId TEXT,
        brandShortId INTEGER,
        packageWeight TEXT,
        affiliated_id TEXT,
        affiliated_abbid TEXT,
        affiliated_shRsCode TEXT,
        delivery_express INTEGER,
        delivery_collection INTEGER
    )
    ''')

    conn.commit()
    return conn


def parse_lefox_item(decrypted_json):
    try:
        data = json.loads(decrypted_json)
        items = data.get("data", [])
        if not items:
            return []

        result = []
        for item in items:
            affiliated = item.get("affiliated", {})
            package = item.get("packageInfo", {})
            delivery = item.get("deliveryPattern", {})
            lefoxId = str(item.get("lefoxId"))

            values = (
                lefoxId,
                item.get("goodsID"),
                item.get("goodsCode"),
                item.get("goodsName"),
                item.get("originalGoodsName"),
                item.get("brandCode"),
                item.get("categoryCode"),
                item.get("categoryId"),
                item.get("merchantID"),
                item.get("merchantName"),
                item.get("price"),
                item.get("taxFreePrice"),
                item.get("originalPrice"),
                item.get("sellState"),
                item.get("taxType"),
                item.get("goodsType"),
                item.get("barCode"),
                item.get("snapshotId"),
                item.get("index"),
                item.get("sevenDaysNoReason"),
                item.get("currentBuyType"),
                item.get("nodeType"),
                int(item.get("isPrimary") or 0),
                item.get("primaryId"),
                item.get("brandId"),
                item.get("brandShortId"),
                package.get("packageWeight"),
                affiliated.get("id"),
                affiliated.get("abbid"),
                affiliated.get("shRsCode"),
                int(delivery.get("expressDelivery") or 0),
                int(delivery.get("collectionGoods") or 0)
            )
            result.append(values)
        return result

    except Exception as e:
        print("❌ 解析失败:", e)
        return []


def batch_save_to_lefox_db(conn, all_values_list):
    if not all_values_list:
        return

    sql = '''
    INSERT OR REPLACE INTO item_data (
        lefoxId,
        goodsID, goodsCode, goodsName, originalGoodsName,
        brandCode, categoryCode, categoryId, merchantID, merchantName,
        price, taxFreePrice, originalPrice,
        sellState, taxType, goodsType,
        barCode, snapshotId,
        index_no, sevenDaysNoReason, currentBuyType, nodeType, isPrimary, primaryId,
        brandId, brandShortId,
        packageWeight,
        affiliated_id, affiliated_abbid, affiliated_shRsCode,
        delivery_express, delivery_collection
    ) VALUES ({})
    '''.format(','.join(['?'] * 32))

    cursor = conn.cursor()
    cursor.executemany(sql, all_values_list)
    conn.commit()


# ===================== Lefox API 请求 =====================
def get_lefox_data_batch(lefoxIds):
    business_data = {
        "goodsIdInfo": [{"lefoxId": lid} for lid in lefoxIds],
        "isShowBrandStore": 0,
        "isShow0Pic": 0,
        "ignoreInvalid": 0
    }

    business_json = json.dumps(business_data, separators=(',', ':'))
    encrypted_data = rsa_encrypt(business_json, LEFOX_PUBLIC_KEY)

    timestamp = str(int(time.time()))

    request_data = {
        "appid": LEFOX_APPID,
        "dataEncryptMethod": "rsa",
        "signEncryptMethod": "md5",
        "timestamp": timestamp,
        "data": encrypted_data
    }

    sorted_params = OrderedDict([
        ("appid", LEFOX_APPID),
        ("data", encrypted_data),
        ("dataEncryptMethod", "rsa"),
        ("key", LEFOX_SIGN_KEY),
        ("signEncryptMethod", "md5"),
        ("timestamp", timestamp)
    ])

    request_data["sign"] = generate_sign(sorted_params)

    headers = {
        "Authorization": LEFOX_AUTH_TOKEN,
        "Content-Type": "application/json"
    }

    url = LEFOX_HOST + "/v2/proxy/datalibrary_support/data.library.search.item.with.buytype"

    try:
        resp = requests.post(url, headers=headers, json=request_data, timeout=60)
        if not resp.text:
            return None

        res = resp.json()
        data_encrypt = res.get("data")
        if not data_encrypt:
            return None

        return rsa_decrypt(data_encrypt, LEFOX_PRIVATE_KEY)

    except Exception as e:
        print(f"❌ 批量请求失败: {e}")
        return None


# ===================== 合并数据到主数据库 =====================
def merge_to_main_db():
    print("\n🔄 开始合并数据到主数据库...")
    
    conn_main = sqlite3.connect(MAIN_DB)
    conn_lefox = sqlite3.connect(LEFOX_DB)
    
    cur_main = conn_main.cursor()
    cur_lefox = conn_lefox.cursor()
    
    cur_lefox.execute("SELECT * FROM item_data")
    rows_lefox = cur_lefox.fetchall()
    columns_lefox = [desc[0] for desc in cur_lefox.description]
    
    print(f"📦 从 Lefox 数据库读取到 {len(rows_lefox)} 条数据")
    
    for col in columns_lefox:
        try:
            cur_main.execute(f'ALTER TABLE item_details ADD COLUMN "{col}" TEXT')
        except:
            pass
    
    conn_main.commit()
    
    lefox_data = {row[columns_lefox.index("lefoxId")]: row for row in rows_lefox if row[columns_lefox.index("lefoxId")]}
    
    cur_main.execute("SELECT itemid FROM item_details")
    all_item_ids = [r[0] for r in cur_main.fetchall()]
    
    update_count = 0
    for item_id in all_item_ids:
        if item_id in lefox_data:
            values = list(lefox_data[item_id])
            placeholders = ", ".join([f'"{c}" = ?' for c in columns_lefox])
            sql = f"UPDATE item_details SET {placeholders} WHERE itemid = ?"
            cur_main.execute(sql, values + [item_id])
            update_count += 1
    
    conn_main.commit()
    
    conn_main.close()
    conn_lefox.close()
    
    print(f"✅ 合并完成！成功更新 {update_count} 条数据到主数据库")


def export_db_to_jsonl():
    """将 product_data.db 导出为 JSONL 文件"""
    print("\n📊 开始导出数据库到 JSONL...")
    
    try:
        conn = sqlite3.connect(MAIN_DB)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM item_details")
        rows = cursor.fetchall()
        
        columns = [desc[0] for desc in cursor.description]
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        jsonl_filename = f"product_data_{timestamp}.jsonl"
        
        with open(jsonl_filename, 'w', encoding='utf-8') as f:
            for row in rows:
                item = {}
                for i, col in enumerate(columns):
                    item[col] = row[i]
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
        
        conn.close()
        
        print(f"✅ JSONL 导出完成！文件：{jsonl_filename}")
        print(f"📋 共导出 {len(rows)} 条数据")
        
    except Exception as e:
        print(f"❌ JSONL 导出失败：{e}")


# ===================== 主同步流程 =====================
def sync_lefox_data():
    item_ids = get_item_ids_from_main_db()
    print(f"📋 从主数据库读取到 {len(item_ids)} 个 itemid")
    
    if not item_ids:
        print("❌ 没有找到 itemid")
        return
    
    id_batches = [item_ids[i:i + BATCH_SIZE_PER_REQUEST] for i in range(0, len(item_ids), BATCH_SIZE_PER_REQUEST)]
    total_batches = len(id_batches)
    
    print(f"📦 分成 {total_batches} 批，每批 {BATCH_SIZE_PER_REQUEST} 个")
    print(f"🚀 并发线程：{MAX_WORKERS}")
    print("=" * 70)
    
    conn_lefox = init_lefox_db()
    success_count = 0
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {}
        for batch_idx, batch_ids in enumerate(id_batches):
            future = executor.submit(get_lefox_data_batch, batch_ids)
            future_map[future] = (batch_idx, batch_ids)
        
        all_items_data = []
        for future in as_completed(future_map):
            batch_idx, batch_ids = future_map[future]
            try:
                decrypted = future.result()
                if decrypted:
                    items_data = parse_lefox_item(decrypted)
                    if items_data:
                        all_items_data.extend(items_data)
                        success_count += len(items_data)
                        print(f"✅ [批次 {batch_idx + 1}/{total_batches}] 成功获取 {len(items_data)} 条 | 累计：{success_count}/{len(item_ids)}")
                    else:
                        print(f"❌ [批次 {batch_idx + 1}/{total_batches}] 解析失败")
                else:
                    print(f"❌ [批次 {batch_idx + 1}/{total_batches}] 请求失败/无数据")
            except Exception as e:
                print(f"❌ [批次 {batch_idx + 1}/{total_batches}] 异常：{str(e)}")
            
            if len(all_items_data) >= 500:
                batch_save_to_lefox_db(conn_lefox, all_items_data)
                all_items_data.clear()
                print(f"📥 批量写入临时数据库完成")
        
        if all_items_data:
            batch_save_to_lefox_db(conn_lefox, all_items_data)
            print(f"📥 最终批量写入临时数据库完成")
    
    conn_lefox.close()
    
    duration = time.time() - start_time
    print("=" * 70)
    print(f"🎉 Lefox 数据获取完成 | 成功获取：{success_count} 条 | 耗时：{duration:.1f} 秒")
    
    merge_to_main_db()
    export_db_to_jsonl()


def job_sync_lefox():
    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动 Lefox 数据同步任务...")
    print("=" * 70)
    sync_lefox_data()


if __name__ == "__main__":
    print("启动定时服务 (每天 09:05 同步 Lefox 数据)")
    print("首次运行，会先同步一次 Lefox 数据...")
    
    job_sync_lefox()
    
    schedule.every().day.at("09:05").do(job_sync_lefox)
    print("\n定时调度已就绪，请保持本窗口开启，按 Ctrl+C 停止服务...")
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n服务已停止")

    