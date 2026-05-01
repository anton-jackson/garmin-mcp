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


def register(mcp):
    @mcp.tool()
    def get_training_status(date_: str) -> dict[str, Any]:
        """Training status for a date (yyyy-mm-dd): acute load, chronic load, load ratio, optimal range, and training status label."""
        return _safe(lambda: normalize(get_client().get_training_status(date_)))
