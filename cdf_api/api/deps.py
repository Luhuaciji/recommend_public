"""
FastAPI 路由依赖：鉴权验证
✅ 所有签名/安全相关都来这个文件
"""
from fastapi import Request, Header, HTTPException
import hmac
import hashlib
import time
import json
from collections import defaultdict

# 配置分发给甲方的 appid 和 appsecret (真实生产环境中建议放到 .env 里)
APP_SECRETS = {
    "cdf_26283b073aa0433a": "6c89a8e9a12b833ffefe0819b0db61c35229d023371f6f75667ebadc033d0ed4" 
}

# 【修复 1+2】Nonce 缓存绑定 appid + 带时间戳自动过期
# 结构: {appid: {nonce: expire_timestamp}}
used_nonces = defaultdict(dict)
NONCE_EXPIRE_SECONDS = 300  # 和时间戳窗口保持一致，5分钟后自动清理

def clean_expired_nonces():
    """定期清理过期 Nonce，防止内存泄漏"""
    current_time = int(time.time())
    for appid in list(used_nonces.keys()):
        nonce_cache = used_nonces[appid]
        expired = [n for n, exp in nonce_cache.items() if exp < current_time]
        for n in expired:
            del nonce_cache[n]
        if not nonce_cache:
            del used_nonces[appid]

async def verify_signature(
    request: Request,
    appid: str = Header(...),
    timestamp: int = Header(...),
    nonce: str = Header(...),
    signature: str = Header(...) # 甲方传来的签名印章
):
    """FastAPI 依赖注入：执行签名验证"""
    
    # 每100次请求自动清理一次过期 Nonce
    if len(used_nonces) % 100 == 0:
        clean_expired_nonces()
    
    # 1. 验证 appid 是否合法，并取出对应的密码
    appsecret = APP_SECRETS.get(appid)
    if not appsecret:
        raise HTTPException(status_code=401, detail="鉴权失败：无效的 appid")

    # 2. 验证时间戳 (防过期)：如果请求时间戳和服务器当前时间相差超过 5 分钟 (300秒)，拒绝
    current_time = int(time.time())
    if abs(current_time - timestamp) > 300:
        raise HTTPException(status_code=401, detail="鉴权失败：请求已过期")

    # 【修复 2】Nonce 绑定 appid
    nonce_cache = used_nonces[appid]
    if nonce in nonce_cache:
        raise HTTPException(status_code=401, detail="鉴权失败：请求重复提交 (Nonce已存在)")
    nonce_cache[nonce] = timestamp + NONCE_EXPIRE_SECONDS

    # 4. 获取 POST 请求体中的明文 JSON 参数
    body_bytes = await request.body()
    try:
        body_dict = json.loads(body_bytes) if body_bytes else {}
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是合法的 JSON")

    # 5. 把 Header 参数和 Body 参数混在一起，准备排序
    sign_dict = {
        "appid": appid,
        "timestamp": str(timestamp),  
        "nonce": nonce
    }
    
    # 【修复 3】所有值转字符串，保证和前端序列化一致
    for k, v in body_dict.items():
        if v is not None:
            if isinstance(v, (dict, list)):
                sign_dict[k] = json.dumps(v, separators=(',', ':'), ensure_ascii=False)
            else:
                sign_dict[k] = str(v)

    # 6. 按照字母顺序排序，并拼接成 key=value&key2=value2 格式
    message = '&'.join([f"{k}={sign_dict[k]}" for k in sorted(sign_dict.keys())])

    # 7. 使用 HMAC-SHA256 算法，配合 appsecret 计算我们自己的印章
    calculated_sign = hmac.new(
        appsecret.encode("utf-8"),
        message.encode("utf-8"),
        digestmod=hashlib.sha256
    ).hexdigest()

    # 8. 比对印章：如果和我们算出来的不一致，拦截
    if calculated_sign != signature:
        error_detail = {
            "msg": "签名校验不通过，以下是计算参数",
            "appid": appid,
            "timestamp": str(timestamp),
            "nonce": nonce,
            "用于签名的完整字符串": message,
            "预期签名": calculated_sign,
            "收到签名": signature
        }
        print(f"❌ 签名拦截！appid: {appid}")
        print(f"   签名字符串: {message}")
        print(f"   预期签名: {calculated_sign}")
        print(f"   收到签名: {signature}")
        raise HTTPException(status_code=401, detail=error_detail)
        # raise HTTPException(status_code=401, detail="鉴权失败：签名(signature)校验不通过")
    # 如果代码能走到这里，说明验证通过，放行请求！