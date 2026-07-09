import os
import requests
import psycopg2
import redis
import urllib3
from elasticsearch import Elasticsearch

# 禁用安全请求警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 配置 ---
config = {
    "pg": {
        "host": "d1d716bc51dd4e65a9b5d83075ea6799in03.internal.cn-east-3.postgresql.rds.myhuaweicloud.com",
        "user": "root",
        "password": "yPhdYJjaaUhquS!NRBy4G9",
        "dbname": "postgres", # 默认连接库
        "connect_timeout": 15
    },
    "redis": {
        "host": "redis-0492aa48-6ca9-4045-b76a-191f71f1702d.cn-east-3.dcs.myhuaweicloud.com",
        "port": 6379,
        "password": "ez546ZF)Pj4zc!bN"
    },
    "es": {
        "hosts": ["http://10.56.3.77:9200", "http://10.56.2.93:9200", "http://10.56.2.35:9200"],
        "user": "admin",
        "password": "cH2XMdIQKUORj!ZnjOPM15"
    },
    "deepseek": {
        "url": "https://llm-test.cdfsunrise.com/v1/chat/completions",
        "model": "DeepSeek-V3.2",
        "key": "sk-EqTaZnxubyD1QiZS5eF99e89A83c4941B862B652EeA19cEd"
    }
}

def test_remote_connectivity():
    print("🚀 开始环境连通性测试...")

    # 1. PostgreSQL 测试
    print("\n[1/4] 测试 PostgreSQL...")
    try:
        conn = psycopg2.connect(**config["pg"])
        conn.autocommit = True
        cur = conn.cursor()
        # 创建一个远端标识的测试表
        cur.execute("CREATE TABLE IF NOT EXISTS remote_smoke_test (id SERIAL PRIMARY KEY, note TEXT);")
        cur.execute("INSERT INTO remote_smoke_test (note) VALUES ('Remote connection verified');")
        cur.execute("SELECT note FROM remote_smoke_test ORDER BY id DESC LIMIT 1;")
        print(f"✅ PG 成功: {cur.fetchone()[0]}")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ PG 失败: {e}")

    # 2. Redis 测试
    print("\n[2/4] 测试 Redis...")
    try:
        r = redis.Redis(
            host=config["redis"]["host"], 
            port=config["redis"]["port"], 
            password=config["redis"]["password"],
            socket_timeout=10
        )
        r.set("remote_status", "ready")
        print(f"✅ Redis 成功: {r.get('remote_status').decode('utf-8')}")
    except Exception as e:
        print(f"❌ Redis 失败: {e}")

    # 3. Elasticsearch 测试
    print("\n[3/4] 测试 Elasticsearch...")
    try:
        es = Elasticsearch(
            config["es"]["hosts"],
            basic_auth=(config["es"]["user"], config["es"]["password"]),
            verify_certs=False,
            request_timeout=15
        )
        if es.ping():
            info = es.info()
            print(f"✅ ES 成功: 认证通过，集群: {info['cluster_name']}")
        else:
            print("❌ ES 失败: Ping 无响应")
    except Exception as e:
        print(f"❌ ES 报错: {e}")

    # 4. DeepSeek API 测试
    print("\n[4/4] 测试 DeepSeek API...")
    try:
        headers = {"Authorization": f"Bearer {config['deepseek']['key']}"}
        payload = {
            "model": config["deepseek"]["model"],
            "messages": [{"role": "user", "content": "Remote Test"}]
        }
        res = requests.post(config["deepseek"]["url"], json=payload, headers=headers, timeout=20)
        if res.status_code == 200:
            print(f"✅ DeepSeek 成功: {res.json()['choices'][0]['message']['content']}")
        else:
            print(f"❌ DeepSeek 报错: {res.status_code}, {res.text}")
    except Exception as e:
        print(f"❌ DeepSeek 连接失败: {e}")

if __name__ == "__main__":
    test_remote_connectivity()