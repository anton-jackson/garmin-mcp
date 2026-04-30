"""JSON normalization helpers."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any


def normalize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return obj
