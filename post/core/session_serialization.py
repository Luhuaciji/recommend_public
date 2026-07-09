from __future__ import annotations

import dataclasses
import importlib
import json
from pathlib import Path
from typing import Any, Dict


_TYPE_KEY = "__session_type__"
_DATA_KEY = "data"
_SET_TYPE = "set"

_ALLOWED_DATACLASS_TYPES = {
    "core.gift.models:GiftSlot",
    "core.gift.models:ProductCategory",
    "core.gift.models:ProductCandidate",
    "core.gift.models:CategorySelectionResult",
    "core.gift.models:RouterDecision",
    "core.gift.models:GiftRecommendationState",
}


def _type_name(value: Any) -> str:
    return f"{value.__class__.__module__}:{value.__class__.__name__}"


def _resolve_dataclass(type_name: str):
    if type_name not in _ALLOWED_DATACLASS_TYPES:
        return None
    module_name, class_name = type_name.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def _encode(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, set):
        return {_TYPE_KEY: _SET_TYPE, _DATA_KEY: [_encode(item) for item in value]}
    if dataclasses.is_dataclass(value):
        return {
            _TYPE_KEY: _type_name(value),
            _DATA_KEY: {
                field.name: _encode(getattr(value, field.name))
                for field in dataclasses.fields(value)
            },
        }
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dict__"):
        return {
            str(key): _encode(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if not isinstance(value, dict):
        return value

    type_name = value.get(_TYPE_KEY)
    if type_name == _SET_TYPE:
        return set(_decode(item) for item in value.get(_DATA_KEY, []))

    cls = _resolve_dataclass(type_name) if isinstance(type_name, str) else None
    if cls is not None:
        data = value.get(_DATA_KEY, {})
        if not isinstance(data, dict):
            data = {}
        return cls(**{key: _decode(item) for key, item in data.items()})

    return {key: _decode(item) for key, item in value.items()}


def dumps_session(session: Dict[str, Any]) -> str:
    return json.dumps(_encode(session), ensure_ascii=False, separators=(",", ":"))


def loads_session(payload: str) -> Dict[str, Any]:
    data = _decode(json.loads(payload))
    if not isinstance(data, dict):
        return {}
    interrupted_ids = data.get("interrupted_ids")
    if isinstance(interrupted_ids, list):
        data["interrupted_ids"] = set(interrupted_ids)
    elif interrupted_ids is None:
        data["interrupted_ids"] = set()
    return data
