"""Training status MCP tools."""
from __future__ import annotations

from typing import Any

from ..format import normalize
from ..garmin import NeedsMFA, get_client


def _safe(fn):
    try:
        return fn()
    except NeedsMFA:
        return {"needs_mfa": True}


def _strip_vo2(obj: Any) -> Any:
    """Recursively drop any keys mentioning VO2 — they're noisy and the agent over-indexes on them."""
    if isinstance(obj, dict):
        return {k: _strip_vo2(v) for k, v in obj.items() if "vo2" not in k.lower()}
    if isinstance(obj, list):
        return [_strip_vo2(x) for x in obj]
    return obj


def register(mcp):
    @mcp.tool()
    def get_training_status(date_: str) -> dict[str, Any]:
        """Training status for a date (yyyy-mm-dd): acute load, chronic load, load ratio, optimal range, and training status label."""
        return _safe(lambda: _strip_vo2(normalize(get_client().get_training_status(date_))))
