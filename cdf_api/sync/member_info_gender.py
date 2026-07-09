#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fetch member info from Lefox OpenAPI and extract gender by accountId.

Public functions:
- get_user_member_info(account_id): request and decrypt member.info response.
- get_gender_by_account_id(account_id): return "男", "女", or "未知".
"""

import argparse
import base64
import hashlib
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

try:
    from .sync_all import (
        LEFOX_APPID as DEFAULT_APPID,
        LEFOX_AUTH_TOKEN as DEFAULT_AUTH_TOKEN,
        LEFOX_HOST as DEFAULT_HOST,
        LEFOX_PRIVATE_KEY as DEFAULT_PRIVATE_KEY,
        LEFOX_PUBLIC_KEY as DEFAULT_PUBLIC_KEY,
        LEFOX_SIGN_KEY as DEFAULT_SIGN_KEY,
    )
except ImportError:
    from sync_all import (  # type: ignore
        LEFOX_APPID as DEFAULT_APPID,
        LEFOX_AUTH_TOKEN as DEFAULT_AUTH_TOKEN,
        LEFOX_HOST as DEFAULT_HOST,
        LEFOX_PRIVATE_KEY as DEFAULT_PRIVATE_KEY,
        LEFOX_PUBLIC_KEY as DEFAULT_PUBLIC_KEY,
        LEFOX_SIGN_KEY as DEFAULT_SIGN_KEY,
    )


MEMBER_INFO_API_PATH = "/v2/proxy/usermember/v1.member.info"


class OpenPlatformError(RuntimeError):
    """Raised when OpenAPI request, response parsing, or decryption fails."""


@dataclass(frozen=True)
class OpenPlatformConfig:
    appid: str
    sign_key: str
    host: str
    public_key: str
    private_key: str
    auth_token: str = ""
    timeout: int = 30

    @classmethod
    def from_env(cls) -> "OpenPlatformConfig":
        return cls(
            appid=os.getenv("LEFOX_APPID", DEFAULT_APPID),
            sign_key=os.getenv("LEFOX_SIGN_KEY", DEFAULT_SIGN_KEY),
            host=os.getenv("LEFOX_HOST", DEFAULT_HOST).rstrip("/"),
            public_key=_normalize_env_key(os.getenv("LEFOX_PUBLIC_KEY", DEFAULT_PUBLIC_KEY)),
            private_key=_normalize_env_key(os.getenv("LEFOX_PRIVATE_KEY", DEFAULT_PRIVATE_KEY)),
            auth_token=os.getenv("LEFOX_AUTH_TOKEN", DEFAULT_AUTH_TOKEN),
            timeout=int(os.getenv("LEFOX_TIMEOUT", "30")),
        )


def _normalize_env_key(key: str) -> str:
    return key.replace("\\n", "\n")


def rsa_encrypt(plain_text: str, public_key_str: str) -> str:
    public_key_body = (
        public_key_str.replace("-----BEGIN RSA PUBLIC KEY-----", "")
        .replace("-----END RSA PUBLIC KEY-----", "")
        .replace("\n", "")
        .replace(" ", "")
    )
    public_key_bytes = base64.b64decode(public_key_body)
    public_key = serialization.load_der_public_key(
        public_key_bytes,
        backend=default_backend(),
    )

    chunk_size = (public_key.key_size // 8) - 11
    plain_bytes = plain_text.encode("utf-8")
    encrypted_chunks = []
    for index in range(0, len(plain_bytes), chunk_size):
        chunk = plain_bytes[index : index + chunk_size]
        encrypted_chunks.append(public_key.encrypt(chunk, padding.PKCS1v15()))

    encrypted_data = b"".join(encrypted_chunks)
    return base64.urlsafe_b64encode(encrypted_data).decode("utf-8").rstrip("=")


def rsa_decrypt(encrypted_text: str, private_key_str: str) -> str:
    encrypted_text += "=" * ((4 - len(encrypted_text) % 4) % 4)
    encrypted_bytes = base64.urlsafe_b64decode(encrypted_text)

    private_key_body = (
        private_key_str.replace("-----BEGIN RSA PRIVATE KEY-----", "")
        .replace("-----END RSA PRIVATE KEY-----", "")
        .replace("\n", "")
        .replace(" ", "")
    )
    private_key_bytes = base64.b64decode(private_key_body)
    private_key = serialization.load_der_private_key(
        private_key_bytes,
        password=None,
        backend=default_backend(),
    )

    chunk_size = private_key.key_size // 8
    decrypted_chunks = []
    for index in range(0, len(encrypted_bytes), chunk_size):
        chunk = encrypted_bytes[index : index + chunk_size]
        decrypted_chunks.append(private_key.decrypt(chunk, padding.PKCS1v15()))

    return b"".join(decrypted_chunks).decode("utf-8")


def generate_sign(sorted_params: OrderedDict[str, Any]) -> str:
    param_str = "&".join(f"{key}={value}" for key, value in sorted_params.items())
    return hashlib.md5(param_str.encode("utf-8")).hexdigest()


def build_signed_request_payload(business_data: dict[str, Any], config: OpenPlatformConfig) -> dict[str, Any]:
    business_json = json.dumps(business_data, separators=(",", ":"), ensure_ascii=False)
    encrypted_data = rsa_encrypt(business_json, config.public_key)
    timestamp = str(int(time.time()))

    sorted_params: OrderedDict[str, Any] = OrderedDict()
    sorted_params["appid"] = config.appid
    sorted_params["data"] = encrypted_data
    sorted_params["dataEncryptMethod"] = "rsa"
    sorted_params["key"] = config.sign_key
    sorted_params["signEncryptMethod"] = "md5"
    sorted_params["timestamp"] = timestamp

    return {
        "appid": config.appid,
        "dataEncryptMethod": "rsa",
        "signEncryptMethod": "md5",
        "timestamp": timestamp,
        "data": encrypted_data,
        "sign": generate_sign(sorted_params),
    }


def request_and_decrypt(
    api_path: str,
    business_data: dict[str, Any],
    config: Optional[OpenPlatformConfig] = None,
    session: Optional[requests.Session] = None,
) -> dict[str, Any]:
    config = config or OpenPlatformConfig.from_env()
    request_payload = build_signed_request_payload(business_data, config)
    url = f"{config.host}{api_path}"
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if config.auth_token:
        headers["Authorization"] = config.auth_token

    http = session or requests
    try:
        response = http.post(
            url,
            headers=headers,
            data=json.dumps(request_payload, separators=(",", ":")).encode("utf-8"),
            timeout=config.timeout,
        )
        response.raise_for_status()
        response_json = response.json()
    except requests.RequestException as exc:
        raise OpenPlatformError(f"OpenAPI request failed: {exc}") from exc
    except ValueError as exc:
        raise OpenPlatformError(f"OpenAPI returned non-JSON response: {response.text}") from exc

    encrypted_response_data = response_json.get("data")
    if not encrypted_response_data:
        raise OpenPlatformError(f"OpenAPI response missing encrypted data: {response_json}")

    try:
        decrypted_text = rsa_decrypt(encrypted_response_data, config.private_key)
        return json.loads(decrypted_text)
    except Exception as exc:
        raise OpenPlatformError(f"OpenAPI response decrypt/parse failed: {exc}") from exc


def build_member_info_business_data(account_id: str) -> dict[str, Any]:
    return {
        "accountId": account_id,
        "memberId": 0,
        "needLevelInfo": True,
        "memberSystemCode": "platform",
        "needSystemInfo": False,
        "needAccountInfo": True,
    }


def get_user_member_info(
    account_id: str,
    config: Optional[OpenPlatformConfig] = None,
    session: Optional[requests.Session] = None,
) -> dict[str, Any]:
    business_data = build_member_info_business_data(account_id)
    return request_and_decrypt(MEMBER_INFO_API_PATH, business_data, config=config, session=session)


def get_nested_value(data: dict[str, Any], path: tuple[str, ...], default: Any = None) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def sex_to_gender(sex: Any) -> str:
    gender_map = {
        1: "男",
        2: "女",
        "1": "男",
        "2": "女",
    }
    return gender_map.get(sex, "未知")


def get_gender_from_member_info(member_info_response: dict[str, Any]) -> str:
    sex = get_nested_value(member_info_response, ("data", "accountInfo", "sex"))
    return sex_to_gender(sex)


def get_gender_by_account_id(
    account_id: str,
    config: Optional[OpenPlatformConfig] = None,
    session: Optional[requests.Session] = None,
) -> str:
    member_info = get_user_member_info(account_id, config=config, session=session)
    return get_gender_from_member_info(member_info)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch user gender by accountId from Lefox OpenAPI.")
    parser.add_argument("account_id", help="Lefox accountId")
    parser.add_argument("--raw", action="store_true", help="Print decrypted member.info response JSON.")
    args = parser.parse_args()

    member_info = get_user_member_info(args.account_id)
    if args.raw:
        print(json.dumps(member_info, ensure_ascii=False, indent=2))
    else:
        print(get_gender_from_member_info(member_info))


if __name__ == "__main__":
    main()
