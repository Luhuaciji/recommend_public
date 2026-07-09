"""
Aliyun AI response guard for model-generated plain text.

This module is intentionally standalone and is invoked by the recommendation
flow only after final visible response text has been generated.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


@dataclass
class GuardCheckResult:
    success: bool
    allowed: bool
    suggestion: str
    request_id: Optional[str]
    code: Optional[int]
    message: Optional[str]
    detail: Optional[List[Dict[str, Any]]]
    raw: Optional[Dict[str, Any]]
    error: Optional[str] = None


class AliyunResponseGuard:
    SERVICE = "response_security_check_pro"
    MAX_CONTENT_LENGTH = 2000

    def __init__(
        self,
        access_key_id: Optional[str] = None,
        access_key_secret: Optional[str] = None,
        region_id: Optional[str] = None,
        endpoint: Optional[str] = None,
        connect_timeout: Optional[int] = None,
        read_timeout: Optional[int] = None,
        enabled: Optional[bool] = None,
        fail_open: Optional[bool] = None,
        sdk_client: Optional[Any] = None,
        request_factory: Optional[Any] = None,
    ) -> None:
        self.enabled = enabled if enabled is not None else self._get_bool_env("ALIYUN_GUARD_ENABLE", True)
        self.fail_open = fail_open if fail_open is not None else self._get_bool_env("ALIYUN_GUARD_FAIL_OPEN", False)

        self.access_key_id = (
            access_key_id
            or os.getenv("ALIYUN_GUARD_ACCESS_KEY_ID")
            or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID")
        )
        self.access_key_secret = (
            access_key_secret
            or os.getenv("ALIYUN_GUARD_ACCESS_KEY_SECRET")
            or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
        )

        self.region_id = region_id or os.getenv("ALIYUN_GUARD_REGION_ID", "cn-shanghai")
        self.endpoint = endpoint or os.getenv("ALIYUN_GUARD_ENDPOINT", "green-cip.cn-shanghai.aliyuncs.com")
        self.connect_timeout = connect_timeout or self._get_int_env("ALIYUN_GUARD_CONNECT_TIMEOUT_MS", 3000)
        self.read_timeout = read_timeout or self._get_int_env("ALIYUN_GUARD_READ_TIMEOUT_MS", 10000)

        self._client = sdk_client
        self._request_factory = request_factory

    def check_response(self, content: str, data_id: Optional[str] = None) -> GuardCheckResult:
        data_id = data_id or str(uuid.uuid4())

        if not self.enabled:
            return GuardCheckResult(
                success=True,
                allowed=True,
                suggestion="disabled",
                request_id=None,
                code=None,
                message="Aliyun response guard disabled",
                detail=None,
                raw=None,
                error=None,
            )

        if not content or not content.strip():
            return self._error_result("empty content", respect_fail_open=False)

        if len(content) > self.MAX_CONTENT_LENGTH:
            return self._error_result(
                f"content length exceeds {self.MAX_CONTENT_LENGTH} characters",
                respect_fail_open=False,
            )

        content_meta = self._content_meta(content)

        try:
            client = self._get_client()
            request = self._build_request(content=content, data_id=data_id)
            response = client.multi_modal_guard(request)

            status_code = getattr(response, "status_code", None)
            if status_code != 200:
                result = self._error_result(
                    f"HTTP status code is not 200: {status_code}",
                    raw=self._safe_to_dict(response),
                )
                self._log_result(data_id, result, content_meta)
                return result

            body = getattr(response, "body", None)
            body_dict = self._safe_to_dict(body)
            code = self._to_int(self._get_attr_or_key(body, "code"))
            message = self._get_attr_or_key(body, "message")
            request_id = self._get_attr_or_key(body, "request_id") or self._get_attr_or_key(body, "RequestId")

            if code != 200:
                result = self._error_result(
                    f"Aliyun guard business code is not 200: {code}, message={message}",
                    request_id=request_id,
                    code=code,
                    message=message,
                    raw=body_dict,
                )
                self._log_result(data_id, result, content_meta)
                return result

            data = self._get_attr_or_key(body, "data") or self._get_attr_or_key(body, "Data")
            data_dict = self._safe_to_dict(data)
            suggestion = self._get_case_insensitive(data_dict, "Suggestion")
            detail = self._get_case_insensitive(data_dict, "Detail")

            if not suggestion:
                result = self._error_result(
                    "missing Data.Suggestion in Aliyun guard response",
                    request_id=request_id,
                    code=code,
                    message=message,
                    raw=body_dict,
                )
                self._log_result(data_id, result, content_meta)
                return result

            suggestion = str(suggestion).strip().lower()
            result = GuardCheckResult(
                success=True,
                allowed=self._is_allowed(suggestion),
                suggestion=suggestion,
                request_id=str(request_id) if request_id is not None else None,
                code=code,
                message=str(message) if message is not None else None,
                detail=detail if isinstance(detail, list) else None,
                raw=body_dict if isinstance(body_dict, dict) else {"body": body_dict},
                error=None,
            )
            self._log_result(data_id, result, content_meta)
            return result

        except Exception as exc:
            result = self._error_result(str(exc))
            logger.exception(
                "aliyun_response_guard_exception data_id=%s content_len=%s content_sha256=%s allowed=%s",
                data_id,
                content_meta["length"],
                content_meta["sha256"],
                result.allowed,
            )
            return result

    def _build_request(self, content: str, data_id: str) -> Any:
        service_parameters = {
            "content": content,
            "dataId": data_id,
        }
        request_factory = self._request_factory or self._load_request_factory()
        return request_factory(
            service=self.SERVICE,
            service_parameters=json.dumps(service_parameters, ensure_ascii=False),
        )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        if not self.access_key_id or not self.access_key_secret:
            raise RuntimeError("Aliyun AccessKey is not configured")

        try:
            from alibabacloud_green20220302.client import Client
            from alibabacloud_tea_openapi.models import Config
        except ImportError as exc:
            raise RuntimeError(
                "Aliyun Green SDK is not installed. Install alibabacloud_green20220302==3.2.4"
            ) from exc

        config = Config(
            access_key_id=self.access_key_id,
            access_key_secret=self.access_key_secret,
            region_id=self.region_id,
            endpoint=self.endpoint,
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
        )
        self._client = Client(config)
        return self._client

    @staticmethod
    def _load_request_factory() -> Any:
        try:
            from alibabacloud_green20220302 import models
        except ImportError as exc:
            raise RuntimeError(
                "Aliyun Green SDK is not installed. Install alibabacloud_green20220302==3.2.4"
            ) from exc
        return models.MultiModalGuardRequest

    @staticmethod
    def _is_allowed(suggestion: str) -> bool:
        return suggestion == "pass"

    def _error_result(
        self,
        error: str,
        request_id: Optional[str] = None,
        code: Optional[int] = None,
        message: Optional[str] = None,
        raw: Optional[Dict[str, Any]] = None,
        respect_fail_open: bool = True,
    ) -> GuardCheckResult:
        return GuardCheckResult(
            success=False,
            allowed=self.fail_open if respect_fail_open else False,
            suggestion="error",
            request_id=request_id,
            code=code,
            message=message,
            detail=None,
            raw=raw,
            error=error,
        )

    @staticmethod
    def _get_bool_env(name: str, default: bool) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _get_int_env(name: str, default: int) -> int:
        value = os.getenv(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            logger.warning("Invalid integer env var %s=%r, using default %s", name, value, default)
            return default

    @staticmethod
    def _get_case_insensitive(data: Any, key: str) -> Any:
        if not isinstance(data, dict):
            return None
        for item_key, value in data.items():
            if str(item_key).lower() == key.lower():
                return value
        return None

    @classmethod
    def _get_attr_or_key(cls, obj: Any, name: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return cls._get_case_insensitive(obj, name)
        return getattr(obj, name, None)

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _safe_to_dict(cls, obj: Any) -> Any:
        if obj is None:
            return None
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, list):
            return [cls._safe_to_dict(item) for item in obj]
        if isinstance(obj, dict):
            return {key: cls._safe_to_dict(value) for key, value in obj.items()}
        if hasattr(obj, "to_map"):
            return obj.to_map()
        if hasattr(obj, "__dict__"):
            return {
                key: cls._safe_to_dict(value)
                for key, value in obj.__dict__.items()
                if not key.startswith("_")
            }
        return str(obj)

    @staticmethod
    def _content_meta(content: str) -> Dict[str, Any]:
        return {
            "length": len(content or ""),
            "sha256": hashlib.sha256((content or "").encode("utf-8")).hexdigest(),
        }

    @staticmethod
    def _detail_summary(detail: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        summary = []
        for item in detail or []:
            if not isinstance(item, dict):
                continue
            labels = []
            for result in item.get("Result") or item.get("result") or []:
                if isinstance(result, dict):
                    label = result.get("Label") or result.get("label")
                    if label:
                        labels.append(label)
            summary.append(
                {
                    "type": item.get("Type") or item.get("type"),
                    "level": item.get("Level") or item.get("level"),
                    "labels": labels,
                }
            )
        return summary

    def _log_result(self, data_id: str, result: GuardCheckResult, content_meta: Dict[str, Any]) -> None:
        logger.info(
            "aliyun_response_guard_result data_id=%s request_id=%s suggestion=%s allowed=%s "
            "success=%s code=%s message=%s content_len=%s content_sha256=%s detail_summary=%s",
            data_id,
            result.request_id,
            result.suggestion,
            result.allowed,
            result.success,
            result.code,
            result.message,
            content_meta["length"],
            content_meta["sha256"],
            self._detail_summary(result.detail),
        )
